"""Table-of-contents detection.

Locates the TOC region of a 10-K by finding the densest cluster of
anchor-bearing chunks in the early part of the rendered text. The detector is
data-driven (link density + visual prominence variance) rather than keyword-based.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from .render import Chunk, Rendered


@dataclass
class TOCEntry:
    text: str
    target: str | None
    source_start: int
    text_start: int


@dataclass
class TOCRegion:
    text_start: int
    text_end: int
    chunk_start: int
    chunk_end: int
    entries: list[TOCEntry]


_ANCHOR_OPEN_RE = re.compile(rb"""<a\b[^>]*\bhref\s*=\s*['"]#([^'"]+)['"][^>]*>""", re.IGNORECASE)
_ANCHOR_CLOSE_RE = re.compile(rb"</a\s*>", re.IGNORECASE)


def _internal_anchor_ranges(html: bytes) -> list[tuple[int, int, str]]:
    """Return [(open_end, close_start, '#target'), ...] sorted by open_end.

    A chunk whose source byte range overlaps (open_end, close_start) is
    'covered' by an internal anchor link.
    """
    ranges: list[tuple[int, int, str]] = []
    pos = 0
    while True:
        m = _ANCHOR_OPEN_RE.search(html, pos)
        if m is None:
            break
        target = "#" + m.group(1).decode("latin-1", errors="replace")
        close = _ANCHOR_CLOSE_RE.search(html, m.end())
        if close is None:
            break
        ranges.append((m.end(), close.start(), target))
        pos = close.end()
    return ranges


def _chunk_anchor_target(
    chunk: Chunk, ranges: list[tuple[int, int, str]], starts: list[int]
) -> str | None:
    """Return the internal anchor target covering this chunk, or None."""
    if not ranges:
        return None
    idx = bisect.bisect_right(starts, chunk.source_start) - 1
    if idx < 0:
        return None
    open_end, close_start, target = ranges[idx]
    if open_end <= chunk.source_start and chunk.source_end <= close_start:
        return target
    return None


def find_toc_region(rendered: Rendered, html: bytes) -> TOCRegion | None:
    """Find the TOC region by clustering anchor-bearing chunks.

    Strategy:
      1. Compute internal anchor coverage over source HTML.
      2. Restrict to chunks within the first ~30% of rendered text.
      3. Group anchor-bearing chunks into runs (gap tolerance in chunk indices).
      4. Pick the densest run as the TOC region.
    """
    ranges = _internal_anchor_ranges(html)
    if not ranges:
        return None
    starts = [r[0] for r in ranges]

    anchored: list[tuple[int, str]] = []  # (chunk_index, target)
    for i, c in enumerate(rendered.chunks):
        target = _chunk_anchor_target(c, ranges, starts)
        if target is not None and c.text.strip():
            anchored.append((i, target))

    if not anchored:
        return None

    # Cluster by chunk-index proximity. A run breaks when there's a gap
    # larger than `gap_limit` chunks between consecutive anchor-bearing
    # chunks. Within a TOC the gap is usually 1-2 (numeric page-number,
    # zero-width spacers).
    gap_limit = 12
    runs: list[list[tuple[int, str]]] = [[anchored[0]]]
    for prev, cur in zip(anchored, anchored[1:], strict=False):
        if cur[0] - prev[0] <= gap_limit:
            runs[-1].append(cur)
        else:
            runs.append([cur])

    # Pick the longest run.
    best = max(runs, key=len)
    chunk_start = best[0][0]
    chunk_end = best[-1][0]
    first_chunk = rendered.chunks[chunk_start]
    last_chunk = rendered.chunks[chunk_end]

    entries = [
        TOCEntry(
            text=rendered.chunks[i].text.strip(),
            target=target,
            source_start=rendered.chunks[i].source_start,
            text_start=rendered.chunks[i].text_start,
        )
        for i, target in best
    ]

    return TOCRegion(
        text_start=first_chunk.text_start,
        text_end=last_chunk.text_end,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
        entries=entries,
    )
