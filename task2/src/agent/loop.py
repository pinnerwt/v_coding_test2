from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from agent.context import build_messages
from agent.distill import distill_page_knowledge
from agent.force_done import coerce_done_via_llm
from agent.llm import LLMClient, ToolNameNotAllowed
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

_SYSTEM = """You are a web-browsing ReAct agent. Each turn, pick exactly one tool to call.

## The `reason` field is mandatory and load-bearing
Every tool call must include a `reason` argument: 1–3 short sentences capturing
(a) what the most recent observation showed (quote concrete evidence — a number, a
URL, an element label), (b) what this action will accomplish, and (c) why this
action over alternatives. Be concrete; vague reasons like "exploring" or "trying
again" are useless. Your `reason` is your only persistent memory beyond the last
3 observations — every word counts.

## Re-read `## Action history` before deciding
A `## Action history` section appears later in this system prompt with one line
per prior step (`step N | url | action_call | reason`). Re-read it each turn:
- If a prior reason already captured a fact ("found 59,513,990 monthly downloads"),
  that fact is still true — do not re-fetch it.
- If you have issued the same call 2–3 times with no progress, the strategy is
  not working — change approach (different page, different element, commit done).

## Use `## Recent observations (last 5)`
The user message includes a `## Recent observations (last 5)` section with the
raw text of the 5 most recent observations. Older observations are NOT preserved
verbatim — only your `reason` strings are. Write reasons that capture what you
saw, because future turns will not see the raw obs again.

## Forced reason checkpoint every 5 steps
Every 5th step (step 5, 10, 15, …) the loop locks the tool choice to `reason()`.
On those turns you MUST call `reason(text=..., reason=...)` with a 3-part
consolidation: (a) what concrete facts the last 5 obs established — including
any *secondary* findings that would make a good-enough fallback answer if the
primary goal stays unreachable, (b) what's still missing, (c) the next 1-3
actions you intend to try. The reason text is your durable scratchpad — write
it for your future self.

## Grounding
Element IDs come from `list_interactive` — never invent CSS selectors or guess
element IDs from prior knowledge.

## Stopping rules
- Call `done(status="success", answer="<answer>", evidence="<verbatim
  substring>", reason="...")` as soon as you have the answer. The `evidence`
  argument is REQUIRED on success — it must be a verbatim substring (≥10 chars)
  copied from a prior `read` or `read_grep` observation that contains your
  answer. Do not paraphrase, do not summarize, do not infer from prior
  knowledge: the loop checks `evidence` against the actual tape and rejects
  fabricated citations (success is downgraded to failed). If you cannot point
  to a substring in your reads that contains the answer, you do not yet have
  the answer — read more of the page first. Do not keep exploring after you
  have a grounded answer.
- Call `done(status="failed", answer="<best partial>", reason="...")` when you
  have exhausted reasonable approaches.
- Rendered-value caveat: if the goal asks for a value the page does not render
  exactly (e.g. asks for "total downloads" but the page only shows "Downloads
  last month"), commit `done(success, "<the rendered value> — <one-line caveat
  about what the page does/doesn't show>")` rather than searching indefinitely
  for the exact phrase.
- If a `[BLOCKED: …]` banner appears in an observation, commit
  `done(failed, "blocked by <wall>")` on the next turn — the site is unreachable
  from this browser.
- On the final allowed step you will only have the `done` tool available — make
  your best grounded commit then; do not stall.
"""


