from __future__ import annotations

import json
from typing import Any

K_RECENT = 8


def _short(action: str, args: dict, obs: str) -> str:
    lines = (obs or "").splitlines()
    obs1 = lines[0][:120] if lines else ""
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
    sys_parts = [system]
    if replan_hint:
        sys_parts.append("")
        sys_parts.append(replan_hint)

    user_parts = [f"Goal: {goal}"]
    if qa:
        user_parts.append("")
        for q, a in qa:
            user_parts.append(f"Q: {q}\nA: {a}")
    user_parts.append("")
    user_parts.append("URL notes:")
    user_parts.append(url_notes or "(none)")
    user_parts.append("")
    user_parts.append(page_header)

    older = tape[:-K_RECENT] if len(tape) > K_RECENT else []
    recent = tape[-K_RECENT:] if len(tape) > K_RECENT else tape
    if older:
        user_parts.append("")
        user_parts.append("Earlier steps:")
        for i, step in enumerate(older):
            user_parts.append(
                f"- step {i}: {_short(step['action'], step.get('args', {}), step.get('obs', ''))}"
            )

    msgs: list[dict] = [
        {"role": "system", "content": "\n".join(sys_parts)},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
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
