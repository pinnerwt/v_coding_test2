from __future__ import annotations

from typing import Any, Protocol

from agent.llm import LLMClient
from agent.notes_store import NotesStore


class _TraceLike(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


_PROMPT = (
    "You are extracting durable PAGE-FACTS from a single browsing session, "
    "to help any FUTURE agent reach ANY goal on this URL.\n\n"
    "Output ≤10 short bullets, one per line, prefixed by '- '. Each bullet "
    "must describe the PAGE: selectors and element ids, hidden requirements, "
    "dead-end actions, walls (Cloudflare/CAPTCHA/login), where data lives, "
    "what filters or dropdowns exist. Reject anything goal-specific (the "
    "value the user asked for in this run, intermediate analyses of that "
    "value). If a prior note is supplied, fold it in: deduplicate, prefer "
    "the more specific phrasing, drop facts contradicted by this session.\n\n"
    "No prose, no headers, no preamble. Just the bullets."
)


async def distill_page_knowledge(
    *,
    llm: LLMClient,
    notes: NotesStore | None,
    url: str,
    goal: str,
    status: str,
    answer: str,
    tape: list[dict[str, Any]],
    reason_log: list[str],
    trace: _TraceLike | None,
) -> None:
    """Fire-and-forget post-`done()` distillation. Writes goal-agnostic
    page-facts to `notes` for `url`, replacing the prior row. Any failure
    is swallowed and emitted as a `distill_failed` trace event so the
    user-visible result is unaffected."""
    if notes is None or not url:
        return
    try:
        prior = notes.get(url) or "(none)"
        user = (
            f"URL: {url}\n"
            f"Goal: {goal}\n"
            f"Status: {status}\n"
            f"Final answer: {answer}\n\n"
            f"Prior page-note for this URL:\n{prior}\n\n"
            f"In-session reasoning the agent kept:\n"
            + ("\n".join(f"- {r}" for r in reason_log) or "(none)")
            + "\n\nFull tape:\n"
            + "\n".join(
                f"- {s.get('action')}({s.get('args')}) → {(s.get('obs') or '')[:200]!r}"
                for s in tape
            )
        )
        msg, _ = await llm.chat(
            [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": user},
            ],
            reasoning=False,
        )
        text = (msg.get("content") or "").strip()
        if not text:
            return
        notes.set(url, text)
    except Exception as e:
        if trace is not None:
            trace.write({"type": "distill_failed", "payload": {"error": str(e)[:200]}})
