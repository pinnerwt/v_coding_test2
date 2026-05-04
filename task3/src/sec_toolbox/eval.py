"""Eval harness: run extraction over a slate of filings and write per-filing
JSON plus an aggregate CSV (filing, item, status, length, issues).

The harness is the regression bar — extending the slate or noticing a new
failure mode happens here, before the underlying code is touched.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extract import extract
from .verify import verify_schedule


@dataclass(frozen=True)
class EvalSpec:
    name: str
    html_path: Path
    fiscal_year: int


@dataclass
class EvalReport:
    total_items: int
    rows: list[dict[str, Any]]


_AGGREGATE_FIELDS = ["filing", "item", "status", "length", "issues"]


def run_eval(specs: list[EvalSpec], out_dir: Path) -> EvalReport:
    """Run extraction for each spec, write per-filing JSON and a single
    aggregate CSV, and return an in-memory summary.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        html = spec.html_path.read_bytes()
        items = extract(html, fiscal_year=spec.fiscal_year)
        # Per-filing JSON dump
        (out_dir / f"{spec.name}.json").write_text(
            json.dumps([_jsonable(i) for i in items], indent=2)
        )
        issues = verify_schedule(items, fiscal_year=spec.fiscal_year)
        issues_by_item: dict[str | None, list[str]] = {}
        for issue in issues:
            issues_by_item.setdefault(issue.item_number, []).append(issue.message)
        for it in items:
            num = it.get("item_number")
            row_issues = issues_by_item.get(num, [])
            rows.append(
                {
                    "filing": spec.name,
                    "item": num or "",
                    "status": it.get("status", ""),
                    "length": len(it.get("content_text", "")),
                    "issues": "; ".join(row_issues),
                }
            )
        # Schedule-level issues for items that are missing entirely (no row).
        present_nums = {r["item"] for r in rows if r["filing"] == spec.name}
        for issue in issues:
            if issue.item_number and issue.item_number not in present_nums:
                rows.append(
                    {
                        "filing": spec.name,
                        "item": issue.item_number,
                        "status": "missing",
                        "length": 0,
                        "issues": issue.message,
                    }
                )

    csv_path = out_dir / "report.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_AGGREGATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return EvalReport(total_items=len(rows), rows=rows)


def _jsonable(item: dict[str, Any]) -> dict[str, Any]:
    """char_range is a tuple in-memory but JSON has no tuples; serialise as list."""
    out = dict(item)
    cr = out.get("char_range")
    if isinstance(cr, tuple):
        out["char_range"] = list(cr)
    return out
