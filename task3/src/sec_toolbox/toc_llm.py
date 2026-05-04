"""LLM-driven fallback for locating the master 10-K TOC.

Used when ``toc.find_toc_region`` finds a region but the resolved item
count or resolution rate is too low (the GE 2018 case, where the heuristic
locks onto a nested MD&A sub-TOC). The LLM sees the first ~50K rendered
characters and returns a structured tool-call payload describing where the
TOC ends and where each Item begins.

The output is a synthetic :class:`TOCRegion` shaped exactly like the one
:func:`sec_toolbox.toc.find_toc_region` produces, so downstream code in
``segment.py`` is unchanged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ._locate import build_id_index, source_byte_to_text_offset
from .llm import LLMClient
from .render import Rendered
from .toc import TOCEntry, TOCRegion

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "toc_extraction.md"
_FRONT_SLICE_CHARS = 50_000


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_client() -> LLMClient | None:
    try:
        return LLMClient()
    except RuntimeError:
        return None


def _parse_payload(text: str) -> dict | None:
    """Pull a JSON object out of the LLM's response. Tolerate accidental
    code-fence wrapping."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _resolve_item(
    item: dict,
    rendered: Rendered,
    id_index: dict[str, int],
    body_start: int,
) -> tuple[int, int] | None:
    """Resolve a single item to (text_start, source_start) past body_start."""
    anchor = item.get("anchor")
    snippet = item.get("heading_snippet") or ""
    if isinstance(anchor, str) and anchor:
        name = anchor.lstrip("#")
        src = id_index.get(name)
        if src is not None:
            text_pos = source_byte_to_text_offset(rendered.source_offset, src)
            if text_pos > body_start:
                return text_pos, src
    if snippet:
        idx = rendered.text.find(snippet, body_start)
        if idx != -1:
            src = (
                rendered.source_offset[idx]
                if idx < len(rendered.source_offset)
                else 0
            )
            return idx, src
    return None


def propose_toc(
    rendered: Rendered, html: bytes, *, client: LLMClient | None = None
) -> TOCRegion | None:
    """Best-effort LLM TOC extraction. Returns ``None`` on any failure."""
    if client is None:
        client = _build_client()
        if client is None:
            return None

    front = rendered.text[:_FRONT_SLICE_CHARS]
    if not front:
        return None

    prompt = _load_prompt()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": front},
    ]
    try:
        raw = client.chat(messages, temperature=0.0)
    except Exception:  # noqa: BLE001
        return None

    payload = _parse_payload(raw)
    if payload is None:
        return None

    marker = payload.get("toc_end_marker") or ""
    items = payload.get("items") or []
    if not isinstance(marker, str) or not isinstance(items, list) or not items:
        return None

    body_start = rendered.text.find(marker)
    if body_start < 0:
        return None

    id_index = build_id_index(html)
    entries: list[TOCEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        num = item.get("item_number")
        if not isinstance(num, str) or not num:
            continue
        loc = _resolve_item(item, rendered, id_index, body_start)
        if loc is None:
            continue
        text_pos, src_pos = loc
        snippet = item.get("heading_snippet") or num
        entries.append(
            TOCEntry(
                text=f"Item {num}. {snippet}",
                target=item.get("anchor"),
                source_start=src_pos,
                text_start=text_pos,
            )
        )

    if not entries:
        return None

    entries.sort(key=lambda e: e.text_start)
    text_end = body_start + len(marker)
    # Synthetic region: the LLM's TOC view starts at the document head.
    return TOCRegion(
        text_start=0,
        text_end=text_end,
        chunk_start=0,
        chunk_end=0,
        entries=entries,
    )
