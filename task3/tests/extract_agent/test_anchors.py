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


def test_case_insensitive_item_keyword():
    # ITEM_RE uses re.IGNORECASE — all casings of the keyword should match.
    for kw in ("ITEM", "Item", "item"):
        text = f"{kw} 1. Business\nbody"
        anchors = find_anchors(text)
        assert [a["item_number"] for a in anchors] == ["1"]


def test_dedupe_keeps_toc_when_no_body_anchor_exists():
    # If every anchor for an item is in the TOC region, dedupe must keep it.
    anchors = [
        {"item_number": "2", "match_start": 500, "match_end": 520, "title": "Properties"},
    ]
    deduped = dedupe_anchors(anchors, toc_region_end=8000)
    assert len(deduped) == 1
    assert deduped[0]["match_start"] == 500
    assert deduped[0]["item_number"] == "2"


def test_find_anchors_empty_input():
    assert find_anchors("") == []


def test_skips_letter_outside_a_to_c():
    # Regex letter class is [A-C] — "1D" should not match.
    text = "Item 1D. Foo\nbody"
    assert find_anchors(text) == []
    # Positive control: "1A" on the same shape does match.
    anchors = find_anchors("Item 1A. Foo\nbody")
    assert [a["item_number"] for a in anchors] == ["1A"]


def test_toc_dot_leader_title_captured():
    # TOC lines often have dot leaders + page number; title capture folds them in.
    text = "Item 1.    Business ............ 5\nbody"
    anchors = find_anchors(text)
    assert [a["item_number"] for a in anchors] == ["1"]
    assert "Business" in anchors[0]["title"]


def test_custom_regex_with_named_groups():
    # Custom regex can supply named groups: item_number (required),
    # item_letter (optional), item_title (optional).
    import re

    pat = re.compile(
        r"^\s*(?P<item_number>1[0-6]|[1-9])(?P<item_letter>[A-C])?\.\s+(?P<item_title>.*?)$",
        re.IGNORECASE | re.MULTILINE,
    )
    text = "1. Business\nbody\n\n1A. Risk Factors\nbody\n"
    anchors = find_anchors(text, regex=pat)
    assert [a["item_number"] for a in anchors] == ["1", "1A"]
    assert anchors[0]["title"] == "Business"
    assert anchors[1]["title"] == "Risk Factors"


def test_custom_regex_named_item_number_only():
    # Only item_number named group; item_letter / item_title optional.
    import re

    pat = re.compile(
        r"^\s*(?P<item_number>[1-9]|1[0-6])\.\s",
        re.IGNORECASE | re.MULTILINE,
    )
    text = "1. Business\nbody\n2. Properties\nbody\n"
    anchors = find_anchors(text, regex=pat)
    assert [a["item_number"] for a in anchors] == ["1", "2"]
    assert anchors[0]["title"] == ""


def test_custom_regex_missing_contract_raises():
    # A regex with neither numbered groups nor `item_number` named group
    # must raise ValueError so the tool envelope shows a clear message
    # (instead of leaking IndexError: no such group).
    import re

    import pytest

    pat = re.compile(r"^\s*ITEM\s+\d+", re.IGNORECASE | re.MULTILINE)
    with pytest.raises(ValueError, match="item_number"):
        find_anchors("ITEM 1. Business\nbody", regex=pat)


def test_custom_regex_non_capturing_groups_raises():
    # Real failure mode from Citigroup 2008 trace: agent supplied a regex
    # whose alternation uses non-capturing groups, so group(1) is absent.
    import re

    import pytest

    pat = re.compile(
        r"^(?:Item\s+[1-9][A-Z]?|PART\s+(?:I{1,3}|IV))\b",
        re.IGNORECASE | re.MULTILINE,
    )
    with pytest.raises(ValueError, match="item_number"):
        find_anchors("Item 1. Business", regex=pat)
