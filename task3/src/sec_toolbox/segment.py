"""Segmentation: resolve TOC entries to body locations and slice the filing.

Given a rendered 10-K and its TOC region, look up each TOC entry's anchor
target in the source HTML, find where that target is *defined* (i.e. the
element carrying ``id="..."`` or ``name="..."``), and translate that source
byte position back into a rendered-text offset.
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


def resolve_entries_to_body(
    rendered: Rendered, region: TOCRegion, html: bytes
) -> list[BodyLocation]:
    """Resolve each TOC entry to a body text location via its anchor target.

    Entries without an anchor target, or whose target cannot be found in the
    HTML, are skipped here. Older filings without anchors are handled by the
    visual-prominence fallback in :func:`resolve_via_prominence`.
    """
    id_index = _build_id_index(html)
    locations: list[BodyLocation] = []
    for entry in region.entries:
        if not entry.target:
            continue
        name = entry.target.lstrip("#")
        if not name:
            continue
        body_source = id_index.get(name)
        if body_source is None:
            continue
        # Skip targets that point inside the TOC region itself (shouldn't
        # happen in well-formed filings, but guard against self-loops).
        body_text_start = _source_byte_to_text_offset(rendered.source_offset, body_source)
        if body_text_start <= region.text_end:
            continue
        locations.append(
            BodyLocation(
                entry=entry,
                body_text_start=body_text_start,
                body_source_start=body_source,
            )
        )
    return locations
