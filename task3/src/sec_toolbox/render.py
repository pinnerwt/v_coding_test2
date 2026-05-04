"""HTML rendering with reverse offset map.

Decodes a 10-K HTML filing into plain text plus per-character byte offsets
back into the source bytes. Splits the rendered text into block-level chunks
so layout features can be attached per chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape


@dataclass
class LayoutFeatures:
    font_size_pt: float = 10.0
    is_bold: bool = False
    is_italic: bool = False
    is_centered: bool = False
    line_length: int = 0
    capitalization_ratio: float = 0.0
    surrounding_whitespace: int = 0


@dataclass
class Chunk:
    text: str
    text_start: int
    text_end: int
    source_start: int
    source_end: int
    layout: LayoutFeatures = field(default_factory=LayoutFeatures)


@dataclass
class Rendered:
    text: str
    source_offset: list[int]
    chunks: list[Chunk]
    median_font_size: float = 10.0

    def chunk_at(self, text_pos: int) -> Chunk | None:
        for c in self.chunks:
            if c.text_start <= text_pos < c.text_end:
                return c
        return None


_BLOCK_TAGS = {
    "p", "div", "li", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "hr", "section", "article", "table", "ul", "ol",
    "header", "footer", "blockquote", "pre", "address",
    "figure", "form", "fieldset", "body", "html", "main", "nav", "aside",
}

_SKIP_TAGS = {"script", "style", "head", "noscript"}

_TAG_RE = re.compile(r"<(/?)(\w[\w:-]*)([^>]*?)/?>", re.DOTALL)


def render_html(html: bytes) -> Rendered:
    """Render HTML bytes to text with a reverse offset map and chunk list."""
    # Latin-1 decode keeps byte offsets equal to character offsets, since each
    # byte maps to exactly one code point. Non-ASCII bytes still survive as
    # individual characters; consumers reading source bytes get the right slice.
    src = html.decode("latin-1", errors="replace")
    n = len(src)

    text_chars: list[str] = []
    source_offsets: list[int] = []
    chunks: list[Chunk] = []

    skip_depth = 0
    cur_text_start: int | None = None
    cur_source_start: int | None = None
    cur_source_end: int | None = None

    def flush_chunk() -> None:
        nonlocal cur_text_start, cur_source_start, cur_source_end
        if cur_text_start is None:
            return
        text_end = len(text_chars)
        if text_end > cur_text_start:
            text = "".join(text_chars[cur_text_start:text_end])
            if text.strip():
                chunks.append(
                    Chunk(
                        text=text,
                        text_start=cur_text_start,
                        text_end=text_end,
                        source_start=cur_source_start or 0,
                        source_end=cur_source_end or 0,
                    )
                )
        cur_text_start = None
        cur_source_start = None
        cur_source_end = None

    def emit_segment(seg_start: int, seg_end: int) -> None:
        nonlocal cur_text_start, cur_source_start, cur_source_end
        if skip_depth > 0:
            return
        i = seg_start
        while i < seg_end:
            ch = src[i]
            if ch == "&":
                semi = src.find(";", i + 1, min(i + 12, seg_end))
                if semi != -1:
                    raw = src[i : semi + 1]
                    decoded = unescape(raw)
                    if decoded != raw:
                        if cur_text_start is None:
                            cur_text_start = len(text_chars)
                            cur_source_start = i
                        for c in decoded:
                            text_chars.append(c)
                            source_offsets.append(i)
                        cur_source_end = semi + 1
                        i = semi + 1
                        continue
            if cur_text_start is None:
                cur_text_start = len(text_chars)
                cur_source_start = i
            text_chars.append(ch)
            source_offsets.append(i)
            cur_source_end = i + 1
            i += 1

    pos = 0
    while pos < n:
        if src.startswith("<!--", pos):
            flush_chunk()
            end = src.find("-->", pos + 4)
            pos = (end + 3) if end != -1 else n
            continue
        if src.startswith("<!", pos):
            flush_chunk()
            end = src.find(">", pos + 2)
            pos = (end + 1) if end != -1 else n
            continue

        lt = src.find("<", pos)
        if lt == -1:
            emit_segment(pos, n)
            break
        if lt > pos:
            emit_segment(pos, lt)

        m = _TAG_RE.match(src, lt)
        if not m:
            # Stray '<' — treat as literal.
            emit_segment(lt, lt + 1)
            pos = lt + 1
            continue

        is_close = m.group(1) == "/"
        tag_name = m.group(2).lower()
        # Strip namespace prefix (e.g. ix:nonNumeric → nonnumeric)
        if ":" in tag_name:
            tag_name = tag_name.split(":", 1)[1]
        tag_end = m.end()

        if tag_name in _SKIP_TAGS:
            flush_chunk()
            if not is_close:
                close_re = re.compile(
                    rf"</\s*(?:\w+:)?{re.escape(tag_name)}\s*>", re.IGNORECASE
                )
                close_m = close_re.search(src, tag_end)
                pos = close_m.end() if close_m else n
            else:
                pos = tag_end
            continue

        if tag_name in _BLOCK_TAGS:
            flush_chunk()

        pos = tag_end

    flush_chunk()

    text = "".join(text_chars)
    return Rendered(
        text=text,
        source_offset=source_offsets,
        chunks=chunks,
    )
