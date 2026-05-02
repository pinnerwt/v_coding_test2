from __future__ import annotations

import json
from typing import Any, Literal

from agent.context import build_messages
from agent.llm import LLMClient

Trigger = Literal["max_steps", "no_progress", "asked_after_clarification"]

_PLACEHOLDERS: dict[Trigger, str] = {
    "max_steps": "max steps",
    "no_progress": "stuck: no novel observation for {n} consecutive steps",
    "asked_after_clarification": "stuck after user clarification",
}


def _placeholder(trigger: Trigger, n_no_progress: int | None) -> str:
    template = _PLACEHOLDERS[trigger]
    if trigger == "no_progress":
        return template.format(n=n_no_progress if n_no_progress is not None else "?")
    return template


async def coerce_done_via_llm(
    *,
    llm: LLMClient,
    tape: list[dict[str, Any]],
    goal: str,
    qa: list[tuple[str, str]],
    url: str,
    url_notes: str,
    page_header: str,
    trigger: Trigger,
    n_no_progress: int | None,
    done_tool_schema: dict[str, Any],
) -> dict[str, Any]:
    """One-shot LLM call constrained to emit done(...). Returns
    {"status": ..., "answer": ...}. On transport-level deviation
    (empty tool_calls, wrong tool name, malformed args) returns a
    trigger-specific placeholder. Network errors propagate."""
    messages = build_messages(
        system="",
        goal=goal,
        qa=qa,
        url_notes=url_notes,
        tape=tape,
        page_header=page_header,
        replan_hint=None,
    )
    msg, _usage = await llm.chat(
        messages,
        tools=[done_tool_schema],
        tool_choice={"type": "function", "function": {"name": "done"}},
        reasoning=False,
    )
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    tc = tool_calls[0]
    if tc.get("function", {}).get("name") != "done":
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    try:
        args = json.loads(tc["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    return {
        "status": args.get("status", "failed"),
        "answer": args.get("answer", _placeholder(trigger, n_no_progress)),
    }
