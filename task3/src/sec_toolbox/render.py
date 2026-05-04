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
    "p",
    "div",
    "li",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "hr",
    "section",
    "article",
    "table",
    "ul",
    "ol",
    "header",
    "footer",
    "blockquote",
    "pre",
    "address",
    "figure",
    "form",
    "fieldset",
    "body",
    "html",
    "main",
    "nav",
    "aside",
}

_VOID_TAGS = {
    "br",
    "hr",
    "img",
    "input",
    "meta",
    "link",
    "area",
    "base",
    "col",
    "embed",
    "param",
    "source",
    "track",
    "wbr",
}

_SKIP_TAGS = {"script", "style", "head", "noscript"}

_BOLD_TAGS = {"b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"}
_ITALIC_TAGS = {"i", "em"}

_HEADING_SIZES = {"h1": 18.0, "h2": 16.0, "h3": 14.0, "h4": 12.0, "h5": 11.0, "h6": 10.5}

_TAG_RE = re.compile(r"<(/?)(\w[\w:-]*)([^>]*?)(/?)>", re.DOTALL)
_STYLE_RE = re.compile(r'style\s*=\s*"([^"]*)"|style\s*=\s*\'([^\']*)\'', re.IGNORECASE)
_ALIGN_ATTR_RE = re.compile(r'align\s*=\s*"([^"]*)"|align\s*=\s*\'([^\']*)\'', re.IGNORECASE)


def _parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        k, v = decl.split(":", 1)
        out[k.strip().lower()] = v.strip().lower()
    return out


def _font_size_to_pt(value: str) -> float | None:
    m = re.match(r"([\d.]+)\s*(pt|px|em|rem)?", value)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = (m.group(2) or "pt").lower()
    if unit == "pt":
        return n
    if unit == "px":
        return n * 0.75
    if unit in ("em", "rem"):
        return n * 10.0
    return n


def _capitalization_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _build_layout(parent: dict, tag_name: str, attrs_str: str) -> dict:
    new = dict(parent)
    if tag_name in _BOLD_TAGS:
        new["is_bold"] = True
    if tag_name in _ITALIC_TAGS:
        new["is_italic"] = True
    if tag_name == "center":
        new["is_centered"] = True
    if tag_name in _HEADING_SIZES:
        new["font_size_pt"] = max(new["font_size_pt"], _HEADING_SIZES[tag_name])

    align_m = _ALIGN_ATTR_RE.search(attrs_str)
    if align_m and (align_m.group(1) or align_m.group(2)) == "center":
        new["is_centered"] = True

    style_m = _STYLE_RE.search(attrs_str)
    if style_m:
        style = _parse_style(style_m.group(1) or style_m.group(2) or "")
        fs = style.get("font-size")
        if fs:
            pt = _font_size_to_pt(fs)
            if pt is not None:
                new["font_size_pt"] = pt
        fw = style.get("font-weight")
        if fw:
            if fw == "bold" or (fw.isdigit() and int(fw) >= 600):
                new["is_bold"] = True
            elif fw == "normal":
                new["is_bold"] = False
        fst = style.get("font-style")
        if fst == "italic":
            new["is_italic"] = True
        ta = style.get("text-align")
        if ta == "center":
            new["is_centered"] = True
        elif ta in ("left", "right", "justify"):
            new["is_centered"] = False
    return new


def render_html(html: bytes) -> Rendered:
    """Render HTML bytes to text with a reverse offset map and chunk list."""
    src = html.decode("latin-1", errors="replace")
    n = len(src)

    text_chars: list[str] = []
    source_offsets: list[int] = []
    chunks: list[Chunk] = []

    skip_depth = 0
    cur_text_start: int | None = None
    cur_source_start: int | None = None
    cur_source_end: int | None = None
    cur_layout: LayoutFeatures | None = None

    root_layout = {
        "font_size_pt": 10.0,
        "is_bold": False,
        "is_italic": False,
        "is_centered": False,
    }
    open_stack: list[tuple[str, dict]] = [("__root__", root_layout)]

    def merge_layout_into_current() -> None:
        nonlocal cur_layout
        top = open_stack[-1][1]
        if cur_layout is None:
            cur_layout = LayoutFeatures(
                font_size_pt=top["font_size_pt"],
                is_bold=top["is_bold"],
                is_italic=top["is_italic"],
                is_centered=top["is_centered"],
            )
        else:
            cur_layout.font_size_pt = max(cur_layout.font_size_pt, top["font_size_pt"])
            cur_layout.is_bold = cur_layout.is_bold or top["is_bold"]
            cur_layout.is_italic = cur_layout.is_italic or top["is_italic"]
            cur_layout.is_centered = cur_layout.is_centered or top["is_centered"]

    def flush_chunk() -> None:
        nonlocal cur_text_start, cur_source_start, cur_source_end, cur_layout
        if cur_text_start is None:
            cur_layout = None
            return
        text_end = len(text_chars)
        if text_end > cur_text_start:
            text = "".join(text_chars[cur_text_start:text_end])
            if text.strip():
                layout = cur_layout or LayoutFeatures()
                stripped = text.strip()
                layout.line_length = len(stripped)
                layout.capitalization_ratio = _capitalization_ratio(stripped)
                chunks.append(
                    Chunk(
                        text=text,
                        text_start=cur_text_start,
                        text_end=text_end,
                        source_start=cur_source_start or 0,
                        source_end=cur_source_end or 0,
                        layout=layout,
                    )
                )
        cur_text_start = None
        cur_source_start = None
        cur_source_end = None
        cur_layout = None

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
                        merge_layout_into_current()
                        for c in decoded:
                            text_chars.append(c)
                            source_offsets.append(i)
                        cur_source_end = semi + 1
                        i = semi + 1
                        continue
            if cur_text_start is None:
                cur_text_start = len(text_chars)
                cur_source_start = i
            merge_layout_into_current()
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
            emit_segment(lt, lt + 1)
            pos = lt + 1
            continue

        is_close = m.group(1) == "/"
        raw_tag = m.group(2).lower()
        attrs_str = m.group(3) or ""
        self_closing = bool(m.group(4))
        tag_name = raw_tag.split(":", 1)[1] if ":" in raw_tag else raw_tag
        tag_end = m.end()

        if tag_name in _SKIP_TAGS:
            flush_chunk()
            if not is_close:
                close_re = re.compile(rf"</\s*(?:\w+:)?{re.escape(tag_name)}\s*>", re.IGNORECASE)
                close_m = close_re.search(src, tag_end)
                pos = close_m.end() if close_m else n
            else:
                pos = tag_end
            continue

        if is_close:
            if tag_name in _BLOCK_TAGS:
                flush_chunk()
            for j in range(len(open_stack) - 1, 0, -1):
                if open_stack[j][0] == tag_name:
                    del open_stack[j:]
                    break
            pos = tag_end
            continue

        if tag_name in _BLOCK_TAGS:
            flush_chunk()

        if tag_name not in _VOID_TAGS and not self_closing:
            new_layout = _build_layout(open_stack[-1][1], tag_name, attrs_str)
            open_stack.append((tag_name, new_layout))

        pos = tag_end

    flush_chunk()

    if chunks:
        sizes = sorted(c.layout.font_size_pt for c in chunks)
        median = sizes[len(sizes) // 2]
    else:
        median = 10.0

    text = "".join(text_chars)
    return Rendered(
        text=text,
        source_offset=source_offsets,
        chunks=chunks,
        median_font_size=median,
    )
