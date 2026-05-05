from extract_agent.merge_5b import merge_records


def test_passes_through_records_without_segments():
    records = [
        {
            "part": "I",
            "item_number": "1",
            "item_title": "B",
            "content_text": "abc",
            "char_range": [0, 3],
            "status": "extracted",
        },
    ]
    new, rejections = merge_records(records, {})
    assert new == records
    assert rejections == []


def test_splits_record_with_two_segments():
    body = "First sentence. Second sentence."
    records = [
        {
            "part": "I",
            "item_number": "10",
            "item_title": "X",
            "content_text": body,
            "char_range": [100, 100 + len(body)],
            "status": "extracted",
        },
    ]
    segments = {
        0: [
            {"status": "extracted", "starts_with": "First sentence", "ends_with": "."},
            {
                "status": "incorporated_by_reference",
                "starts_with": "Second sentence",
                "ends_with": ".",
            },
        ]
    }
    new, rejections = merge_records(records, segments)
    assert rejections == []
    assert len(new) == 2
    assert new[0]["status"] == "extracted"
    assert new[1]["status"] == "incorporated_by_reference"
