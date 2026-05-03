from __future__ import annotations

import json
from typing import Any

K_RECENT = 8
NOVELTY_WINDOW = 8
OBS_FINGERPRINT_LEN = 200


def _short(action: str, args: dict, obs: str) -> str:
    lines = (obs or "").splitlines()
    obs1 = lines[0][:120] if lines else ""
    return f"{action}({json.dumps(args, ensure_ascii=False)[:80]}) -> {obs1}"


def _call_str(action: str, args: dict) -> str:
    return f"{action}({json.dumps(args or {}, ensure_ascii=False, sort_keys=True)[:60]})"


def _obs_fingerprint(obs: str) -> str:
    return (obs or "")[:OBS_FINGERPRINT_LEN]


def _histogram_line(tape: list[dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    for s in tape:
        k = _call_str(s.get("action", ""), s.get("args", {}))
        counts[k] = counts.get(k, 0) + 1
    repeats = sorted(
        ((k, v) for k, v in counts.items() if v >= 2),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if not repeats:
        return None
    return "Calls so far (≥2): " + ", ".join(f"{k}×{v}" for k, v in repeats)


def _novelty_line(tape: list[dict[str, Any]]) -> str | None:
    if len(tape) < NOVELTY_WINDOW:
        return None
    earlier = tape[:-NOVELTY_WINDOW]
    recent = tape[-NOVELTY_WINDOW:]
    earlier_fps = {_obs_fingerprint(s.get("obs", "")) for s in earlier}
    novel = sum(1 for s in recent if _obs_fingerprint(s.get("obs", "")) not in earlier_fps)
    return f"Novel observations in last {NOVELTY_WINDOW} steps: {novel}/{NOVELTY_WINDOW}"


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
    plateau_interrupt: str | None = None,
) -> list[dict]:
    sys_parts = [system]
    if replan_hint:
        sys_parts.append("")
        sys_parts.append(replan_hint)
    if plateau_interrupt:
        sys_parts.append("")
        sys_parts.append(plateau_interrupt)

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

    hist = _histogram_line(tape)
    if hist:
        user_parts.append("")
        user_parts.append(hist)
    nov = _novelty_line(tape)
    if nov:
        user_parts.append(nov)

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
