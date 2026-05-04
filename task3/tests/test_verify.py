"""Self-verification: schedule completeness, char_range roundtrip, XBRL cross-check."""

import pathlib

import pytest

from sec_toolbox.extract import extract
from sec_toolbox.taxonomy import items_for_year
from sec_toolbox.verify import verify_char_ranges, verify_schedule

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"


def _full_items(year: int) -> list[dict]:
    return [{"item_number": i.item_number, "status": "extracted"} for i in items_for_year(year)]


def test_schedule_check_flags_missing_item():
    items = [{"item_number": n, "status": "extracted"} for n in ["1", "1A", "1B", "2", "3", "4"]]
    issues = verify_schedule(items, fiscal_year=2023)
    assert any("missing" in i.message.lower() for i in issues)
    # Specifically, Item 5 (and others past 4) should be flagged.
    flagged = {i.item_number for i in issues}
    assert "5" in flagged
    assert "1C" in flagged  # 2023 schedule includes 1C


def test_schedule_check_no_issues_on_complete_2023():
    items = _full_items(2023)
    assert verify_schedule(items, fiscal_year=2023) == []


def test_schedule_check_no_issues_on_complete_2019():
    items = _full_items(2019)
    assert verify_schedule(items, fiscal_year=2019) == []


def test_schedule_check_2019_does_not_require_1c():
    items = [{"item_number": i.item_number, "status": "extracted"} for i in items_for_year(2019)]
    # Year 2019 has no 1C, so a list without 1C is fine.
    assert verify_schedule(items, fiscal_year=2019) == []


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_char_range_roundtrip_apple(monkeypatch):
    """Each extracted slice's char_range must point at HTML that, when
    re-rendered, contains the slice's content_text (or a generous prefix)."""
    monkeypatch.setattr(
        "sec_toolbox.status._llm_read",
        lambda t: "reserved" if "Reserved" in t else "substantive",
    )
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    html = APPLE.read_bytes()
    items = extract(html, fiscal_year=2023)
    issues = verify_char_ranges(items, html)
    assert issues == [], f"roundtrip failed: {[i.message for i in issues]}"


def test_char_range_roundtrip_flags_bogus_range():
    """A row whose char_range points at unrelated bytes must be flagged."""
    html = b"<html><body><p>Alpha beta gamma delta epsilon zeta eta.</p></body></html>"
    bogus = [
        {
            "item_number": "1",
            "status": "extracted",
            "content_text": "Completely unrelated phrase that does not appear.",
            "char_range": (0, 20),
        }
    ]
    issues = verify_char_ranges(bogus, html)
    assert issues, "expected a roundtrip issue"
    assert "1" in {i.item_number for i in issues}
