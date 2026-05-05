"""HTML cleaner for SEC 10-K filings.

Ported from per-filing extractor scripts (see
``task3/scripts/extract/320193-000032019323000106.py``). The per-filing
``PAGE_FOOTER_RE`` knob is generalised here as ``extra_strip_patterns``: a list
of regex strings each applied via ``re.sub(pat, "\\n", text)`` after the
default cleanup but before the final newline-collapse pass.
"""

from __future__ import annotations

import html
import re

BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "section",
    "article",
    "header",
    "footer",
    "ul",
    "ol",
    "td",
    "th",
    "hr",
    "address",
}


def clean_html(raw: str, *, extra_strip_patterns: list[str] | None = None) -> str:
    """Clean SEC 10-K HTML to plain text suitable for anchor finding.

    Transform order:
      1. Strip <ix:header> and <ix:hidden> blocks (inline XBRL).
      2. Strip <script>, <style>, <head>, <noscript> blocks.
      3. Replace block-level open/close tags with newlines.
      4. Strip remaining tags.
      5. Decode HTML entities.
      6. Normalise NBSP (U+00A0) to space; drop ZWSP (U+200B).
      7. Collapse runs of horizontal whitespace.
      8. Trim trailing/leading whitespace per line.
      9. Collapse 3+ consecutive newlines to 2.
     10. Apply each pattern in `extra_strip_patterns` via re.sub(pat, "\\n", text).
     11. Collapse 3+ newlines again, then strip().

    `extra_strip_patterns` matches against already-normalised text (after step 9).
    Use this for filing-specific recurring page-running footers, e.g.
    r"\\nApple Inc\\. \\| 2023 Form 10-K \\| \\d+\\n".
    """
    # Strip inline-XBRL hidden/header blocks first — their nonNumeric values
    # would otherwise leak as plain text to the top of the cleaned output.
    raw = re.sub(
        r"<\s*ix:(header|hidden)\b[^>]*>.*?<\s*/\s*ix:\1\s*>",
        " ",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    raw = re.sub(
        r"<(script|style|head|noscript)\b[^>]*>.*?</\1>",
        " ",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    block_pat = re.compile(
        r"<\s*/?\s*(" + "|".join(BLOCK_TAGS) + r")\b[^>]*>",
        re.IGNORECASE,
    )
    raw = block_pat.sub("\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw)
    raw = raw.replace("\xa0", " ").replace("​", "")
    raw = re.sub(r"[ \t\f\v]+", " ", raw)
    raw = re.sub(r" *\n", "\n", raw)
    raw = re.sub(r"\n *", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    if extra_strip_patterns:
        for pat in extra_strip_patterns:
            raw = re.sub(pat, "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()
