"""Source-byte / rendered-text locator helpers shared by ``segment`` and
``toc_llm``. Kept as a tiny module to avoid circular imports between those
two."""

from __future__ import annotations

import bisect
import re

_ID_ATTR_RE = re.compile(rb"""\b(?:id|name)\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)


def build_id_index(html: bytes) -> dict[str, int]:
    """Map every ``id=``/``name=`` attribute value to the source byte where the
    enclosing tag opens. The last occurrence wins (body anchors typically
    follow TOC link declarations)."""
    index: dict[str, int] = {}
    for m in _ID_ATTR_RE.finditer(html):
        name = m.group(1).decode("latin-1", errors="replace")
        lt = html.rfind(b"<", 0, m.start())
        index[name] = lt if lt != -1 else m.start()
    return index


def source_byte_to_text_offset(source_offsets: list[int], byte_pos: int) -> int:
    """Map a source byte position to the smallest rendered-text index whose
    ``source_offset`` is >= ``byte_pos``. Returns ``len(source_offsets)`` if no
    rendered character originates at or after that byte. Binary search; the
    offsets array is monotonically non-decreasing."""
    return bisect.bisect_left(source_offsets, byte_pos)
