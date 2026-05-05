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
  compare-golden <actual> <golden>
                                 Diff actual JSON's per-item span layout against
                                 a hand-labelled golden (task3/eval/*.golden.json).
                                 Used to test Phase 5b status-splitting output.

Multi-record-per-item is allowed when a single Item is split into adjacent
sub-records of differing status (e.g. JPM Item 10 = inline executive officers
[extracted] + IBR sentence + Code of Conduct [extracted]). The duplicates check
only fires on records whose char_range overlap, which would indicate the
extractor failed to drop a TOC stub.
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
    "1",
    "1A",
    "1B",
    "2",
    "3",
    "4",
    "5",
    "7",
    "7A",
    "8",
    "9A",
    "9B",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
}

# Stable display order. Items not in here are appended at the end in lexical order.
ITEM_ORDER = [
    "1",
    "1A",
    "1B",
    "1C",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "7A",
    "8",
    "9",
    "9A",
    "9B",
    "9C",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
]


def validate(items: list[dict]) -> list[str]:
    findings: list[str] = []

    # Multiple records per item are legal when they're adjacent sub-records from
    # Phase 5b status-splitting. Overlapping char_range with the same item_number
    # is still an error — that indicates the rule extractor failed to drop a TOC
    # stub (or a sub-record was emitted with the wrong bounds).
    by_num: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_num[it["item_number"]].append(it)
    seen = set(by_num.keys())
    overlaps: list[str] = []
    for num, group in by_num.items():
        if len(group) < 2:
            continue
        ranges = sorted((it["char_range"][0], it["char_range"][1]) for it in group)
        for (_s1, e1), (s2, _e2) in zip(ranges, ranges[1:], strict=False):
            if s2 < e1:
                overlaps.append(num)
                break
    if overlaps:
        findings.append(
            f"overlapping records for same item (TOC stub not dropped?): {sorted(set(overlaps))}"
        )

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
                f"Part {it['part']} Item {it['item_number']} body='{body[:30]}' "
                f"tagged extracted but is N/A"
            )
        if "[Reserved]" in title and it["status"] != "reserved":
            findings.append(
                f"Part {it['part']} Item {it['item_number']} title says [Reserved] "
                f"but status={it['status']}"
            )

    # Char_range monotonicity (every record).
    last_end = -1
    for it in items:
        s, e = it["char_range"]
        if s < last_end:
            findings.append(
                f"Part {it['part']} Item {it['item_number']} "
                f"char_range start {s} < previous end {last_end}"
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
                f"Item {num}: largest occurrence is only {len(body)} chars "
                f"(body slice may be wrong)"
            )
        # Bare "Reserved" title (no brackets), e.g. JPM "Item 6. Reserved".
        if title.strip().lower() == "reserved" and biggest["status"] != "reserved":
            findings.append(
                f"Part {biggest['part']} Item {num} bare 'Reserved' title "
                f"but status={biggest['status']} (body={body[:30]!r})"
            )
        # Body of the canonical record is JUST a page number — extraction lost
        # the actual content (or the item legitimately has none and should be
        # reserved/not_applicable, not extracted).
        if body and NUMERIC_ONLY_RE.match(body) and biggest["status"] == "extracted":
            findings.append(
                f"Part {biggest['part']} Item {num} body is page-number only "
                f"({body!r}) but status=extracted"
            )
        # Short canonical body with company-bar / Form 10-K footer leak.
        if len(body) < 200 and ("Form 10-K" in body or PAGE_FOOTER_RE.search(body)):
            findings.append(
                f"Part {biggest['part']} Item {num} short body looks like "
                f"page-footer leak: {body[:80]!r}"
            )
        # Short canonical body with a trailing standalone page number.
        if 0 < len(body) < 200 and TRAILING_PAGENUM_RE.search(body):
            findings.append(
                f"Part {biggest['part']} Item {num} trailing page-number leak "
                f"in body: {body[-40:]!r}"
            )

    return findings


def summary_lines(items: list[dict]) -> list[str]:
    """One line per record, ordered by char_range. Items split into sub-records
    by Phase 5b emit multiple consecutive lines sharing the same item_number;
    each line shows that sub-record's status and length."""
    out = []
    for it in sorted(items, key=lambda r: r["char_range"][0]):
        title = it["item_title"]
        out.append(
            f"Part {it['part']} Item {it['item_number']} [{it['status']}] "
            f"-- {title} ({len(it['content_text'])} chars)"
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
        (
            f"  occurrences: {len(matches)}  longest_chars: {len(body)}  "
            f"char_range: {big['char_range']}"
        ),
        f"  head ({min(head, len(body))}c): {body[:head]!r}",
    ]
    if len(body) > head + tail:
        out.append(f"  tail ({tail}c): {body[-tail:]!r}")
    return "\n".join(out)


def compare_golden(actual: list[dict], golden: dict) -> list[str]:
    """Compare actual JSON's per-item span layout against a hand-labelled golden.

    Golden schema (per item entry):
      item_number: str
      expected_spans: list of {body_start, body_end, status}, body-relative
                      offsets within the rule-extracted item's content_text.

    Actual sub-records' bounds are converted back to body-relative by
    subtracting the per-item parent envelope start (= min char_range[0] across
    all sub-records of that item_number). Tolerance for boundary drift is
    `boundary_tolerance_chars` (default 20) — any sub-record boundary within
    that window of an expected boundary counts as a match.
    """
    findings: list[str] = []
    tol = int(golden.get("boundary_tolerance_chars", 20))

    by_num: dict[str, list[dict]] = defaultdict(list)
    for it in actual:
        by_num[it["item_number"]].append(it)

    for entry in golden.get("items", []):
        num = entry["item_number"]
        expected = entry["expected_spans"]
        if num not in by_num:
            findings.append(f"Item {num}: missing from actual JSON")
            continue
        subs = sorted(by_num[num], key=lambda it: it["char_range"][0])
        envelope_start = subs[0]["char_range"][0]
        actual_spans = [
            {
                "body_start": it["char_range"][0] - envelope_start,
                "body_end": it["char_range"][1] - envelope_start,
                "status": it["status"],
            }
            for it in subs
        ]
        if len(actual_spans) != len(expected):
            findings.append(
                f"Item {num}: expected {len(expected)} sub-record(s), got {len(actual_spans)}"
            )
            exp_layout = [(s["body_start"], s["body_end"], s["status"]) for s in expected]
            act_layout = [(s["body_start"], s["body_end"], s["status"]) for s in actual_spans]
            findings.append(f"  expected: {exp_layout}")
            findings.append(f"  actual:   {act_layout}")
            continue
        for i, (exp, act) in enumerate(zip(expected, actual_spans, strict=False)):
            if exp["status"] != act["status"]:
                findings.append(
                    f"Item {num} span[{i}]: expected status={exp['status']!r}, "
                    f"got {act['status']!r}"
                )
            if abs(exp["body_start"] - act["body_start"]) > tol:
                findings.append(
                    f"Item {num} span[{i}]: body_start drift {act['body_start']} "
                    f"vs expected {exp['body_start']} (tol={tol})"
                )
            if abs(exp["body_end"] - act["body_end"]) > tol:
                findings.append(
                    f"Item {num} span[{i}]: body_end drift {act['body_end']} "
                    f"vs expected {exp['body_end']} (tol={tol})"
                )
    return findings


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
    p.add_argument(
        "--all",
        action="store_true",
        help="Validate every data/extracted/*.json instead of named paths.",
    )

    p = sub.add_parser("summary", help="One-line-per-item summary (longest occurrence).")
    p.add_argument("path", type=Path)

    p = sub.add_parser("inspect", help="Show longest-occurrence body for a single item.")
    p.add_argument("path", type=Path)
    p.add_argument("--item", required=True, help="Item number, e.g. '1A', '7A', '9C'.")
    p.add_argument("--head", type=int, default=200)
    p.add_argument("--tail", type=int, default=80)

    p = sub.add_parser("report", help="validate + summary on one file.")
    p.add_argument("path", type=Path)

    p = sub.add_parser(
        "compare-golden",
        help="Diff actual JSON against a hand-labelled golden span layout.",
    )
    p.add_argument("actual", type=Path)
    p.add_argument("golden", type=Path)

    args = ap.parse_args()

    if args.cmd == "validate":
        paths = sorted(Path("data/extracted").glob("*.json")) if args.all else args.paths
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

    if args.cmd == "compare-golden":
        actual = _load(args.actual)
        golden = json.loads(args.golden.read_text())
        findings = compare_golden(actual, golden)
        print(f"\n=== compare-golden: {args.actual.name} vs {args.golden.name} ===")
        if not findings:
            print("  GOLDEN MATCH")
        else:
            for f in findings:
                print(f"  - {f}")
        sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
