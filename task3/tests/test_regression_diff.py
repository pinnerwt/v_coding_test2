"""Unit tests for eval/regression.py:_diff_filing item-level accuracy counts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("regression", REPO / "eval" / "regression.py")
_regression = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_regression)
_diff_filing = _regression._diff_filing


def _rec(
    item_no: str,
    status: str = "extracted",
    body: str = "x" * 100,
    char_range=(0, 100),
) -> dict:
    return {
        "part": "I",
        "item_number": item_no,
        "item_title": f"Item {item_no}",
        "content_text": body,
        "char_range": list(char_range),
        "status": status,
    }


def test_diff_clean_match_returns_full_counts():
    legacy = [_rec("1"), _rec("2")]
    current = [_rec("1"), _rec("2")]
    result = _diff_filing(legacy, current)
    # New return shape: (hard, soft, expected, counts)
    assert isinstance(result, tuple) and len(result) == 4
    hard, soft, expected, counts = result
    assert hard == [] and soft == [] and expected == []
    assert counts["total_items"] == 2
    assert counts["status_match_strict"] == 2
    assert counts["status_match_with_overrides"] == 2
    assert counts["body_within_5pct"] == 2


def test_diff_status_flip_drops_strict_match_count():
    legacy = [_rec("1", status="extracted"), _rec("2", status="not_applicable")]
    current = [_rec("1", status="extracted"), _rec("2", status="incorporated_by_reference")]
    _, _, _, counts = _diff_filing(legacy, current)
    assert counts["total_items"] == 2
    assert counts["status_match_strict"] == 1
    assert counts["status_match_with_overrides"] == 1
    assert counts["body_within_5pct"] == 2


def test_diff_tolerated_flip_credits_overrides_only():
    legacy = [_rec("9C", status="incorporated_by_reference")]
    current = [_rec("9C", status="not_applicable")]
    override = {"items": {"9C": {"tolerate": ["status_flip"], "reason": "documented"}}}
    _, _, expected, counts = _diff_filing(legacy, current, override=override)
    assert expected and "tolerated" in expected[0]
    assert counts["total_items"] == 1
    assert counts["status_match_strict"] == 0
    assert counts["status_match_with_overrides"] == 1


def test_diff_body_len_drift_drops_body_match():
    legacy = [_rec("1", body="x" * 1000)]
    current = [_rec("1", body="x" * 800)]  # 20% short — beyond SOFT_LEN_THRESHOLD
    _, soft, _, counts = _diff_filing(legacy, current)
    assert soft and "body-len drift" in soft[0]
    assert counts["body_within_5pct"] == 0
    assert counts["status_match_strict"] == 1
