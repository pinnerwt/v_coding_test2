import pathlib

import pytest

from sec_toolbox.extract import extract

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"


def _stub_llm(text: str) -> str:
    """For Apple FY23 short slices: Item 6 is "[Reserved]"; the rest of the
    short slices are short cross-references — call them substantive so they
    land as ``extracted``. The schedule check downstream is what flags any
    real anomalies; this stub just keeps the test deterministic.
    """
    if "[Reserved]" in text or "Reserved" in text:
        return "reserved"
    return "substantive"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_extract_apple_produces_full_schedule(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", _stub_llm)
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    html = APPLE.read_bytes()
    items = extract(html, fiscal_year=2023)
    nums = [i["item_number"] for i in items]
    expected = {
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
    }
    assert set(nums) >= expected, f"missing items: {expected - set(nums)}"

    item6 = next(i for i in items if i["item_number"] == "6")
    assert item6["status"] == "reserved"

    item1 = next(i for i in items if i["item_number"] == "1")
    assert item1["status"] == "extracted"
    assert item1["content_text"]
    assert item1["char_range"][1] > item1["char_range"][0]
    assert item1["part"] == "I"
    assert item1["item_title"] == "Business"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_extract_apple_schema_keys(monkeypatch):
    """Each row must match the brief's schema exactly."""
    monkeypatch.setattr("sec_toolbox.status._llm_read", _stub_llm)
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    html = APPLE.read_bytes()
    items = extract(html, fiscal_year=2023)
    required_keys = {"part", "item_number", "item_title", "content_text", "char_range", "status"}
    for row in items:
        assert required_keys <= row.keys(), f"missing keys: {required_keys - row.keys()}"
        assert isinstance(row["char_range"], tuple)
        assert len(row["char_range"]) == 2
        assert row["status"] in {
            "extracted",
            "incorporated_by_reference",
            "not_applicable",
            "reserved",
        }
