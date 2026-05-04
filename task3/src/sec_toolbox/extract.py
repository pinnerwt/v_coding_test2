"""End-to-end extraction: HTML bytes → list of per-Item dicts.

Glue layer over :mod:`segment` and :mod:`status`. Each output row matches the
brief schema: ``part``, ``item_number``, ``item_title``, ``content_text``,
``char_range``, ``status``. ``item_title`` prefers the canonical title from
the Item taxonomy when the slice was matched; otherwise falls back to the
TOC entry's text (with the "Item N." prefix stripped).
"""

from __future__ import annotations

from typing import Any

from .segment import Slice, segment
from .status import classify


def _row_for_slice(s: Slice) -> dict[str, Any]:
    status = classify(s).status
    title = s.canonical_title or s.item_title
    return {
        "part": s.part,
        "item_number": s.item_number,
        "item_title": title,
        "content_text": s.content_text,
        "char_range": s.char_range,
        "status": status,
    }


def extract(html: bytes, fiscal_year: int) -> list[dict[str, Any]]:
    """Run the full pipeline and return one dict per Item slice."""
    slices = segment(html, fiscal_year=fiscal_year)
    return [_row_for_slice(s) for s in slices]
