"""Tests for scripts/extract/_probe.py — diagnostic CLI for new 10-K filings."""

from __future__ import annotations

import importlib.util
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "probe_synthetic.htm"


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe", Path(__file__).parent.parent / "scripts" / "extract" / "_probe.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_head_returns_first_n_bytes_of_raw_html():
    probe = _load_probe()
    out = probe.cmd_head(FIXTURE, n_bytes=80)
    assert out.startswith("<html xmlns:ix=")
    assert len(out) <= 80


def test_clean_head_returns_first_n_chars_of_cleaned_text():
    probe = _load_probe()
    out = probe.cmd_clean_head(FIXTURE, n_chars=200)
    # ix:hidden content must be stripped.
    assert "SECRET-XBRL-NUMBER" not in out
    # block tags become newlines, so "PART I" appears on its own line near the start.
    assert "PART I" in out


def test_anchors_counts_part_and_item_matches_and_flags_running_headers():
    probe = _load_probe()
    res = probe.cmd_anchors(FIXTURE)
    # Six "PART I" divs across pages — running-header territory.
    assert res["part_count"] == 6
    # Item 1, Item 1A, Item 60 — the default ITEM_RE filters Item 60 out (>16).
    assert res["item_count"] == 2
    assert res["running_headers_likely"] is True


def test_anchors_respects_item_re_override():
    probe = _load_probe()
    # Loose regex: allow any digits, no upper bound.
    res = probe.cmd_anchors(FIXTURE, item_re=r"^\s*ITEM\s+(\d+)([A-C])?\s*\.\s*")
    assert res["item_count"] == 3  # now Item 60 also matches


def test_items_lists_each_match_with_offset_and_context():
    probe = _load_probe()
    res = probe.cmd_items(FIXTURE, context=40)
    titles = [m["text"] for m in res]
    assert any("Item 1." in t for t in titles)
    assert any("Item 1A" in t for t in titles)
    # Each entry has integer offset and context string.
    for m in res:
        assert isinstance(m["offset"], int)
        assert isinstance(m["context"], str)
        # context is at most 2*ctx + match length; allow generous upper bound.
        assert len(m["context"]) <= 2 * 40 + 200


def test_find_literal_finds_keyword_offsets():
    probe = _load_probe()
    res = probe.cmd_find(FIXTURE, pattern="BUSINESS", literal=True, ignore_case=False)
    # Exactly one ALL-CAPS BUSINESS heading; "Business" (mixed case) is not matched.
    assert len(res) == 1
    assert isinstance(res[0]["offset"], int)


def test_find_literal_ignore_case_matches_both_cases():
    probe = _load_probe()
    res = probe.cmd_find(FIXTURE, pattern="BUSINESS", literal=True, ignore_case=True)
    assert len(res) == 2  # "Business" and "BUSINESS"


def test_find_regex_with_ignore_case():
    probe = _load_probe()
    res = probe.cmd_find(FIXTURE, pattern=r"acme", literal=False, ignore_case=True)
    # "Acme" appears in many places.
    assert len(res) >= 4


def test_footers_detects_company_pipe_year_pattern():
    probe = _load_probe()
    res = probe.cmd_footers(FIXTURE)
    # Six "Acme Inc. | 2023 Form 10-K | <n>" footers; overlapping shorter
    # heuristic must not double-count the same span.
    assert len(res) == 6
    for m in res:
        assert "Form 10-K" in m["text"]
        assert "Acme" in m["text"]  # longer pattern wins
