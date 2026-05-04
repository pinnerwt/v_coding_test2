"""Eval harness: run extraction over a slate of filings, emit per-filing JSON
and an aggregate CSV (filing, item, status, length, verification flags)."""

import csv
import json
import pathlib

import pytest

from sec_toolbox.eval import EvalSpec, run_eval

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_run_eval_writes_per_filing_json_and_aggregate_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sec_toolbox.status._llm_read",
        lambda t: "reserved" if "Reserved" in t else "substantive",
    )
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    specs = [
        EvalSpec(name="apple_2023", html_path=APPLE, fiscal_year=2023),
    ]
    report = run_eval(specs, out_dir=tmp_path)

    # Per-filing JSON
    apple_json = tmp_path / "apple_2023.json"
    assert apple_json.exists()
    items = json.loads(apple_json.read_text())
    assert any(i["item_number"] == "1" for i in items)

    # Aggregate CSV
    csv_path = tmp_path / "report.csv"
    assert csv_path.exists()
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert {"filing", "item", "status", "length", "issues"} <= rows[0].keys()
    apple_rows = [r for r in rows if r["filing"] == "apple_2023"]
    assert len(apple_rows) >= 22  # 2023 schedule has 22 Items
    assert report.total_items == len(apple_rows)


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_run_eval_records_verification_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sec_toolbox.status._llm_read",
        lambda t: "reserved" if "Reserved" in t else "substantive",
    )
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    specs = [EvalSpec(name="apple_2023", html_path=APPLE, fiscal_year=2023)]
    run_eval(specs, out_dir=tmp_path)
    csv_path = tmp_path / "report.csv"
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    # Apple FY23 should pass schedule check — every row's issues column is empty.
    assert all(r["issues"] == "" for r in rows)
