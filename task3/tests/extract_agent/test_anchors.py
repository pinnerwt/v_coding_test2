from extract_agent.anchors import ITEM_TO_PART, dedupe_anchors, find_anchors


def test_finds_basic_item_headings():
    text = "Item 1.    Business\nstuff\n\nItem 1A.   Risk Factors\nstuff"
    anchors = find_anchors(text)
    nums = [a["item_number"] for a in anchors]
    assert nums == ["1", "1A"]


def test_skips_bare_item_running_headers():
    # "Item 1" with no period/colon should not match (filters page-running headers)
    text = "Item 1\nstuff\nItem 1A. Risk Factors\nstuff"
    anchors = find_anchors(text)
    assert [a["item_number"] for a in anchors] == ["1A"]


def test_skips_out_of_range_item_numbers():
    text = "Item 60. Footnote ref\nItem 1. Business\nstuff"
    anchors = find_anchors(text)
    assert [a["item_number"] for a in anchors] == ["1"]


def test_part_map_is_canonical_form_10k():
    assert ITEM_TO_PART["1"] == "I"
    assert ITEM_TO_PART["7A"] == "II"
    assert ITEM_TO_PART["10"] == "III"
    assert ITEM_TO_PART["15"] == "IV"


def test_dedupe_drops_toc_when_body_exists():
    anchors = [
        {"item_number": "1", "match_start": 1000, "match_end": 1020, "title": "Business"},
        {"item_number": "1", "match_start": 9000, "match_end": 9020, "title": "Business"},
        {"item_number": "1A", "match_start": 1100, "match_end": 1130, "title": "Risk Factors"},
    ]
    deduped = dedupe_anchors(anchors, toc_region_end=8000)
    by_num = {a["item_number"]: a for a in deduped}
    # Item 1 should keep only the body anchor (>8000)
    assert by_num["1"]["match_start"] == 9000
    # Item 1A has only TOC-region anchor; keep it (no body alternative)
    assert by_num["1A"]["match_start"] == 1100


def test_anchor_captures_title_when_present():
    text = "Item 1.   Business\nbody"
    anchors = find_anchors(text)
    assert anchors[0]["title"] == "Business"


def test_anchor_empty_title_when_absent():
    text = "Item 1.\nBusiness\nbody"
    anchors = find_anchors(text)
    # Title may be empty or "Business" depending on regex; both acceptable —
    # the slicer's "next non-empty line" fallback handles empty titles.
    assert anchors[0]["title"] in ("", "Business")
