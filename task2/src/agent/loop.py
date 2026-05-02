from __future__ import annotations

import json
from typing import Any

from agent.context import NOVELTY_WINDOW, _obs_fingerprint, build_messages
from agent.llm import LLMClient
from agent.page_diff import (
    GlobalTextCache,
    OffsetCache,
    format_small_diff,
    plan_read,
    should_inject_diff,
)
from agent.tools.meta import LoopDone, QuestionChannel
from agent.tools.registry import ToolRegistry
from agent.trace import TraceWriter
from agent.wall_detect import Wall, detect_wall

_SYSTEM = (
    "You are a web-browsing ReAct agent. Each turn, pick exactly one tool to call. "
    "Always include a brief `thought` argument explaining your choice. Element IDs "
    "come from list_interactive — never invent CSS selectors. Call done(status, "
    "answer) when the user goal is satisfied or impossible. "
    "If the goal asks for a value the page does not render exactly (e.g. asks for "
    "'total downloads' but the page only shows 'Downloads last month'), commit "
    'done(success, "<the rendered value> — <one-line caveat about what the page '
    "does/doesn't show>\") rather than searching indefinitely for the exact phrase. "
    'If a [BLOCKED: …] banner appears, commit done(failed, "blocked by <wall>") '
    "on the next turn — the site is unreachable from this browser. "
    "On the final allowed step you will only have the done tool — make your best "
    "grounded commit then; do not stall."
)

_REPLAN_HINT = (
    "REPLAN: You repeated the same action 3 times with the same observation. "
    "Re-read URL notes and pick a DIFFERENT action this turn — different element, "
    "navigate elsewhere, call note() to record the failure, or ask_user_question."
)

# Force a done(failed) when the agent produces this many consecutive
# observations whose fingerprints were already seen earlier in the tape.
# Catches arbitrary-length cycles that the hint state machine misses
# because a single varied action keeps resetting it. canirun.ai bench
# case 113 burned 50 steps because no such ceiling existed.
NO_PROGRESS_GIVEUP = 12


def _step_key(step: dict) -> tuple:
    return (
        step.get("action"),
        json.dumps(step.get("args", {}), sort_keys=True),
        step.get("obs"),
    )


def _action_key(name: str, args: dict) -> tuple:
    return (name, json.dumps(args or {}, sort_keys=True))


def _check_read_grep_grounding(pattern: str, goal: str, last_read_obs: str | None) -> str | None:
    """Block read_grep patterns that came from model prior knowledge rather
    than observed page content. Pattern is allowed iff it appears (case-
    insensitive) in the goal, or in the most recent `read` obs.

    Returns None when allowed, or an error string suitable for synthetic obs.
    """
    if not pattern:
        return None
    p = pattern.lower()
    if p in (goal or "").lower():
        return None
    if last_read_obs is not None and p in last_read_obs.lower():
        return None
    return (
        f"ERROR: read_grep({pattern!r}) — pattern not present in the goal or in "
        "the most recent read. Do not search for values you have only inferred. "
        "If you want a UI element, use list_interactive. If you want body text, "
        "use read first to surface terms, then grep on something you saw."
    )


def _last_read_obs(tape: list[dict]) -> str | None:
    for step in reversed(tape):
        if step.get("action") == "read":
            obs = step.get("obs")
            if isinstance(obs, str):
                return obs
    return None


