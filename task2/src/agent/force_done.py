from __future__ import annotations

import json
from typing import Any, Literal

from agent.context import build_messages
from agent.llm import LLMClient, ToolNameNotAllowed
from agent.tools.meta import _normalize

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


_FORCE_DONE_PROMPTS: dict[Trigger, str] = {
    "max_steps": (
        "This is your final allowed step. Commit done() now with the best "
        "answer your prior reads support. If your reads contained the answer, "
        'use done(success, "<answer>"); otherwise done(failed, "<one-line '
        'reason>").'
    ),
    "no_progress": (
        "You have produced no novel observation for {n} consecutive steps. "
        "Either the goal is unreachable from this browser (commit "
        'done(failed, "blocked by <wall>") if you saw a Cloudflare/CAPTCHA/'
        'login wall, or done(failed, "<reason>") otherwise) or you already '
        'have the answer (commit done(success, "<answer>") with the rendered '
        "value from your reads — even if the page does not name the asked "
        "phrase verbatim)."
    ),
    "asked_after_clarification": (
        "User clarification did not unblock you. Commit "
        'done(failed, "<closest answer you have>") rather than retrying the '
        "same action."
    ),
}


def _instruction(trigger: Trigger, n_no_progress: int | None) -> str:
    template = _FORCE_DONE_PROMPTS[trigger]
    if trigger == "no_progress":
        return template.format(n=n_no_progress if n_no_progress is not None else "?")
    return template


_READ_TRUNCATE = 800


def _read_content_dump(tape: list[dict[str, Any]]) -> str:
    reads = [s for s in tape if s.get("action") == "read"]
    if not reads:
        return "Read content captured so far: (none — no read() calls in tape)"
    lines = ["Read content captured so far (chronological):"]
    for s in reads:
        offset = s.get("args", {}).get("offset", 0)
        obs = s.get("obs", "") or ""
        if len(obs) > _READ_TRUNCATE:
            obs = obs[:_READ_TRUNCATE]
        lines.append(f'- read(offset={offset}): "{obs}"')
    return "\n".join(lines)


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
    reason_log: list[str] | None = None,
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
        reason_log=reason_log,
    )
    messages.append({"role": "user", "content": _instruction(trigger, n_no_progress)})
    messages.append({"role": "user", "content": _read_content_dump(tape)})
    try:
        msg, _usage = await llm.chat(
            messages,
            tools=[done_tool_schema],
            tool_choice={"type": "function", "function": {"name": "done"}},
            reasoning=False,
        )
    except ToolNameNotAllowed:
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
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
    status = args.get("status", "failed")
    answer = args.get("answer", _placeholder(trigger, n_no_progress))
    evidence = args.get("evidence", "") or ""
    if status == "success":
        ev_norm = _normalize(evidence)
        haystack = " ".join(_normalize(str(s.get("obs", ""))) for s in tape if isinstance(s, dict))
        if not ev_norm or ev_norm not in haystack:
            return {
                "status": "failed",
                "answer": _placeholder(trigger, n_no_progress),
            }
    return {"status": status, "answer": answer}
