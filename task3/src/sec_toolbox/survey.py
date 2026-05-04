from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sec_toolbox.fetch import Fetcher


@dataclass(frozen=True)
class SlateEntry:
    category: str
    name: str
    cik: str
    target: int | str  # year or "recent"


SLATE: list[SlateEntry] = [
    # A — modern inline-XBRL
    SlateEntry("A", "Apple", "320193", 2023),
    SlateEntry("A", "Microsoft", "789019", 2023),
    SlateEntry("A", "NVIDIA", "1045810", 2024),
    # B — heavy IBR
    SlateEntry("B", "Berkshire Hathaway", "1067983", "recent"),
    SlateEntry("B", "JPMorgan Chase", "19617", "recent"),
    SlateEntry("B", "ExxonMobil", "34088", "recent"),
    # C — older HTML, pre-XBRL
    SlateEntry("C", "IBM", "51143", 2004),
    SlateEntry("C", "General Electric", "40545", 2004),
    SlateEntry("C", "Coca-Cola", "21344", 2004),
    # D — small-cap / recent IPO
    SlateEntry("D", "Palantir", "1321655", "recent"),
    SlateEntry("D", "Reddit", "1834584", 2024),
    SlateEntry("D", "Rivian", "1874178", "recent"),
    # E — older plain-text
    SlateEntry("E", "IBM", "51143", 1995),
    SlateEntry("E", "General Electric", "40545", 1995),
    SlateEntry("E", "Microsoft", "789019", 1995),
]


def pick_filing(submissions_json: dict[str, Any], target: int | str) -> dict[str, str] | None:
    recent = submissions_json.get("filings", {}).get("recent", {})
    accs = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    forms = recent.get("form") or []
    docs = recent.get("primaryDocument") or []
    rows = [
        {"accession": a, "date": d, "form": f, "primary_doc": p}
        for a, d, f, p in zip(accs, dates, forms, docs, strict=False)
        if f == "10-K"
    ]
    if not rows:
        return None
    if target == "recent":
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows[0]
    target_year = int(target)
    rows.sort(key=lambda r: abs(int(r["date"][:4]) - target_year))
    return rows[0]


def sniff_kind(body: bytes, content_type: str) -> str:
    head = body[:4096].lower()
    if b"inlinexbrl" in head or b"xmlns:ix" in head:
        return "inline_xbrl"
    if content_type.startswith("text/plain") or head.startswith(b"<sec-document>"):
        return "plain_text"
    if b"<html" in head or content_type.startswith("text/html"):
        return "html"
    return "unknown"


def run_survey(*, fetcher: Fetcher, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "report.csv"
    md_path = out_dir / "report.md"

    rows: list[dict[str, Any]] = []
    for s in SLATE:
        try:
            sub_entry = fetcher.submissions(cik=s.cik)
            sub_json = json.loads(sub_entry.path.read_text())
            pick = pick_filing(sub_json, s.target)
            if pick is None:
                rows.append(
                    {
                        "category": s.category,
                        "name": s.name,
                        "cik": s.cik,
                        "accession": "",
                        "filing_date": "",
                        "primary_doc": "",
                        "content_type": "",
                        "ext": "",
                        "bytes": 0,
                        "sniffed_kind": "no-10k-found",
                    }
                )
                continue
            arc = fetcher.archive(
                cik=s.cik,
                accession=pick["accession"],
                filename=pick["primary_doc"],
            )
            body = arc.path.read_bytes()
            rows.append(
                {
                    "category": s.category,
                    "name": s.name,
                    "cik": s.cik,
                    "accession": pick["accession"],
                    "filing_date": pick["date"],
                    "primary_doc": pick["primary_doc"],
                    "content_type": arc.content_type,
                    "ext": arc.ext,
                    "bytes": arc.bytes,
                    "sniffed_kind": sniff_kind(body, arc.content_type),
                }
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                {
                    "category": s.category,
                    "name": s.name,
                    "cik": s.cik,
                    "accession": "",
                    "filing_date": "",
                    "primary_doc": "",
                    "content_type": "",
                    "ext": "",
                    "bytes": 0,
                    "sniffed_kind": f"error: {type(e).__name__}: {e}",
                }
            )

    fieldnames = [
        "category",
        "name",
        "cik",
        "accession",
        "filing_date",
        "primary_doc",
        "content_type",
        "ext",
        "bytes",
        "sniffed_kind",
    ]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    md_lines = ["# Task 3 — SEC 10-K survey", ""]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    cat_titles = {
        "A": "A. Modern inline-XBRL",
        "B": "B. Heavy 'incorporated by reference'",
        "C": "C. Older HTML, pre-XBRL",
        "D": "D. Small-cap / recent IPO",
        "E": "E. Older plain-text",
    }
    for cat in sorted(by_cat):
        md_lines.append(f"## {cat_titles.get(cat, cat)}")
        md_lines.append("")
        header = (
            "| Filer | CIK | Accession | Filing date | Primary doc | "
            "Content-Type | Ext | Bytes | Sniffed |"
        )
        md_lines.append(header)
        md_lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in by_cat[cat]:
            row = (
                f"| {r['name']} | {r['cik']} | {r['accession']} | {r['filing_date']} | "
                f"{r['primary_doc']} | {r['content_type']} | {r['ext']} | "
                f"{r['bytes']} | {r['sniffed_kind']} |"
            )
            md_lines.append(row)
        md_lines.append("")
    md_path.write_text("\n".join(md_lines))
