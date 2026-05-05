from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, inspect_record


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
    assert "inspect_record" in REGISTRY
    assert REGISTRY["inspect_record"].SCHEMA["function"]["name"] == "inspect_record"


def test_returns_full_body_when_short():
    state = SessionState()
    state.records = [_make_record("1", "short body text", (0, 15))]
    result = inspect_record.run(state, {"index": 0})
    assert result["body_preview"] == "short body text"
    assert result["record"]["item_number"] == "1"
    assert result["record"]["content_text_length"] == 15
    assert "content_text" not in result["record"]


def test_truncates_long_body():
    state = SessionState()
    # 4096 A's, then 4000 middle M's, then 2048 C's = 10144 > 6144
    body = "A" * 4096 + "M" * 4000 + "C" * 2048
    state.records = [_make_record("1", body, (0, len(body)))]
    result = inspect_record.run(state, {"index": 0})
    preview = result["body_preview"]
    assert "...[truncated]..." in preview
    assert preview.startswith("A" * 100)
    assert preview.endswith("C" * 100)
    # First 4KB of A's, last 2KB of C's, middle M's elided
    assert preview.count("A") == 4096
    assert preview.count("C") == 2048
    assert preview.count("M") == 0
    assert result["record"]["content_text_length"] == len(body)


def test_truncated_false_when_body_short():
    state = SessionState()
    state.records = [_make_record("1", "short body text", (0, 15))]
    result = inspect_record.run(state, {"index": 0})
    assert result["truncated"] is False


def test_truncated_true_when_body_long():
    state = SessionState()
    body = "A" * 4096 + "M" * 4000 + "C" * 2048  # 10144 > 6144
    state.records = [_make_record("1", body, (0, len(body)))]
    result = inspect_record.run(state, {"index": 0})
    assert result["truncated"] is True


def test_returns_neighbors():
    state = SessionState()
    state.records = [
        _make_record("1", "a", (0, 10)),
        _make_record("2", "b", (10, 20)),
        _make_record("3", "c", (20, 30)),
    ]
    result = inspect_record.run(state, {"index": 1})
    assert result["neighbor_above"] == {"item_number": "1", "char_range": [0, 10]}
    assert result["neighbor_below"] == {"item_number": "3", "char_range": [20, 30]}

    # Edges
    r0 = inspect_record.run(state, {"index": 0})
    assert r0["neighbor_above"] is None
    assert r0["neighbor_below"]["item_number"] == "2"

    r2 = inspect_record.run(state, {"index": 2})
    assert r2["neighbor_below"] is None
    assert r2["neighbor_above"]["item_number"] == "2"


def test_error_on_no_records():
    state = SessionState()
    result = inspect_record.run(state, {"index": 0})
    assert "error" in result
    assert "no records" in result["error"]


def test_error_on_out_of_range():
    state = SessionState()
    state.records = [_make_record("1", "a", (0, 1))]
    result = inspect_record.run(state, {"index": 5})
    assert "error" in result
    assert "5" in result["error"]
    assert "out of range" in result["error"]
