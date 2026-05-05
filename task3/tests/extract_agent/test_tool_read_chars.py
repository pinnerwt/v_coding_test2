from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, read_chars


def test_registered():
    assert "read_chars" in REGISTRY
    assert REGISTRY["read_chars"].SCHEMA["function"]["name"] == "read_chars"


def test_returns_slice():
    state = SessionState()
    tid = state.store_text("hello world")
    result = read_chars.run(state, {"text_id": tid, "start": 0, "end": 5})
    assert result["text"] == "hello"
    assert result["length"] == 5
    assert result.get("truncated") in (False, None)


def test_caps_at_8kb():
    state = SessionState()
    tid = state.store_text("a" * 20000)
    result = read_chars.run(state, {"text_id": tid, "start": 0, "end": 20000})
    assert len(result["text"]) == 8192
    assert result["length"] == 8192
    assert result["truncated"] is True


def test_returns_error_on_unknown_text_id():
    state = SessionState()
    result = read_chars.run(state, {"text_id": "nope", "start": 0, "end": 5})
    assert "error" in result
    assert "nope" in result["error"]


def test_returns_error_on_negative_start():
    state = SessionState()
    tid = state.store_text("hello world")
    result = read_chars.run(state, {"text_id": tid, "start": -1, "end": 5})
    assert "error" in result
    assert "start" in result["error"]


def test_returns_error_on_inverted_range():
    state = SessionState()
    tid = state.store_text("hello world")
    result = read_chars.run(state, {"text_id": tid, "start": 10, "end": 5})
    assert "error" in result
    assert "end" in result["error"]
    assert "start" in result["error"]


def test_returns_error_on_end_past_length():
    state = SessionState()
    tid = state.store_text("hello world")
    result = read_chars.run(state, {"text_id": tid, "start": 0, "end": 999})
    assert "error" in result
    assert "exceeds text length" in result["error"]
