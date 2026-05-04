"""Self-verification: schedule completeness, char_range roundtrip, XBRL cross-check."""

from sec_toolbox.taxonomy import items_for_year
from sec_toolbox.verify import verify_schedule


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
