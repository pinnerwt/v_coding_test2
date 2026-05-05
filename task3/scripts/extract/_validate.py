"""Validation pass for per-filing extracted JSON. Reads all data/extracted/*.json
and prints one bullet list of findings per filing."""

from __future__ import annotations

import json
import re
from pathlib import Path

PAGE_FOOTER_RE = re.compile(r"\|\s*\d+\s*$")


def validate(items: list[dict]) -> list[str]:
    findings: list[str] = []
    # Each ITEM heading occurrence is its own record, so a filing with TOC + body has
    # ~2 records per item (≈ 40-50 records). What matters is unique item coverage.
    unique_items = sorted({it["item_number"] for it in items})
    expected_items = {"1", "1A", "1B", "2", "3", "4",
                      "5", "7", "7A", "8", "9A", "9B",
                      "10", "11", "12", "13", "14", "15"}
    missing_items = sorted(expected_items - set(unique_items))
    if missing_items:
        findings.append(f"missing required items: {missing_items}")
    parts = sorted({it["part"] for it in items if it["part"]})
    expected = {"I", "II", "III", "IV"}
    missing = sorted(expected - set(parts))
    if missing:
        findings.append(f"missing parts: {missing}")

    for it in items:
        body = it["content_text"].strip()
        norm = body.lower().rstrip(".").strip()
        if it["status"] == "extracted" and norm in {"none", "n/a"}:
            findings.append(
                f"Part {it['part']} Item {it['item_number']} body='{body[:30]}' tagged extracted but is N/A"
            )
        if "[Reserved]" in it["item_title"] and it["status"] != "reserved":
            findings.append(
                f"Part {it['part']} Item {it['item_number']} title says [Reserved] but status={it['status']}"
            )
        if len(body) < 200 and ("Form 10-K" in body or PAGE_FOOTER_RE.search(body)):
            findings.append(
                f"Part {it['part']} Item {it['item_number']} short body looks like page-footer leak: {body[:80]!r}"
            )

    # Char range monotonicity
    last_end = -1
    for it in items:
        s, e = it["char_range"]
        if s < last_end:
            findings.append(
                f"Part {it['part']} Item {it['item_number']} char_range start {s} < previous end {last_end}"
            )
        last_end = max(last_end, e)

    # Items present multiple times: each occurrence's body should be reasonable
    # for what it represents (TOC stub = small, body = larger). For each item_number
    # group, flag if the LARGEST occurrence is < 50 chars (means body wasn't found).
    by_item: dict[str, list[dict]] = {}
    for it in items:
        by_item.setdefault(it["item_number"], []).append(it)
    for num, group in by_item.items():
        biggest = max(group, key=lambda it: len(it["content_text"]))
        # Allow short legitimate items: not_applicable, reserved, incorporated_by_reference
        if biggest["status"] == "extracted" and len(biggest["content_text"]) < 50:
            findings.append(
                f"Item {num}: largest occurrence is only {len(biggest['content_text'])} chars (body slice may be wrong)"
            )

    return findings


def main() -> None:
    base = Path("data/extracted")
    for path in sorted(base.glob("*.json")):
        items = json.loads(path.read_text())
        findings = validate(items)
        parts = sorted({it["part"] for it in items if it["part"]})
        print(f"\n=== {path.name} (items={len(items)} parts={parts}) ===")
        if not findings:
            print("  OK")
        else:
            for f in findings:
                print(f"  - {f}")


if __name__ == "__main__":
    main()
