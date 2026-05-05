from extract_agent.anchors import ITEM_TO_PART
from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, validate_records

EXPECTED_ITEMS = [
    "1",
    "1A",
    "1B",
    "2",
    "3",
    "4",
    "5",
    "7",
    "7A",
    "8",
    "9A",
    "9B",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
]


def test_registered():
    assert "validate_records" in REGISTRY
    assert REGISTRY["validate_records"].SCHEMA["function"]["name"] == "validate_records"


def test_returns_error_when_no_records():
    state = SessionState()
    result = validate_records.run(state, {})
    assert "error" in result


def test_ok_true_when_records_pass():
    body = "x" * 200
    records = []
    cursor = 0
    for item in EXPECTED_ITEMS:
        records.append(
            {
                "part": ITEM_TO_PART[item],
                "item_number": item,
                "item_title": f"Title for {item}",
                "content_text": body,
                "char_range": [cursor, cursor + len(body)],
                "status": "extracted",
            }
        )
        cursor += len(body) + 10  # gap to ensure non-overlap
    state = SessionState()
    state.records = records
    result = validate_records.run(state, {})
    assert result["ok"] is True, result
    assert result["findings"] == []
