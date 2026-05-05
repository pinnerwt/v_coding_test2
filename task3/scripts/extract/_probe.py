"""Diagnostic CLI for new 10-K filings.

Run probes before writing a per-filing extractor: spot inline-XBRL, count
PART/ITEM matches (running-header detection), sample heading shapes, hunt
page footers, search arbitrary regex/literal in the cleaned text.

Usage:
  _probe.py <html_path> head --bytes 2000
  _probe.py <html_path> clean-head --chars 2000
  _probe.py <html_path> anchors [--part-re RE] [--item-re RE]
  _probe.py <html_path> items [--item-re RE] [--context 80] [--limit N]
  _probe.py <html_path> find --pattern STR [--literal] [--ignore-case] [--limit N]
  _probe.py <html_path> footers
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

# --- shared cleaner (same shape as the per-filing extractors) ---

BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "section", "article", "header", "footer", "ul", "ol",
    "td", "th", "hr", "address",
}

DEFAULT_PART_RE = r"^\s*PART\s+(IV|III|II|I)\b\.?"
DEFAULT_ITEM_RE = r"^\s*ITEM\s+(1[0-6]|[1-9])([A-C])?\s*[\.\:]\s*(.*?)$"

# Likely page-footer patterns. Generic shapes only — extractors define the
# filing-specific exact regex; this probe just surfaces candidates.
FOOTER_HEURISTICS = [
    # "Company Name(, Inc.)? | <year> Form 10-K | <n>"
    r"[A-Z][A-Za-z0-9 .,&'\-]+?(?:,?\s*Inc\.?)?\s*\|\s*\d{4}\s+Form\s+10-K\s*\|\s*\d+",
    # "Form 10-K | <n>" trailing-page-number variant
    r"\bForm\s+10-K\s*\|\s*\d+\b",
]


def clean_html(raw: str) -> str:
    raw = re.sub(r"<(script|style|head|noscript)\b[^>]*>.*?</\1>", " ",
                 raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<ix:header\b[^>]*>.*?</ix:header>", " ",
                 raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<ix:hidden\b[^>]*>.*?</ix:hidden>", " ",
                 raw, flags=re.DOTALL | re.IGNORECASE)
    block_pat = re.compile(
        r"<\s*/?\s*(" + "|".join(BLOCK_TAGS) + r")\b[^>]*>",
        re.IGNORECASE,
    )
    raw = block_pat.sub("\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw)
    raw = raw.replace(" ", " ").replace("​", "")
    raw = re.sub(r"[ \t\f\v]+", " ", raw)
    raw = re.sub(r" *\n", "\n", raw)
    raw = re.sub(r"\n *", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


# --- subcommand implementations ---

def cmd_head(html_path: Path, n_bytes: int = 2000) -> str:
    return Path(html_path).read_text(encoding="utf-8", errors="replace")[:n_bytes]


def cmd_clean_head(html_path: Path, n_chars: int = 2000) -> str:
    text = clean_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    return text[:n_chars]


def cmd_anchors(
    html_path: Path,
    part_re: str = DEFAULT_PART_RE,
    item_re: str = DEFAULT_ITEM_RE,
) -> dict:
    text = clean_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    part_count = sum(1 for _ in re.finditer(part_re, text, re.IGNORECASE | re.MULTILINE))
    item_count = sum(1 for _ in re.finditer(item_re, text, re.IGNORECASE | re.MULTILINE))
    return {
        "part_count": part_count,
        "item_count": item_count,
        # >5 PART matches strongly suggests page-running headers.
        "running_headers_likely": part_count > 5,
    }


def cmd_items(
    html_path: Path,
    item_re: str = DEFAULT_ITEM_RE,
    context: int = 80,
    limit: int | None = None,
) -> list[dict]:
    text = clean_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    out: list[dict] = []
    for i, m in enumerate(re.finditer(item_re, text, re.IGNORECASE | re.MULTILINE)):
        if limit is not None and i >= limit:
            break
        start = max(0, m.start() - context)
        end = min(len(text), m.end() + context)
        out.append({
            "offset": m.start(),
            "text": m.group(0).strip(),
            "context": text[start:end].replace("\n", " "),
        })
    return out


def cmd_find(
    html_path: Path,
    pattern: str,
    literal: bool = False,
    ignore_case: bool = False,
    limit: int | None = None,
    context: int = 40,
) -> list[dict]:
    text = clean_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    flags = re.IGNORECASE if ignore_case else 0
    pat = re.escape(pattern) if literal else pattern
    out: list[dict] = []
    for i, m in enumerate(re.finditer(pat, text, flags)):
        if limit is not None and i >= limit:
            break
        start = max(0, m.start() - context)
        end = min(len(text), m.end() + context)
        out.append({
            "offset": m.start(),
            "match": m.group(0),
            "context": text[start:end].replace("\n", " "),
        })
    return out


def cmd_footers(html_path: Path) -> list[dict]:
    text = clean_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    spans: list[tuple[int, int, str, str]] = []
    for pat in FOOTER_HEURISTICS:
        for m in re.finditer(pat, text):
            spans.append((m.start(), m.end(), m.group(0), pat))
    # Drop any span that overlaps a longer one (longer pattern wins).
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    accepted: list[tuple[int, int, str, str]] = []
    for s in spans:
        if any(not (s[1] <= a[0] or s[0] >= a[1]) for a in accepted):
            continue
        accepted.append(s)
    accepted.sort(key=lambda s: s[0])
    return [{"offset": s[0], "text": s[2], "pattern": s[3]} for s in accepted]


# --- CLI ---

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html_path", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("head")
    p.add_argument("--bytes", type=int, default=2000)

    p = sub.add_parser("clean-head")
    p.add_argument("--chars", type=int, default=2000)

    p = sub.add_parser("anchors")
    p.add_argument("--part-re", default=DEFAULT_PART_RE)
    p.add_argument("--item-re", default=DEFAULT_ITEM_RE)

    p = sub.add_parser("items")
    p.add_argument("--item-re", default=DEFAULT_ITEM_RE)
    p.add_argument("--context", type=int, default=80)
    p.add_argument("--limit", type=int, default=None)

    p = sub.add_parser("find")
    p.add_argument("--pattern", required=True)
    p.add_argument("--literal", action="store_true")
    p.add_argument("--ignore-case", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--context", type=int, default=40)

    sub.add_parser("footers")

    args = ap.parse_args()

    if args.cmd == "head":
        print(cmd_head(args.html_path, args.bytes))
    elif args.cmd == "clean-head":
        print(cmd_clean_head(args.html_path, args.chars))
    elif args.cmd == "anchors":
        print(json.dumps(cmd_anchors(args.html_path, args.part_re, args.item_re), indent=2))
    elif args.cmd == "items":
        for m in cmd_items(args.html_path, args.item_re, args.context, args.limit):
            print(f"@{m['offset']:>8}  {m['text']!r}")
            print(f"          ctx: {m['context']!r}")
    elif args.cmd == "find":
        for m in cmd_find(
            args.html_path, args.pattern, args.literal, args.ignore_case, args.limit, args.context
        ):
            print(f"@{m['offset']:>8}  {m['match']!r}")
            print(f"          ctx: {m['context']!r}")
    elif args.cmd == "footers":
        for m in cmd_footers(args.html_path):
            print(f"@{m['offset']:>8}  {m['text']!r}")


if __name__ == "__main__":
    main()
