from __future__ import annotations

import json
from typing import Any


def _action_call_str(action: str, args: dict) -> str:
    """Render `action(arg1=v1, arg2=v2)` for the most-discriminating args.
    Skip None / empty / very-long values. ~60-char target."""
    if not args:
        return f"{action}()"
    parts = []
    for k, v in args.items():
        if v in (None, "", []):
            continue
        sval = json.dumps(v, ensure_ascii=False)
        if len(sval) > 40:
            sval = sval[:37] + "..."
        parts.append(f"{k}={sval}")
    return f"{action}({', '.join(parts)})"


def _render_narrative(tape: list[dict[str, Any]]) -> str:
    if not tape:
        return ""

    def _flat(s: str) -> str:
        return (s or "").replace("\n", " ").replace("\r", " ").strip()

    lines = ["## Action history"]
    for i, step in enumerate(tape):
        url = _flat(step.get("url", ""))
        call = _action_call_str(step.get("action", ""), step.get("args", {}))
        reason = _flat(step.get("reason", ""))
        lines.append(f"step {i} | {url} | {call} | {reason}")
    return "\n".join(lines)


def build_messages(
    *,
    system: str,
    goal: str,
    qa: list[tuple[str, str]],
    url_notes: str,
    tape: list[dict[str, Any]],
    page_header: str,
    replan_hint: str | None,
    page_diff: str | None = None,
    wall_banner: str | None = None,
    reason_log: list[str] | None = None,
) -> list[dict]:
    sys_parts = [system]
    if replan_hint:
        sys_parts.append("")
        sys_parts.append(replan_hint)

    narrative = _render_narrative(tape)
    if narrative:
        sys_parts.append("")
        sys_parts.append(narrative)

    user_parts = [f"Goal: {goal}"]
    if qa:
        user_parts.append("")
        for q, a in qa:
            user_parts.append(f"Q: {q}\nA: {a}")
    user_parts.append("")
    user_parts.append("URL notes:")
    user_parts.append(url_notes or "(none)")
    if reason_log is not None:
        user_parts.append("")
        user_parts.append("Reasoning so far:")
        if reason_log:
            rendered = "\n".join(f"- {r}" for r in reason_log)
            # FIFO trim: keep newest, drop oldest, until ≤4 KB.
            while len(rendered.encode()) > 4096 and "\n" in rendered:
                rendered = rendered.split("\n", 1)[1]
            user_parts.append(rendered)
        else:
            user_parts.append("(none)")
    user_parts.append("")
    user_parts.append(page_header)

    if page_diff:
        user_parts.append("")
        user_parts.append(page_diff)

    if wall_banner:
        user_parts.append("")
        user_parts.append(wall_banner)

    recent_obs = [step.get("obs", "") for step in tape[-3:]]
    if recent_obs:
        user_parts.append("")
        user_parts.append("## Recent observations (last 3)")
        user_parts.append("---")
        for obs in recent_obs:
            user_parts.append(obs)
            user_parts.append("---")

    msgs: list[dict] = [
        {"role": "system", "content": "\n".join(sys_parts)},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
    return msgs
