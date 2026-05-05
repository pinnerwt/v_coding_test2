from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, find_anchors, slice_items


def test_registered():
    assert "slice_items" in REGISTRY
    assert REGISTRY["slice_items"].SCHEMA["function"]["name"] == "slice_items"


def test_returns_error_when_no_anchors():
    state = SessionState()
    tid = state.store_text("ITEM 1. Business\nbody\n")
    result = slice_items.run(state, {"text_id": tid})
    assert "error" in result
    assert "find_anchors" in result["error"]


def test_slices_after_find_anchors_runs():
    text = (
        "ITEM 1. Business\n"
        + ("Real business body content here. " * 50)
        + "\nITEM 2. Properties\n"
        + ("Real property content lives here. " * 50)
        + "\n"
    )
    state = SessionState()
    tid = state.store_text(text)
    find_anchors.run(state, {"text_id": tid})
    result = slice_items.run(state, {"text_id": tid})
    assert result["count"] == 2
    assert len(state.records) == 2
    assert all("status" in r for r in state.records)
    assert all(r["status"] == "extracted" for r in state.records)
    assert isinstance(result["summary"], list)
    assert len(result["summary"]) == 2
    # Summary mentions item id and char count
    assert "Item 1" in result["summary"][0]
    assert "chars" in result["summary"][0]
