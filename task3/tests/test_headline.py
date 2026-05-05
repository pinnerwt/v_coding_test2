"""Unit tests for eval/headline.py:aggregate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("headline", REPO / "eval" / "headline.py")
_headline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_headline)
aggregate = _headline.aggregate


def test_aggregate_rolls_up_three_sources():
    modern = [
        {"label": "320193-...", "n_records": 23},
        {"label": "789019-...", "n_records": 22},
    ]
    famous = [
        {"label": "Berkshire 2008", "status": "max_steps_exceeded", "n_records": 0,
         "cost_usd": 0.07, "elapsed": 121.1},
        {"label": "AIG 2007", "status": "done", "n_records": 20,
         "cost_usd": 0.03, "elapsed": 39.5},
    ]
    cat_e = [
        {"label": "IBM 1995", "status": "done", "n_records": 14,
         "cost_usd": 0.058, "elapsed": 54.8},
        {"label": "GE 1995", "status": "max_steps_exceeded", "n_records": 0,
         "cost_usd": 0.072, "elapsed": 56.0},
    ]
    result = aggregate(modern, famous, cat_e)

    assert result["sources"]["regression"]["filings_total"] == 2
    assert result["sources"]["regression"]["filings_done"] == 2
    assert result["sources"]["regression"]["items_emitted"] == 45

    assert result["sources"]["famous"]["filings_total"] == 2
    assert result["sources"]["famous"]["filings_done"] == 1
    assert result["sources"]["famous"]["items_emitted"] == 20

    assert result["sources"]["cat_e"]["filings_total"] == 2
    assert result["sources"]["cat_e"]["filings_done"] == 1
    assert result["sources"]["cat_e"]["items_emitted"] == 14

    assert result["overall"]["filings_total"] == 6
    assert result["overall"]["filings_done"] == 4
    assert result["overall"]["items_emitted"] == 79


def test_aggregate_medians_skip_zero_record_failures():
    famous = [
        {"label": "ok1", "status": "done", "n_records": 16, "cost_usd": 0.03, "elapsed": 40.0},
        {"label": "ok2", "status": "done", "n_records": 16, "cost_usd": 0.05, "elapsed": 50.0},
        {"label": "fail", "status": "max_steps_exceeded", "n_records": 0,
         "cost_usd": 0.10, "elapsed": 120.0},
    ]
    result = aggregate([], famous, [])
    f = result["sources"]["famous"]
    assert f["cost_median_done"] == 0.04
    assert f["latency_median_done"] == 45.0
