from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from agent.llm import LLMClient
from agent.notes_store import NotesStore


class _TraceLike(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


def root_url(url: str) -> str:
    """Return the site-root form of `url`: scheme://netloc/. Distillation
    is keyed at this granularity so a site's durable facts are shared
    across every page on it."""
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


_PROMPT = (
    "You are extracting durable SITE-FACTS from a single browsing session. "
    "These bullets are persisted across runs and will be loaded as the "
    "starting URL-notes for the next session that visits this site — write "
    "them for a future agent that has never seen this site before.\n\n"
    "Output ≤10 short bullets, one per line, prefixed by '- '. Each bullet "
    "must describe the SITE: selectors and element ids, hidden requirements, "
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
    key = root_url(url)
    try:
        prior = notes.get(key) or "(none)"
        user = (
            f"URL: {key}\n"
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
        notes.set(key, text)
    except Exception as e:
        if trace is not None:
            trace.write({"type": "distill_failed", "payload": {"error": str(e)[:200]}})
