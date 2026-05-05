from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, regex_search


def test_registered():
    assert "regex_search" in REGISTRY
    assert REGISTRY["regex_search"].SCHEMA["function"]["name"] == "regex_search"


def test_returns_matches_with_groups():
    state = SessionState()
    tid = state.store_text("Item 1. Business\nItem 2. Risk Factors")
    result = regex_search.run(state, {"text_id": tid, "pattern": r"Item (\d+)\."})
    assert result["count"] == 2
    assert result["truncated"] is False
    m0 = result["matches"][0]
    assert m0["groups"][0] == "Item 1."
    assert m0["groups"][1] == "1"
    assert isinstance(m0["start"], int)
    assert isinstance(m0["end"], int)


def test_max_matches_caps_and_sets_truncated():
    state = SessionState()
    tid = state.store_text("a a a a a a a a a a")
    result = regex_search.run(state, {"text_id": tid, "pattern": r"a", "max_matches": 3})
    assert result["count"] == 3
    assert result["truncated"] is True
    assert len(result["matches"]) == 3


def test_returns_error_on_invalid_regex():
    state = SessionState()
    tid = state.store_text("hello")
    result = regex_search.run(state, {"text_id": tid, "pattern": r"["})
    assert "error" in result
    assert "invalid regex" in result["error"]
