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

import re
from dataclasses import dataclass

from ._locate import build_id_index, source_byte_to_text_offset
from .render import Rendered, render_html
from .taxonomy import Item, items_for_year
from .toc import TOCEntry, TOCRegion, find_toc_region
from .toc_llm import propose_toc


@dataclass
class BodyLocation:
    entry: TOCEntry
    body_text_start: int
    body_source_start: int


@dataclass
class Slice:
    item_title: str
    content_text: str
    char_range: tuple[int, int]
    text_range: tuple[int, int]
    entry: TOCEntry
    part: str | None = None
    item_number: str | None = None
    canonical_title: str | None = None


# Re-exports kept for backward compatibility with tests that imported the
# private helpers directly from this module.
_build_id_index = build_id_index
_source_byte_to_text_offset = source_byte_to_text_offset


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


_ITEM_TITLE_PREFIX_RE = re.compile(r"^\s*Item\s+\d+[A-Z]?\.?\s*[:\-–—]?\s*", re.IGNORECASE)
_ITEM_HEAD_RE = re.compile(r"^\s*Item\s+\d+[A-Z]?\b", re.IGNORECASE)
_ITEM_NUMBER_RE = re.compile(r"^\s*Item\s+(\d+[A-Z]?)\b", re.IGNORECASE)
_TARGET_ITEM_RE = re.compile(r"#?\s*item[_\-\s]*(\d+[a-z]?)(?![0-9a-z])", re.IGNORECASE)


def _extract_item_number_from_target(target: str | None) -> str | None:
    if not target:
        return None
    m = _TARGET_ITEM_RE.match(target.lstrip("#"))
    if not m:
        return None
    return m.group(1).upper()


def _extract_item_number(entry_text: str, target: str | None = None) -> str | None:
    m = _ITEM_NUMBER_RE.match(entry_text)
    if m:
        return m.group(1).upper()
    return _extract_item_number_from_target(target)


def _match_item(entry: TOCEntry, schedule: list[Item]) -> Item | None:
    """Look up the canonical Item for a TOC entry by item-number match."""
    num = _extract_item_number(entry.text, entry.target)
    if not num:
        return None
    for item in schedule:
        if item.item_number == num:
            return item
    return None


def _strip_item_prefix(text: str) -> str:
    return _ITEM_TITLE_PREFIX_RE.sub("", text).strip()


def _is_item_entry(entry: TOCEntry) -> bool:
    if _ITEM_HEAD_RE.match(entry.text):
        return True
    return _extract_item_number_from_target(entry.target) is not None


_MIN_ITEM_COUNT = 10
_MIN_RESOLUTION_RATE = 0.80


def _resolution_rate(item_count: int, entry_count: int) -> float:
    if entry_count == 0:
        return 0.0
    return item_count / entry_count


def segment(html: bytes, fiscal_year: int | None = None) -> list[Slice]:
    """End-to-end pipeline: render → find TOC → resolve → slice into Items.

    Returns a list of :class:`Slice` ordered by document position. Each slice
    spans from one Item heading to the next; the final slice runs to the end
    of the rendered text. Part-headers and non-Item TOC entries are filtered
    out — only Items get slices.

    When ``fiscal_year`` is given, each slice is mapped against the year's
    canonical Item schedule and carries ``part``, ``item_number``, and
    ``canonical_title`` fields. Without a year these fields are ``None``.
    """
    rendered = render_html(html)
    region = find_toc_region(rendered, html)

    body_locs: list[BodyLocation] = []
    if region is not None:
        body_locs = resolve_entries_to_body(rendered, region, html)

    item_locs = [b for b in body_locs if _is_item_entry(b.entry)]
    needs_fallback = (
        region is None
        or len(item_locs) < _MIN_ITEM_COUNT
        or _resolution_rate(len(item_locs), len(region.entries)) < _MIN_RESOLUTION_RATE
    )

    if needs_fallback:
        llm_region = propose_toc(rendered, html)
        if llm_region is not None:
            # propose_toc already places each TOCEntry at its resolved body
            # position (anchor or snippet match), so trust those rather than
            # re-running anchor/prominence resolution.
            llm_item_locs = [
                BodyLocation(
                    entry=e,
                    body_text_start=e.text_start,
                    body_source_start=e.source_start,
                )
                for e in llm_region.entries
                if _is_item_entry(e)
            ]
            # Accept only if the LLM produced strictly more items than the
            # heuristic — anything else and we'd be making things worse.
            if len(llm_item_locs) > len(item_locs):
                region = llm_region
                item_locs = llm_item_locs

    if not item_locs:
        return []
    item_locs.sort(key=lambda b: b.body_text_start)
    # Drop duplicate text positions (some filings re-use targets across rows).
    deduped: list[BodyLocation] = []
    for b in item_locs:
        if deduped and b.body_text_start == deduped[-1].body_text_start:
            continue
        deduped.append(b)

    schedule: list[Item] | None = items_for_year(fiscal_year) if fiscal_year is not None else None
    slices: list[Slice] = []
    text = rendered.text
    src_offsets = rendered.source_offset
    for i, loc in enumerate(deduped):
        text_start = loc.body_text_start
        text_end = deduped[i + 1].body_text_start if i + 1 < len(deduped) else len(text)
        if text_end <= text_start:
            continue
        content = text[text_start:text_end]
        if not content.strip():
            continue
        char_start = src_offsets[text_start] if text_start < len(src_offsets) else 0
        # The end byte is one past the last char's source byte; clamp to len.
        last_char_idx = text_end - 1
        char_end = src_offsets[last_char_idx] + 1 if last_char_idx < len(src_offsets) else len(html)
        if char_end <= char_start:
            continue
        canon = _match_item(loc.entry, schedule) if schedule is not None else None
        slices.append(
            Slice(
                item_title=_strip_item_prefix(loc.entry.text),
                content_text=content,
                char_range=(char_start, char_end),
                text_range=(text_start, text_end),
                entry=loc.entry,
                part=canon.part if canon else None,
                item_number=canon.item_number if canon else None,
                canonical_title=canon.canonical_title if canon else None,
            )
        )
    return slices
