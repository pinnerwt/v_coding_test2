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

    async def run(self, goal: str) -> dict:
        tools = self.registry.to_openai_tools()
        for step_idx in range(self.max_steps):
            url = self._current_url()
            url_notes = self.notes.get(url) if self.notes else ""
            messages = build_messages(
                system=_SYSTEM,
                goal=goal,
                qa=list(self.qa),
                url_notes=url_notes,
                tape=self.tape,
                page_header=self._page_header(),
                replan_hint=None,
            )
            msg = await self.llm.chat(
                messages, tools=tools, tool_choice="auto", reasoning=False
            )
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self.trace.write(
                    {"type": "error", "payload": {"reason": "no tool call"}}
                )
                return {"status": "failed", "answer": "agent produced no tool call"}
            tc = tool_calls[0]
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            thought = args.pop("thought", "") if isinstance(args, dict) else ""
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
                self.trace.write(
                    {
                        "type": "done",
                        "payload": {"status": d.status, "answer": d.answer},
                    }
                )
                return {"status": d.status, "answer": d.answer}
            obs_str = obs if isinstance(obs, str) else json.dumps(obs)
            self.tape.append(
                {"thought": thought, "action": name, "args": args, "obs": obs_str}
            )
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
        self.trace.write(
            {"type": "done", "payload": {"status": "failed", "answer": "max steps"}}
        )
        return {"status": "failed", "answer": "max steps"}