async def _maybe_live_interactive_payload(session, tape: list[dict]) -> str | None:
    """If `tape[-3:]` contains a `list_interactive` step, re-run the snapshot
    against the live page using the most-recent list_interactive's args, and
    return the JSON payload for the `## Interactive elements (live)` section.

    Returns None when list_interactive isn't recent, or when the live snapshot
    raises (the loop can still proceed without the section)."""
    last_li = None
    for step in tape[-3:]:
        if step.get("action") == "list_interactive":
            last_li = step
    if last_li is None:
        return None
    args = last_li.get("args") or {}
    offset = args.get("offset", 0)
    limit = args.get("limit", 50)
    try:
        entries = await session.snapshot(offset=offset, limit=limit)
    except Exception:
        return None
    return json.dumps(entries, ensure_ascii=False)


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
        trace: TraceWriter,
        browser,
        question_channel: QuestionChannel,
        max_steps: int = 50,
        send_transient=None,
        small_diff_threshold: int = 50,
        max_auto_advance_hops: int = 32,
        diff_inject_max_lines: int = 50,
        reason_log: list[str] | None = None,
        on_visit: Callable[[str], None] | None = None,
        tape: list[dict[str, Any]] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.notes = notes
        self.trace = trace
        self.browser = browser
        self.qc = question_channel
        self.max_steps = max_steps
        self.send_transient = send_transient
        self.tape: list[dict[str, Any]] = tape if tape is not None else []
        self.qa: list[tuple[str, str]] = []
        self.read_cache = OffsetCache()
        self.list_interactive_cache = OffsetCache()
        self._read_limit = 1600
        self._max_auto_advance_hops = max_auto_advance_hops
        self._hidden_tools: set[str] = set()
        self.global_cache = GlobalTextCache()
        self._small_diff_threshold = small_diff_threshold
        self._diff_inject_max_lines = diff_inject_max_lines
        self._read_grep_line_hashes: dict[str, int] = {}
        self._read_grep_dedup_streak: int = 0
        self._wall_streak: int = 0
        self._wall_kind: Wall | None = None
        self.reason_log: list[str] = reason_log if reason_log is not None else []
        self._on_visit = on_visit
        self._last_visit: str | None = None

    def _current_url(self) -> str:
        try:
            return self.browser.page.url
        except Exception:
            return ""

    async def _run_distill(self, *, status: str, answer: str) -> None:
        """Distill page-knowledge after a done() — both natural and forced.
        Per design: failure-mode page knowledge (Cloudflare walls, CAPTCHAs,
        dead-ends) is the most valuable output, so this MUST run on every
        force-done path too, not just the natural LoopDone handler."""
        await distill_page_knowledge(
            llm=self.llm,
            notes=self.notes,
            url=self._current_url(),
            goal=self._goal,
            status=status,
            answer=answer,
            tape=self.tape,
            reason_log=self.reason_log,
            trace=self.trace,
        )

    def _page_header(self) -> str:
        return f"URL={self._current_url()}"

    def _done_tool_schema(self) -> dict[str, Any]:
        for t in self.registry.to_openai_tools():
            if t["function"]["name"] == "done":
                return t
        raise RuntimeError("done tool not registered")

    async def run(self, goal: str) -> dict:
        self._goal = goal
        for step_idx in range(self.max_steps):
            issue_url = self._current_url()
            if step_idx == self.max_steps - 1:
                url = self._current_url()
                url_notes = self.notes.get(url) if self.notes else ""
                result = await coerce_done_via_llm(
                    llm=self.llm,
                    tape=self.tape,
                    goal=goal,
                    qa=list(self.qa),
                    url=url,
                    url_notes=url_notes,
                    page_header=self._page_header(),
                    trigger="max_steps",
                    n_no_progress=None,
                    done_tool_schema=self._done_tool_schema(),
                    reason_log=self.reason_log,
                )
                self.trace.write({"type": "done", "payload": result})
                await self._run_distill(status=result["status"], answer=result["answer"])
                return result
            if self._on_visit is not None:
                u = self._current_url()
                if u and u != self._last_visit:
                    self._on_visit(u)
                    self._last_visit = u
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
                    self._read_grep_line_hashes.clear()
                    self._read_grep_dedup_streak = 0
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

            tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)
            url = issue_url
            url_notes = self.notes.get(url) if self.notes else ""
            interactive_elements = await _maybe_live_interactive_payload(self.browser, self.tape)
            messages = build_messages(
                system=_SYSTEM,
                goal=goal,
                qa=list(self.qa),
                url_notes=url_notes,
                tape=self.tape,
                page_header=self._page_header(),
                replan_hint=None,
                page_diff=diff_block,
                wall_banner=wall_banner,
                reason_log=self.reason_log,
                interactive_elements=interactive_elements,
            )
            if step_idx > 0 and step_idx % 5 == 0:
                tool_choice: Any = {"type": "function", "function": {"name": "reason"}}
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Checkpoint (step {step_idx}): you have taken 5 steps "
                            "since the last forced reason. Call reason() now to "
                            "consolidate — (a) facts the last 5 obs established, "
                            "including any secondary findings that would make a "
                            "good-enough fallback answer if the primary goal stays "
                            "unreachable, (b) what's still missing, (c) the next "
                            "1-3 actions you'll try."
                        ),
                    }
                )
            else:
                tool_choice = "required"
            if self.send_transient is not None:
                await self.send_transient({"type": "llm_call_start"})
            try:
                msg, usage = await self.llm.chat(
                    messages, tools=tools, tool_choice=tool_choice, reasoning=False
                )
            except ToolNameNotAllowed as e:
                obs = (
                    f"tool {e.name!r} is not available right now (masked or "
                    f"unknown). Pick from: {sorted(e.allowed)!r}."
                )
                self.tape.append(
                    {
                        "reason": "",
                        "action": e.name,
                        "args": {},
                        "obs": obs,
                        "url": issue_url,
                    }
                )
                self.trace.write(
                    {
                        "type": "step",
                        "payload": {
                            "n": step_idx,
                            "reason": "",
                            "action": e.name,
                            "args": {},
                            "obs": obs,
                            "url": issue_url,
                        },
                    }
                )
                continue
            if usage is not None:
                self.trace.write({"type": "usage", "payload": usage})
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self.trace.write({"type": "error", "payload": {"reason": "no tool call"}})
                return {"status": "failed", "answer": "agent produced no tool call"}
            tc = tool_calls[0]
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            reason = args.pop("reason", "") if isinstance(args, dict) else ""

            if not reason:
                obs = (
                    f"ERROR: tool {name!r} was NOT executed because the mandatory "
                    "`reason` field was missing. The reason field is mandatory: 1-3 "
                    "sentences capturing what you just observed and why you chose this "
                    "action. Retry with reason."
                )
                self.tape.append(
                    {
                        "action": name,
                        "args": args,
                        "reason": "",
                        "obs": obs,
                        "url": issue_url,
                    }
                )
                self.trace.write(
                    {
                        "type": "step",
                        "payload": {
                            "n": step_idx,
                            "action": name,
                            "args": args,
                            "reason": "",
                            "obs": obs,
                            "url": issue_url,
                        },
                    }
                )
                continue

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
                    self.tape.append(
                        {
                            "reason": reason,
                            "action": name,
                            "args": args,
                            "obs": obs,
                            "url": issue_url,
                        }
                    )
                    self.trace.write(
                        {
                            "type": "step",
                            "payload": {
                                "n": step_idx,
                                "reason": reason,
                                "action": name,
                                "args": args,
                                "obs": obs,
                                "url": issue_url,
                            },
                        }
                    )
                    continue
                args = {**args, "offset": plan.served_offset}
                self.read_cache.record(offset=plan.served_offset, served=plan.served_text)
                if plan.advanced_from is not None:
                    auto_advance_prefix = (
                        f"[auto-advanced {plan.advanced_from}→{plan.served_offset}: "
                        f"{plan.advanced_from} unchanged since prior read]"
                    )

            try:
                if obs_override is not None:
                    obs = obs_override
                elif grounding_block is not None:
                    obs = grounding_block
                else:
                    obs = await self.registry.call(name, args)
                    if auto_advance_prefix is not None and isinstance(obs, str):
                        obs = f"{auto_advance_prefix} {obs}"
                if name == "read_grep" and isinstance(obs, str):
                    raw_lines = obs.splitlines()
                    kept: list[str] = []
                    hidden = 0
                    has_new = False
                    for ln in raw_lines:
                        key = ln.strip()
                        if not key:
                            kept.append(ln)
                            continue
                        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
                        if h in self._read_grep_line_hashes:
                            hidden += 1
                            continue
                        self._read_grep_line_hashes[h] = step_idx
                        kept.append(ln)
                        has_new = True
                    if not has_new:
                        obs = (
                            f"DUPLICATE: read_grep returned {hidden} lines, all "
                            "previously shown. Page text has not changed since. "
                            "Try a different pattern, a different offset, or call done()."
                        )
                        self._read_grep_dedup_streak += 1
                        if self._read_grep_dedup_streak >= 2:
                            self._hidden_tools.add("read_grep")
                            obs = (
                                "read_grep is no longer available this turn — it "
                                "returned only previously-shown lines 3× in a row. "
                                "Use 'read' with a different offset, list_interactive, "
                                "or done()."
                            )
                    else:
                        if hidden > 0:
                            obs = "\n".join(kept) + (
                                f"\n  ({hidden} lines hidden — already shown in "
                                "prior read_grep calls)"
                            )
                        else:
                            obs = "\n".join(kept)
                        self._read_grep_dedup_streak = 0
                elif name != "read_grep":
                    self._read_grep_dedup_streak = 0
            except LoopDone as d:
                self.trace.write(
                    {
                        "type": "step",
                        "payload": {
                            "n": step_idx,
                            "reason": reason,
                            "action": name,
                            "args": args,
                            "obs": f"done({d.status})",
                            "url": issue_url,
                        },
                    }
                )
                self.trace.write(
                    {
                        "type": "done",
                        "payload": {"status": d.status, "answer": d.answer},
                    }
                )
                await self._run_distill(status=d.status, answer=d.answer)
                return {"status": d.status, "answer": d.answer}
            except Exception as e:
                obs = f"ERROR: {e}"

            obs_str = obs if isinstance(obs, str) else json.dumps(obs)
            self.tape.append(
                {
                    "reason": reason,
                    "action": name,
                    "args": args,
                    "obs": obs_str,
                    "url": issue_url,
                }
            )
            self.trace.write(
                {
                    "type": "step",
                    "payload": {
                        "n": step_idx,
                        "reason": reason,
                        "action": name,
                        "args": args,
                        "obs": obs_str,
                        "url": issue_url,
                    },
                }
            )

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

        raise RuntimeError("ReactLoop: unreachable — max_steps short-circuit must return")
