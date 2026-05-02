from __future__ import annotations

import json
from typing import Any

K_RECENT = 8


def _short(action: str, args: dict, obs: str) -> str:
    obs1 = (obs or "").splitlines()[0][:120]
    return f"{action}({json.dumps(args, ensure_ascii=False)[:80]}) -> {obs1}"


def build_messages(
    *,
    system: str,
    goal: str,
    qa: list[tuple[str, str]],
    url_notes: str,
    tape: list[dict[str, Any]],
    page_header: str,
    replan_hint: str | None,
) -> list[dict]:
    parts = [system, "", f"Goal: {goal}"]
    if qa:
        parts.append("")
        for q, a in qa:
            parts.append(f"Q: {q}\nA: {a}")
    parts.append("")
    parts.append("URL notes:")
    parts.append(url_notes or "(none)")
    parts.append("")
    parts.append(page_header)

    older = tape[:-K_RECENT] if len(tape) > K_RECENT else []
    recent = tape[-K_RECENT:] if len(tape) > K_RECENT else tape
    if older:
        parts.append("")
        parts.append("Earlier steps:")
        for i, step in enumerate(older):
            parts.append(
                f"- step {i}: {_short(step['action'], step.get('args', {}), step.get('obs', ''))}"
            )

    if replan_hint:
        parts.append("")
        parts.append(replan_hint)

    msgs: list[dict] = [{"role": "system", "content": "\n".join(parts)}]
    base_idx = len(tape) - len(recent)
    for off, step in enumerate(recent):
        idx = base_idx + off
        tool_call_id = f"call_{idx}"
        msgs.append(
            {
                "role": "assistant",
                "content": step.get("thought", ""),
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": step["action"],
                            "arguments": json.dumps(step.get("args", {})),
                        },
                    }
                ],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": step.get("obs", ""),
            }
        )
    return msgs
