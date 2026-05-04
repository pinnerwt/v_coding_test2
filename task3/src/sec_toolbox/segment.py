"""Segmentation: resolve TOC entries to body locations and slice the filing.

Given a rendered 10-K and its TOC region, look up each TOC entry's anchor
target in the source HTML, find where that target is *defined* (i.e. the
element carrying ``id="..."`` or ``name="..."``), and translate that source
byte position back into a rendered-text offset.

Older filings without internal anchors fall back to a visual-prominence
search: scan rendered chunks past the TOC region and pick the first
prominently-formatted chunk whose normalized text matches the entry title.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from .render import Rendered
from .toc import TOCEntry, TOCRegion


@dataclass
class BodyLocation:
    entry: TOCEntry
    body_text_start: int
    body_source_start: int


_ID_ATTR_RE = re.compile(rb"""\b(?:id|name)\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)


def _build_id_index(html: bytes) -> dict[str, int]:
    """Map every ``id=``/``name=`` attribute value to the source byte where the
    enclosing tag opens.

    When an anchor target is defined more than once we keep the *last*
    occurrence — the body usage typically follows TOC link declarations, and
    the last definition is the body anchor. The previous declaration in the
    TOC itself is intentionally overwritten.
    """
    index: dict[str, int] = {}
    for m in _ID_ATTR_RE.finditer(html):
        name = m.group(1).decode("latin-1", errors="replace")
        # Walk back from the attribute to the '<' of the opening tag.
        lt = html.rfind(b"<", 0, m.start())
        index[name] = lt if lt != -1 else m.start()
    return index


def _source_byte_to_text_offset(source_offsets: list[int], byte_pos: int) -> int:
    """Map a source byte position to the smallest rendered-text index whose
    ``source_offset`` is >= ``byte_pos``. Returns ``len(source_offsets)`` if
    no rendered character originates at or after that byte.

    ``source_offsets`` is monotonically non-decreasing, so binary search works.
    """
    return bisect.bisect_left(source_offsets, byte_pos)


_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("\xa0", " ").lower()).strip()


def resolve_via_prominence(rendered: Rendered, region: TOCRegion, entry: TOCEntry) -> int | None:
    """Find a body location for ``entry`` by scanning prominent chunks past
    the TOC region and matching against the entry's normalized text.

    Returns the rendered-text offset of the first prominent chunk whose
    normalized text contains the entry title (or vice-versa), or ``None`` if
    no such chunk is found.
    """
    key = _normalize(entry.text)
    if not key:
        return None
    median_fs = rendered.median_font_size
    for c in rendered.chunks:
        if c.text_start <= region.text_end:
            continue
        if not (c.layout.is_bold or c.layout.font_size_pt > median_fs):
            continue
        cand = _normalize(c.text)
        if not cand:
            continue
        if key in cand or (len(cand) >= 6 and cand in key):
            return c.text_start
    return None


def resolve_entries_to_body(
    rendered: Rendered, region: TOCRegion, html: bytes
) -> list[BodyLocation]:
    """Resolve each TOC entry to a body text location.

    First tries the entry's internal anchor target (preferred — exact and
    cheap). If the entry has no target, or the target is not defined past the
    TOC region, falls back to a visual-prominence text-match search.
    """
    id_index = _build_id_index(html)
    locations: list[BodyLocation] = []
    for entry in region.entries:
        body_text_start: int | None = None
        body_source: int | None = None

        if entry.target:
            name = entry.target.lstrip("#")
            if name:
                src = id_index.get(name)
                if src is not None:
                    text_pos = _source_byte_to_text_offset(rendered.source_offset, src)
                    if text_pos > region.text_end:
                        body_text_start = text_pos
                        body_source = src

        if body_text_start is None:
            text_pos = resolve_via_prominence(rendered, region, entry)
            if text_pos is not None:
                body_text_start = text_pos
                if text_pos < len(rendered.source_offset):
                    body_source = rendered.source_offset[text_pos]

        if body_text_start is None:
            continue
        locations.append(
            BodyLocation(
                entry=entry,
                body_text_start=body_text_start,
                body_source_start=body_source if body_source is not None else 0,
            )
        )
    return locations
