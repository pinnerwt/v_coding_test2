from __future__ import annotations

import asyncio
import re

from agent.tools.registry import Tool


class LoopDone(Exception):
    def __init__(self, status: str, answer: str, evidence: str = ""):
        super().__init__(f"done({status})")
        self.status = status
        self.answer = answer
        self.evidence = evidence


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


_BRACKETS_RE = re.compile(r"[()\[\]{}]")


def _normalize(s: str) -> str:
    # Strip () [] {} so a human-formatted answer like "Python (99.9%)" can
    # still be substring-matched against unparenthesized page text. Other
    # punctuation (., %, ,) is preserved so percentages and decimals remain
    # intact on both sides of the comparison.
    s = _BRACKETS_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def build_meta_tools(
    *,
    question_channel: QuestionChannel,
    reason_log: list[str] | None = None,
    tape: list[dict] | None = None,
) -> dict:
    async def ask_user_question(question: str) -> str:
        ans = await question_channel.ask(question)
        return f"user said: {ans}"

    async def done(status: str, answer: str, evidence: str = "") -> str:
        # Validation only runs when a tape has been wired (production loop).
        # Standalone construction (e.g. legacy tests) skips validation and
        # behaves as a plain terminator.
        if tape is None:
            raise LoopDone(status, answer, evidence)
        if status == "success":
            if not evidence:
                raise ValueError(
                    "done() rejected — status='success' requires a non-empty "
                    "evidence string. Cite a substring of page text you read."
                )
            if len(evidence) < 10:
                raise ValueError(
                    "done() rejected — evidence must be at least 10 characters "
                    "to avoid trivial matches. Quote a longer surrounding span."
                )
            ev_norm = _normalize(evidence)
            ans_norm = _normalize(answer)
            haystack = " ".join(
                _normalize(str(s.get("obs", ""))) for s in (tape or []) if isinstance(s, dict)
            )
            if ev_norm not in haystack:
                raise ValueError(
                    f"done() rejected — evidence {evidence!r} not found in any "
                    "prior observation. Cite text from a prior read/read_grep "
                    "obs, or call done(status='failed', evidence='<short reason>')."
                )
            if ans_norm not in ev_norm:
                raise ValueError(
                    f"done() rejected — answer {answer!r} not contained in "
                    f"evidence {evidence!r}. Cite text that includes the answer."
                )
        elif status == "failed" and evidence:
            ev_norm = _normalize(evidence)
            haystack = " ".join(
                _normalize(str(s.get("obs", ""))) for s in (tape or []) if isinstance(s, dict)
            )
            if ev_norm not in haystack:
                raise ValueError(
                    f"done() rejected — evidence {evidence!r} not found in any prior observation."
                )
        raise LoopDone(status, answer, evidence)

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
    tape: list[dict] | None = None,
) -> list[Tool]:
    fns = build_meta_tools(
        question_channel=question_channel,
        reason_log=reason_log,
        tape=tape,
    )
    return [
        Tool(
            "reason",
            "Record a short note you want to remember past the rolling action "
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
            "Finish the task. status ∈ {success, failed, needs_user}. evidence "
            "MUST be a substring of a prior observation when status='success' "
            "(and must contain the answer); optional but validated when "
            "status='failed'.",
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["success", "failed", "needs_user"],
                    },
                    "answer": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["status", "answer", "reason"],
            },
            fns["done"],
        ),
    ]
