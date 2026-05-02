from __future__ import annotations

import json
from typing import Any

from agent.context import build_messages
from agent.llm import LLMClient
from agent.tools.meta import LoopDone, QuestionChannel
from agent.tools.registry import ToolRegistry
from agent.trace import TraceWriter

_SYSTEM = (
    "You are a web-browsing ReAct agent. Each turn, pick exactly one tool to call. "
    "Always include a brief `thought` argument explaining your choice. Element IDs "
    "come from list_interactive — never invent CSS selectors. Call done(status, "
    "answer) when the user goal is satisfied or impossible."
)

_REPLAN_HINT = (
    "REPLAN: You repeated the same action 3 times with the same observation. "
    "Re-read URL notes and pick a DIFFERENT action this turn — different element, "
    "navigate elsewhere, call note() to record the failure, or ask_user_question."
)


def _step_key(step: dict) -> tuple:
    return (
        step.get("action"),
        json.dumps(step.get("args", {}), sort_keys=True),
        step.get("obs"),
    )


def _action_key(name: str, args: dict) -> tuple:
    return (name, json.dumps(args or {}, sort_keys=True))


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
    ):
        self.llm = llm
        self.registry = registry
        self.notes = notes
        self.summarizer = summarizer
        self.trace = trace
        self.browser = browser
        self.qc = question_channel
        self.max_steps = max_steps
        self.tape: list[dict[str, Any]] = []
        self.qa: list[tuple[str, str]] = []

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

    def _last_action_args(self) -> tuple | None:
        if not self.tape:
            return None
        s = self.tape[-1]
        return (s["action"], json.dumps(s.get("args", {}), sort_keys=True))

    async def run(self, goal: str) -> dict:
        tools = self.registry.to_openai_tools()
        state = "none"  # none | hinted | asked | giveup
        for step_idx in range(self.max_steps):
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
            )
            msg, _ = await self.llm.chat(messages, tools=tools, tool_choice="auto", reasoning=False)
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

            try:
                obs = await self.registry.call(name, args)
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
            self.tape.append({"thought": thought, "action": name, "args": args, "obs": obs_str})
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

            if self.summarizer is not None:
                trigger = None
                if name == "goto":
                    trigger = "goto"
                elif obs_str.startswith("ERROR:"):
                    trigger = "error"
                if trigger is not None and url:
                    existing = self.notes.get(url) if self.notes else ""
                    await self.summarizer.maybe_summarize(
                        trigger=trigger,
                        prior_url=url,
                        tape_slice=self.tape[-5:],
                        existing_notes=existing,
                    )

            if state == "none" and self._last_three_match():
                state = "hinted"

        self.trace.write({"type": "done", "payload": {"status": "failed", "answer": "max steps"}})
        return {"status": "failed", "answer": "max steps"}
