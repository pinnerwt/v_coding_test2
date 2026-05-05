from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, update_record


def _make_record(item_number, content_text, char_range, status="extracted"):
    return {
        "part": "I",
        "item_number": item_number,
        "item_title": f"Item {item_number}",
        "content_text": content_text,
        "char_range": list(char_range),
        "status": status,
    }


def test_registered():
    assert "update_record" in REGISTRY
    assert REGISTRY["update_record"].SCHEMA["function"]["name"] == "update_record"


def test_patch_status_only():
    state = SessionState()
    state.records = [_make_record("1", "body", (0, 4))]
    result = update_record.run(
        state, {"index": 0, "patch": {"status": "incorporated_by_reference"}}
    )
    assert result["updated"] is True
    assert result["record"]["status"] == "incorporated_by_reference"
    assert "content_text" not in result["record"]
    assert result["record"]["content_text_length"] == 4
    # State actually mutated
    assert state.records[0]["status"] == "incorporated_by_reference"
    assert state.records[0]["content_text"] == "body"


def test_patch_char_range_recomputes_content():
    state = SessionState()
    tid = state.store_text("0123456789ABCDEF")
    state.records = [_make_record("1", "0123", (0, 4))]
    result = update_record.run(
        state,
        {"index": 0, "patch": {"char_range": [4, 10]}, "text_id": tid},
    )
    assert result["updated"] is True
    assert state.records[0]["content_text"] == "456789"
    assert state.records[0]["char_range"] == [4, 10]
    assert result["record"]["content_text_length"] == 6


def test_error_when_char_range_without_text_id():
    state = SessionState()
    state.records = [_make_record("1", "body", (0, 4))]
    result = update_record.run(state, {"index": 0, "patch": {"char_range": [0, 2]}})
    assert "error" in result
    assert "char_range" in result["error"]
    assert "text_id" in result["error"]


def test_error_on_unknown_patch_key():
    state = SessionState()
    state.records = [_make_record("1", "body", (0, 4))]
    result = update_record.run(state, {"index": 0, "patch": {"foobar": "x", "status": "ok"}})
    assert "error" in result
    assert "foobar" in result["error"]


def test_error_on_no_records():
    state = SessionState()
    result = update_record.run(state, {"index": 0, "patch": {"status": "ok"}})
    assert "error" in result
    assert "no records" in result["error"]
