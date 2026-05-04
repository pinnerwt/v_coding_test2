import pathlib

import pytest

from sec_toolbox.extract import extract

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"
IBM = FIXTURES / "51143/000155837020001334/ibm-20191231x10k2af531.htm"
EXXON = FIXTURES / "34088/000003408826000045/xom-20251231.htm"


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


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_extract_ibm_2019(monkeypatch):
    """IBM FY19 (pre-2021): Item 6 is 'Selected Financial Data' and substantive,
    Item 1A 'Risk Factors' must resolve to a real body slice. There's no 1C in
    2019. Stub the LLM to label short slices as substantive (no IBR for IBM)."""
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda t: "substantive")
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    html = IBM.read_bytes()
    items = extract(html, fiscal_year=2019)
    by_num = {i["item_number"]: i for i in items if i["item_number"]}

    # Pre-2023 schedule: no 1C, no 9C
    assert "1C" not in by_num
    assert "9C" not in by_num

    item1a = by_num.get("1A")
    assert item1a is not None, "Item 1A missing"
    assert item1a["item_title"] == "Risk Factors"
    assert item1a["status"] == "extracted"
    assert item1a["content_text"]
    assert item1a["char_range"][1] > item1a["char_range"][0]

    item6 = by_num.get("6")
    assert item6 is not None, "Item 6 missing"
    assert item6["status"] == "extracted"
    assert "Selected Financial Data" in item6["item_title"]


@pytest.mark.skipif(not EXXON.exists(), reason="Exxon fixture not cached")
def test_extract_exxon_2025_part_iii_is_ibr(monkeypatch):
    """ExxonMobil FY25 incorporates Items 10–14 by reference to the proxy.
    Stub the LLM to recognise the IBR phrasing and substantive otherwise."""

    def fake_llm(text: str) -> str:
        lowered = text.lower()
        if "incorporated" in lowered and "reference" in lowered:
            return "incorporated_by_reference"
        if "[reserved]" in lowered or text.strip().lower() == "reserved":
            return "reserved"
        return "substantive"

    monkeypatch.setattr("sec_toolbox.status._llm_read", fake_llm)
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    html = EXXON.read_bytes()
    items = extract(html, fiscal_year=2025)
    by_num = {i["item_number"]: i for i in items if i["item_number"]}

    for num in ["10", "11", "12", "13", "14"]:
        row = by_num.get(num)
        assert row is not None, f"Item {num} missing"
        assert row["status"] == "incorporated_by_reference", (
            f"Item {num} expected IBR, got {row['status']!r}"
        )