class ReactLoop:
    def __init__(
        self,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        notes,
        summarizer,
        trace: TraceWriter,
        browser,
        question_channel: QuestionChannel,
        max_steps: int = 50,
        send_transient=None,
        small_diff_threshold: int = 500,
        max_auto_advance_hops: int = 32,
        diff_inject_max_lines: int = 10,
    ):
        self.llm = llm
        self.registry = registry
        self.notes = notes
        self.summarizer = summarizer
        self.trace = trace
        self.browser = browser
        self.qc = question_channel
        self.max_steps = max_steps
        self.send_transient = send_transient
        self.tape: list[dict[str, Any]] = []
        self.qa: list[tuple[str, str]] = []
        self.no_progress_streak = 0
        self.read_cache = OffsetCache()
        self.list_interactive_cache = OffsetCache()
        self._read_limit = 1600
        self._max_auto_advance_hops = max_auto_advance_hops
        self._hidden_tools: set[str] = set()
        self.global_cache = GlobalTextCache()
        self._small_diff_threshold = small_diff_threshold
        self._diff_inject_max_lines = diff_inject_max_lines
        self._read_grep_seen: dict[str, int] = {}
        self._wall_streak: int = 0
        self._wall_kind: Wall | None = None

    def _current_url(self) -> str:
        try:
            return self.browser.page.url
        except Exception:
            return ""

    def _page_header(self) -> str:
        return f"URL={self._current_url()}"

    def _last_three_match(self) -> bool:
        if len(self.tape) < 3:
            return False
        return _step_key(self.tape[-1]) == _step_key(self.tape[-2]) == _step_key(self.tape[-3])

    def _no_progress(self) -> bool:
        """True when the last NOVELTY_WINDOW observations have all been seen
        earlier in the tape — catches arbitrary-length cycles that
        _last_three_match misses (e.g. click→list→read→escape→click→…)."""
        if len(self.tape) < NOVELTY_WINDOW + 1:
            return False
        earlier = self.tape[:-NOVELTY_WINDOW]
        recent = self.tape[-NOVELTY_WINDOW:]
        earlier_fps = {_obs_fingerprint(s.get("obs", "")) for s in earlier}
        return all(_obs_fingerprint(s.get("obs", "")) in earlier_fps for s in recent)

    def _last_action_args(self) -> tuple | None:
        if not self.tape:
            return None
        s = self.tape[-1]
        return (s["action"], json.dumps(s.get("args", {}), sort_keys=True))

    async def run(self, goal: str) -> dict:
        state = "none"  # none | hinted | asked | giveup
        for step_idx in range(self.max_steps):
            try:
                current_text = await self.browser.page.evaluate("document.body.innerText")
            except Exception:
                current_text = ""

            prev_text = self.global_cache.previous()
            diff_block: str | None = None
            if prev_text is not None:
                if should_inject_diff(
                    previous=prev_text,
                    current=current_text,
                    threshold=self._small_diff_threshold,
                ):
                    diff_block = format_small_diff(
                        previous=prev_text,
                        current=current_text,
                        max_lines=self._diff_inject_max_lines,
                    )
                if prev_text != current_text:
                    # Page mutated → invalidate offset caches and re-expose hidden tools.
                    self.read_cache.clear()
                    self.list_interactive_cache.clear()
                    self._hidden_tools.clear()
                    self._read_grep_seen.clear()
                # If the previous step was press_key and the page didn't change, hide it.
                if (
                    self.tape
                    and self.tape[-1]["action"] == "press_key"
                    and prev_text == current_text
                ):
                    self._hidden_tools.add("press_key")
            self.global_cache.update(current_text)

            wall = detect_wall(text=current_text, url=self._current_url())
            if wall is not None:
                if self._wall_kind == wall:
                    self._wall_streak += 1
                else:
                    self._wall_kind = wall
                    self._wall_streak = 1
            else:
                self._wall_kind = None
                self._wall_streak = 0

            wall_banner: str | None = None
            if self._wall_streak >= 2 and self._wall_kind is not None:
                wall_banner = (
                    f"[BLOCKED: {self._wall_kind.value} wall — this site is unreachable "
                    f"from this browser ({self._wall_streak} consecutive turns on the "
                    f'interstitial). Commit done(failed, "blocked by '
                    f'{self._wall_kind.value}") instead of retrying navigation.]'
                )

            is_final_step = step_idx == self.max_steps - 1
            if is_final_step:
                tools = self.registry.to_openai_tools_filtered(
                    exclude={n for n in self.registry.names() if n != "done"}
                )
            else:
                tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)
            url = self._current_url()
            url_notes = self.notes.get(url) if self.notes else ""
            replan_hint = _REPLAN_HINT if state == "hinted" else None
            messages = build_messages(
                system=_SYSTEM,
                goal=goal,
                qa=list(self.qa),
                url_notes=url_notes,
                tape=self.tape,
                page_header=self._page_header(),
                replan_hint=replan_hint,
                page_diff=diff_block,
                wall_banner=wall_banner,
            )
            if self.send_transient is not None:
                await self.send_transient({"type": "llm_call_start"})
            msg, usage = await self.llm.chat(
                messages, tools=tools, tool_choice="auto", reasoning=False
            )
            if usage is not None:
                self.trace.write({"type": "usage", "payload": usage})
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self.trace.write({"type": "error", "payload": {"reason": "no tool call"}})
                return {"status": "failed", "answer": "agent produced no tool call"}
            tc = tool_calls[0]
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            thought = args.pop("thought", "") if isinstance(args, dict) else ""

            last_aa = self._last_action_args()
            new_aa = _action_key(name, args)
            if state == "hinted":
                if last_aa is not None and new_aa == last_aa:
                    name = "ask_user_question"
                    args = {
                        "question": (
                            f"I'm stuck on {self._current_url()}: same action keeps "
                            "yielding the same result. What should I try?"
                        )
                    }
                    state = "asked"
                else:
                    state = "none"
            elif state == "asked" and last_aa is not None and new_aa == last_aa:
                name = "done"
                args = {
                    "status": "failed",
                    "answer": "stuck after user clarification",
                }
                state = "giveup"

            read_grep_synthetic: str | None = None
            if name == "read_grep" and isinstance(args, dict):
                pat = (args.get("pattern", "") or "").lower()
                if pat and pat in self._read_grep_seen:
                    prior_step = self._read_grep_seen[pat]
                    read_grep_synthetic = (
                        f'(pattern "{args.get("pattern", "")}" already searched at '
                        f"step {prior_step}, no new matches.)"
                    )

            grounding_block: str | None = None
            if name == "read_grep" and isinstance(args, dict):
                grounding_block = _check_read_grep_grounding(
                    args.get("pattern", ""), goal, _last_read_obs(self.tape)
                )

            obs_override: str | None = None
            if name == "list_interactive" and isinstance(args, dict):
                requested_offset = int(args.get("offset", 0) or 0)
                limit = int(args.get("limit", 50) or 50)

                async def _safe_call_li(off: int, _name=name, _args=args) -> str:
                    try:
                        res = await self.registry.call(_name, {**_args, "offset": off})
                    except Exception as e:
                        return f"ERROR: {e}"
                    return res if isinstance(res, str) else json.dumps(res)

                served_offset = requested_offset
                served_str = await _safe_call_li(served_offset)
                hops = 0
                exhausted = False
                if served_str.strip() == "[]":
                    exhausted = True
                elif not served_str.startswith("ERROR:"):
                    while self.list_interactive_cache.was_served(
                        offset=served_offset, candidate=served_str
                    ):
                        hops += 1
                        if hops >= self._max_auto_advance_hops:
                            exhausted = True
                            break
                        served_offset += limit
                        served_str = await _safe_call_li(served_offset)
                        if served_str.startswith("ERROR:"):
                            break
                        if served_str.strip() == "[]":
                            exhausted = True
                            break
                if served_str.startswith("ERROR:"):
                    obs_override = served_str
                    args = {**args, "offset": served_offset}
                elif exhausted:
                    self._hidden_tools.add("list_interactive")
                    obs_override = (
                        f"(end of interactive list; tried offsets up to {served_offset} "
                        f"with no new entries). Try done() or click an existing id."
                    )
                    args = {**args, "offset": served_offset}
                else:
                    self.list_interactive_cache.record(offset=served_offset, served=served_str)
                    if served_offset != requested_offset:
                        served_str = (
                            f"[auto-advanced {requested_offset}→{served_offset}: "
                            f"{requested_offset} unchanged since prior list_interactive] "
                            f"{served_str}"
                        )
                    obs_override = served_str
                    args = {**args, "offset": served_offset}

            auto_advance_prefix: str | None = None
            if name == "read" and isinstance(args, dict):
                requested_offset = int(args.get("offset", 0) or 0)
                try:
                    text = await self.browser.page.evaluate("document.body.innerText")
                except Exception:
                    text = ""
                plan = plan_read(
                    text=text,
                    requested_offset=requested_offset,
                    cache=self.read_cache,
                    read_limit=self._read_limit,
                    max_hops=self._max_auto_advance_hops,
                )
                if plan.exhausted:
                    self._hidden_tools.add("read")
                    obs = (
                        f"(end of page; tried offsets up to {plan.served_offset}, "
                        f"page length {len(text)}). Try read_grep or done()."
                    )
                    new_fp = _obs_fingerprint(obs)
                    earlier_fps = {_obs_fingerprint(s.get("obs", "")) for s in self.tape}
                    self.tape.append(
                        {
                            "thought": thought,
                            "action": name,
                            "args": args,
                            "obs": obs,
                        }
                    )
                    if new_fp in earlier_fps:
                        self.no_progress_streak += 1
                    else:
                        self.no_progress_streak = 0
                    self.trace.write(
                        {
                            "type": "step",
                            "payload": {
                                "n": step_idx,
                                "thought": thought,
                                "action": name,
                                "args": args,
                                "obs": obs,
                            },
                        }
                    )
                    if self.no_progress_streak >= NO_PROGRESS_GIVEUP:
                        answer = (
                            f"stuck: no novel observation for {self.no_progress_streak} "
                            "consecutive steps"
                        )
                        self.trace.write(
                            {
                                "type": "done",
                                "payload": {"status": "failed", "answer": answer},
                            }
                        )
                        return {"status": "failed", "answer": answer}
                    continue
                args = {**args, "offset": plan.served_offset}
                self.read_cache.record(offset=plan.served_offset, served=plan.served_text)
                if plan.advanced_from is not None:
                    auto_advance_prefix = (
                        f"[auto-advanced {plan.advanced_from}→{plan.served_offset}: "
                        f"{plan.advanced_from} unchanged since prior read]"
                    )

            try:
                if read_grep_synthetic is not None:
                    obs = read_grep_synthetic
                elif obs_override is not None:
                    obs = obs_override
                elif grounding_block is not None:
                    obs = grounding_block
                else:
                    obs = await self.registry.call(name, args)
                    if auto_advance_prefix is not None and isinstance(obs, str):
                        obs = f"{auto_advance_prefix} {obs}"
                if (
                    name == "read_grep"
                    and read_grep_synthetic is None
                    and isinstance(obs, str)
                    and not obs.startswith("NOT FOUND:")
                ):
                    pat = (args.get("pattern", "") or "").lower()
                    if pat:
                        self._read_grep_seen[pat] = step_idx
            except LoopDone as d:
                self.trace.write(
                    {
                        "type": "step",
                        "payload": {
                            "n": step_idx,
                            "thought": thought,
                            "action": name,
                            "args": args,
                            "obs": f"done({d.status})",
                        },
                    }
                )
                if d.status == "failed" and self.summarizer is not None and url:
                    existing = self.notes.get(url) if self.notes else ""
                    await self.summarizer.maybe_summarize(
                        trigger="failed",
                        prior_url=url,
                        tape_slice=self.tape[-5:],
                        existing_notes=existing,
                    )
                self.trace.write(
                    {
                        "type": "done",
                        "payload": {"status": d.status, "answer": d.answer},
                    }
                )
                return {"status": d.status, "answer": d.answer}
            except Exception as e:
                obs = f"ERROR: {e}"

            obs_str = obs if isinstance(obs, str) else json.dumps(obs)
            new_fp = _obs_fingerprint(obs_str)
            earlier_fps = {_obs_fingerprint(s.get("obs", "")) for s in self.tape}
            self.tape.append({"thought": thought, "action": name, "args": args, "obs": obs_str})
            if new_fp in earlier_fps:
                self.no_progress_streak += 1
            else:
                self.no_progress_streak = 0
            self.trace.write(
                {
                    "type": "step",
                    "payload": {
                        "n": step_idx,
                        "thought": thought,
                        "action": name,
                        "args": args,
                        "obs": obs_str,
                    },
                }
            )

            if self.no_progress_streak >= NO_PROGRESS_GIVEUP:
                answer = (
                    f"stuck: no novel observation for {self.no_progress_streak} consecutive steps"
                )
                self.trace.write(
                    {"type": "done", "payload": {"status": "failed", "answer": answer}}
                )
                return {"status": "failed", "answer": answer}

            if name == "goto" and obs_str.startswith("ERROR: blocked goto"):
                self.trace.write(
                    {
                        "type": "goto_blocked",
                        "payload": {
                            "url": args.get("url", ""),
                            "reason": "not in observation allowlist",
                        },
                    }
                )

            if self.summarizer is not None:
                trigger = None
                if name == "goto":
                    trigger = "goto"
                elif obs_str.startswith("ERROR:"):
                    trigger = "error"
                if trigger is not None and url:
                    existing = self.notes.get(url) if self.notes else ""
                    try:
                        await self.summarizer.maybe_summarize(
                            trigger=trigger,
                            prior_url=url,
                            tape_slice=self.tape[-5:],
                            existing_notes=existing,
                        )
                    except Exception as e:
                        # Summarizer is best-effort URL-note generation; a transient
                        # LLM timeout or network blip must not abort the agent loop.
                        self.trace.write(
                            {"type": "summarizer_error", "payload": {"error": str(e)[:200]}}
                        )

            if state == "none" and (self._last_three_match() or self._no_progress()):
                state = "hinted"

        self.trace.write({"type": "done", "payload": {"status": "failed", "answer": "max steps"}})
        return {"status": "failed", "answer": "max steps"}
