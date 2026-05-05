"""Validation + inspection CLI for per-filing extracted JSON.

Subcommands:
  validate <json> [<json>...]    Run rule-based checks; print findings or "OK".
  validate --all                 Validate every data/extracted/*.json.
  summary  <json>                One-line-per-item summary (longest occurrence
                                 per item_number) — matches the skill's required
                                 step-6 return format.
  inspect  <json> --item N       Show longest-occurrence body for item N
                                 (head/tail) plus char_range and status. Useful
                                 for diagnosing a flag from `validate`.
  report   <json>                Convenience: validate + summary on one file.

This replaces the ad-hoc `uv run python -c "..."` calls the skill used to need
mid-loop. Add a check here when a new failure mode is discovered, so the next
filing's run picks it up automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PAGE_FOOTER_RE = re.compile(r"\|\s*\d+\s*$")
TRAILING_PAGENUM_RE = re.compile(r"\n\s*\d{1,4}\s*$")
NUMERIC_ONLY_RE = re.compile(r"^\s*\d{1,4}\s*$")

EXPECTED_ITEMS = {
    "1", "1A", "1B", "2", "3", "4",
    "5", "7", "7A", "8", "9A", "9B",
    "10", "11", "12", "13", "14", "15",
}

# Stable display order. Items not in here are appended at the end in lexical order.
ITEM_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14",
    "15", "16",
]


def validate(items: list[dict]) -> list[str]:
    findings: list[str] = []

    # One record per item_number after dedup; duplicates here mean the extractor
    # forgot to drop TOC stubs.
    seen: set[str] = set()
    duplicates: list[str] = []
    for it in items:
        n = it["item_number"]
        if n in seen:
            duplicates.append(n)
        seen.add(n)
    if duplicates:
        findings.append(f"duplicate item records (TOC stubs not dropped?): {sorted(set(duplicates))}")

    missing_items = sorted(EXPECTED_ITEMS - seen)
    if missing_items:
        findings.append(f"missing required items: {missing_items}")

    parts = {it["part"] for it in items if it["part"]}
    missing = sorted({"I", "II", "III", "IV"} - parts)
    if missing:
        findings.append(f"missing parts: {missing}")

    # Per-record checks. Skip checks that would false-fire on TOC stubs (TOC
    # records have page-number bodies by construction, since slicing between
    # adjacent TOC item lines yields just the printed page number).
    for it in items:
        title = it["item_title"]
        body = it["content_text"].strip()
        norm = body.lower().rstrip(".").strip()

        if it["status"] == "extracted" and norm in {"none", "n/a"}:
            findings.append(
                f"Part {it['part']} Item {it['item_number']} body='{body[:30]}' tagged extracted but is N/A"
            )
        if "[Reserved]" in title and it["status"] != "reserved":
            findings.append(
                f"Part {it['part']} Item {it['item_number']} title says [Reserved] but status={it['status']}"
            )

    # Char_range monotonicity (every record).
    last_end = -1
    for it in items:
        s, e = it["char_range"]
        if s < last_end:
            findings.append(
                f"Part {it['part']} Item {it['item_number']} char_range start {s} < previous end {last_end}"
            )
        last_end = max(last_end, e)

    # Per-item-number checks: apply only to the LONGEST occurrence (the body
    # record). Avoids spurious flags on TOC stubs whose bodies are page numbers.
    by_item: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_item[it["item_number"]].append(it)
    for num, group in by_item.items():
        biggest = max(group, key=lambda it: len(it["content_text"]))
        title = biggest["item_title"]
        body = biggest["content_text"].strip()

        if biggest["status"] == "extracted" and len(body) < 50:
            findings.append(
                f"Item {num}: largest occurrence is only {len(body)} chars (body slice may be wrong)"
            )
        # Bare "Reserved" title (no brackets), e.g. JPM "Item 6. Reserved".
        if title.strip().lower() == "reserved" and biggest["status"] != "reserved":
            findings.append(
                f"Part {biggest['part']} Item {num} bare 'Reserved' title but status={biggest['status']} (body={body[:30]!r})"
            )
        # Body of the canonical record is JUST a page number — extraction lost
        # the actual content (or the item legitimately has none and should be
        # reserved/not_applicable, not extracted).
        if body and NUMERIC_ONLY_RE.match(body) and biggest["status"] == "extracted":
            findings.append(
                f"Part {biggest['part']} Item {num} body is page-number only ({body!r}) but status=extracted"
            )
        # Short canonical body with company-bar / Form 10-K footer leak.
        if len(body) < 200 and ("Form 10-K" in body or PAGE_FOOTER_RE.search(body)):
            findings.append(
                f"Part {biggest['part']} Item {num} short body looks like page-footer leak: {body[:80]!r}"
            )
        # Short canonical body with a trailing standalone page number.
        if 0 < len(body) < 200 and TRAILING_PAGENUM_RE.search(body):
            findings.append(
                f"Part {biggest['part']} Item {num} trailing page-number leak in body: {body[-40:]!r}"
            )

    return findings


def summary_lines(items: list[dict]) -> list[str]:
    """One line per item_number, longest-occurrence record."""
    by_item: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_item[it["item_number"]].append(it)

    def order_key(num: str) -> tuple[int, str]:
        try:
            return (ITEM_ORDER.index(num), "")
        except ValueError:
            return (len(ITEM_ORDER), num)

    out = []
    for num in sorted(by_item, key=order_key):
        big = max(by_item[num], key=lambda it: len(it["content_text"]))
        title = big["item_title"]
        out.append(
            f"Part {big['part']} Item {num} [{big['status']}] -- {title} ({len(big['content_text'])} chars)"
        )
    return out


def inspect_item(items: list[dict], item_number: str, head: int, tail: int) -> str:
    matches = [it for it in items if it["item_number"] == item_number]
    if not matches:
        return f"item {item_number!r} not found"
    big = max(matches, key=lambda it: len(it["content_text"]))
    body = big["content_text"]
    out = [
        f"Part {big['part']} Item {item_number} [{big['status']}] -- {big['item_title']}",
        f"  occurrences: {len(matches)}  longest_chars: {len(body)}  char_range: {big['char_range']}",
        f"  head ({min(head, len(body))}c): {body[:head]!r}",
    ]
    if len(body) > head + tail:
        out.append(f"  tail ({tail}c): {body[-tail:]!r}")
    return "\n".join(out)


def _print_findings(path: Path, items: list[dict], findings: list[str]) -> None:
    parts = sorted({it["part"] for it in items if it["part"]})
    print(f"\n=== {path.name} (items={len(items)} parts={parts}) ===")
    if not findings:
        print("  OK")
    else:
        for f in findings:
            print(f"  - {f}")


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="Rule-based checks on one or more JSON files.")
    p.add_argument("paths", nargs="*", type=Path)
    p.add_argument("--all", action="store_true",
                   help="Validate every data/extracted/*.json instead of named paths.")

    p = sub.add_parser("summary", help="One-line-per-item summary (longest occurrence).")
    p.add_argument("path", type=Path)

    p = sub.add_parser("inspect", help="Show longest-occurrence body for a single item.")
    p.add_argument("path", type=Path)
    p.add_argument("--item", required=True, help="Item number, e.g. '1A', '7A', '9C'.")
    p.add_argument("--head", type=int, default=200)
    p.add_argument("--tail", type=int, default=80)

    p = sub.add_parser("report", help="validate + summary on one file.")
    p.add_argument("path", type=Path)

    args = ap.parse_args()

    if args.cmd == "validate":
        if args.all:
            paths = sorted(Path("data/extracted").glob("*.json"))
        else:
            paths = args.paths
        if not paths:
            print("validate: no paths and --all not set", file=sys.stderr)
            sys.exit(2)
        any_findings = False
        for path in paths:
            items = _load(path)
            findings = validate(items)
            _print_findings(path, items, findings)
            if findings:
                any_findings = True
        sys.exit(1 if any_findings else 0)

    if args.cmd == "summary":
        items = _load(args.path)
        for line in summary_lines(items):
            print(line)
        return

    if args.cmd == "inspect":
        items = _load(args.path)
        print(inspect_item(items, args.item, args.head, args.tail))
        return

    if args.cmd == "report":
        items = _load(args.path)
        findings = validate(items)
        _print_findings(args.path, items, findings)
        print()
        for line in summary_lines(items):
            print(line)
        return


if __name__ == "__main__":
    main()
