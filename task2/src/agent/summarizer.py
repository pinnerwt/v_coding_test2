from __future__ import annotations

from agent.llm import LLMClient
from agent.notes_store import NotesStore

_PROMPT = (
    "You compress a browsing agent's recent activity at a single URL into 1–3 short "
    "bullet lines (each ≤120 chars). Focus on what failed, what was found, what to "
    'remember next visit. Output bullets only, one per line, prefixed by "- ". '
    "No prose, no headers."
)


class Summarizer:
    def __init__(self, llm: LLMClient, notes: NotesStore) -> None:
        self._llm = llm
        self._notes = notes

    async def maybe_summarize(
        self,
        trigger: str,
        *,
        prior_url: str,
        tape_slice: list[dict],
        existing_notes: str,
    ) -> None:
        if not prior_url:
            return
        user = (
            f"Trigger: {trigger}\nURL: {prior_url}\n"
            f"Existing notes:\n{existing_notes or '(none)'}\n\n"
            f"Recent activity:\n{tape_slice}"
        )
        msg, _ = await self._llm.chat(
            [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": user},
            ],
            reasoning=False,
        )
        for raw in (msg.get("content") or "").splitlines():
            line = raw.strip()
            if line.startswith("- ") and len(line) <= 200:
                self._notes.append(prior_url, line[2:].strip())
