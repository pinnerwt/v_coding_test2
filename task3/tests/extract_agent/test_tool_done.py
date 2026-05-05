from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, done


def test_registered():
    assert "done" in REGISTRY
    assert REGISTRY["done"].SCHEMA["function"]["name"] == "done"


def test_returns_done_true():
    state = SessionState()
    result = done.run(state, {})
    assert result["done"] is True
    assert result["message"] == ""


def test_carries_optional_message():
    state = SessionState()
    result = done.run(state, {"message": "all 18 items extracted"})
    assert result["done"] is True
    assert result["message"] == "all 18 items extracted"
