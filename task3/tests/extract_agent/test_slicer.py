from extract_agent.slicer import slice_items


def test_slices_between_adjacent_anchors():
    text = "Item 1.   Business\nbody1 body1\n\nItem 2. Properties\nbody2"
    anchors = [
        {
            "item_number": "1",
            "match_start": 0,
            "match_end": len("Item 1.   Business"),
            "title": "Business",
        },
        {
            "item_number": "2",
            "match_start": text.find("Item 2."),
            "match_end": text.find("Item 2.") + len("Item 2. Properties"),
            "title": "Properties",
        },
    ]
    records = slice_items(text, anchors)
    by_num = {r["item_number"]: r for r in records}
    assert "body1" in by_num["1"]["content_text"]
    assert "body2" in by_num["2"]["content_text"]
    assert by_num["1"]["part"] == "I"
    assert by_num["2"]["part"] == "I"


def test_picks_longest_body_when_item_has_two_anchors():
    text = (
        "Item 1.   Stub\n5\nItem 1.   Real Business\nlong body of business\nItem 2. Properties\nx"
    )
    a1 = text.index("Item 1.   Stub")
    a2 = text.index("Item 1.   Real Business")
    a3 = text.index("Item 2.")
    anchors = [
        {
            "item_number": "1",
            "match_start": a1,
            "match_end": a1 + len("Item 1.   Stub"),
            "title": "Stub",
        },
        {
            "item_number": "1",
            "match_start": a2,
            "match_end": a2 + len("Item 1.   Real Business"),
            "title": "Real Business",
        },
        {
            "item_number": "2",
            "match_start": a3,
            "match_end": a3 + len("Item 2. Properties"),
            "title": "Properties",
        },
    ]
    records = slice_items(text, anchors)
    item1 = next(r for r in records if r["item_number"] == "1")
    assert "long body" in item1["content_text"]
    assert item1["item_title"] == "Real Business"


def test_trims_trailing_pagenum_when_body_short():
    text = "Item 4. Mine Safety\nNot applicable.\n\n32\nItem 5. X\ny"
    a1 = text.index("Item 4.")
    a2 = text.index("Item 5.")
    anchors = [
        {
            "item_number": "4",
            "match_start": a1,
            "match_end": a1 + len("Item 4. Mine Safety"),
            "title": "Mine Safety",
        },
        {"item_number": "5", "match_start": a2, "match_end": a2 + len("Item 5. X"), "title": "X"},
    ]
    records = slice_items(text, anchors)
    item4 = next(r for r in records if r["item_number"] == "4")
    assert "32" not in item4["content_text"]
    assert "Not applicable" in item4["content_text"]


def test_does_not_trim_pagenum_from_long_body():
    body = "x " * 300 + "2023"
    text = f"Item 1. Business\n{body}\nItem 2. X\ny"
    a1 = text.index("Item 1.")
    a2 = text.index("Item 2.")
    anchors = [
        {
            "item_number": "1",
            "match_start": a1,
            "match_end": a1 + len("Item 1. Business"),
            "title": "Business",
        },
        {"item_number": "2", "match_start": a2, "match_end": a2 + len("Item 2. X"), "title": "X"},
    ]
    records = slice_items(text, anchors)
    item1 = next(r for r in records if r["item_number"] == "1")
    assert "2023" in item1["content_text"]


def test_records_sorted_by_char_range_start():
    text = "Item 2. P\nb2\nItem 1. B\nb1"
    a2 = text.index("Item 2.")
    a1 = text.index("Item 1.")
    anchors = [
        {"item_number": "1", "match_start": a1, "match_end": a1 + len("Item 1. B"), "title": "B"},
        {"item_number": "2", "match_start": a2, "match_end": a2 + len("Item 2. P"), "title": "P"},
    ]
    records = slice_items(text, anchors)
    starts = [r["char_range"][0] for r in records]
    assert starts == sorted(starts)
