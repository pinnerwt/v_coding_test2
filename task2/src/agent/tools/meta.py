from __future__ import annotations

import asyncio

from agent.tools.registry import Tool


class LoopDone(Exception):
    def __init__(self, status: str, answer: str):
        super().__init__(f"done({status})")
        self.status = status
        self.answer = answer


class QuestionChannel:
    def __init__(self) -> None:
        self._pending: str | None = None
        self._fut: asyncio.Future | None = None

    def pending(self) -> str | None:
        return self._pending

    async def ask(self, question: str) -> str:
        loop = asyncio.get_event_loop()
        self._pending = question
        self._fut = loop.create_future()
        try:
            return await self._fut
        finally:
            self._pending = None
            self._fut = None

    def answer(self, text: str) -> None:
        if self._fut and not self._fut.done():
            self._fut.set_result(text)


def build_meta_tools(
    *,
    question_channel: QuestionChannel,
    reason_log: list[str] | None = None,
) -> dict:
    async def ask_user_question(question: str) -> str:
        ans = await question_channel.ask(question)
        return f"user said: {ans}"

    async def done(status: str, answer: str) -> str:
        raise LoopDone(status, answer)

    async def reason(text: str) -> str:
        if reason_log is not None:
            reason_log.append(text)
        return "noted"

    return {
        "ask_user_question": ask_user_question,
        "done": done,
        "reason": reason,
    }


def build_meta_tool_list(
    *,
    question_channel: QuestionChannel,
    reason_log: list[str] | None = None,
) -> list[Tool]:
    fns = build_meta_tools(
        question_channel=question_channel,
        reason_log=reason_log,
    )
    return [
        Tool(
            "reason",
            "Record a short thought you want to remember past the rolling action "
            "window. In-session only — does not persist.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "reason"],
            },
            fns["reason"],
        ),
        Tool(
            "ask_user_question",
            "Ask the user a clarifying question; loop blocks until answered.",
            {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["question", "reason"],
            },
            fns["ask_user_question"],
        ),
        Tool(
            "done",
            "Finish the task. status ∈ {success, failed, needs_user}.",
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["success", "failed", "needs_user"],
                    },
                    "answer": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["status", "answer", "reason"],
            },
            fns["done"],
        ),
    ]
