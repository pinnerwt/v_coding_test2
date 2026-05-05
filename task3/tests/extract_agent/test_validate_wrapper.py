from extract_agent.validate import summary_lines, validate


def test_validate_flags_overlapping_records():
    records = [
        {
            "part": "I",
            "item_number": "1",
            "item_title": "B",
            "content_text": "x" * 100,
            "char_range": [0, 100],
            "status": "extracted",
        },
        {
            "part": "I",
            "item_number": "1",
            "item_title": "B",
            "content_text": "y" * 100,
            "char_range": [50, 200],
            "status": "extracted",
        },
    ]
    findings = validate(records)
    assert any("overlap" in f for f in findings)


def test_summary_lines_formats_per_record():
    records = [
        {
            "part": "I",
            "item_number": "1",
            "item_title": "Business",
            "content_text": "x" * 100,
            "char_range": [0, 100],
            "status": "extracted",
        },
    ]
    lines = summary_lines(records)
    assert lines == ["Part I Item 1 [extracted] -- Business (100 chars)"]
