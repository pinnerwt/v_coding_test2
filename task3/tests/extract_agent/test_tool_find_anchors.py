from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, find_anchors


def test_registered():
    assert "find_anchors" in REGISTRY
    assert REGISTRY["find_anchors"].SCHEMA["function"]["name"] == "find_anchors"


def test_run_populates_state_anchors():
    text = "ITEM 1. Business\nbody one\n\nITEM 2. Properties\nbody two\n"
    state = SessionState()
    tid = state.store_text(text)
    result = find_anchors.run(state, {"text_id": tid})
    assert result["count"] == 2
    assert state.anchors is not None
    assert len(state.anchors) == 2
    assert set(result["by_item"].keys()) == {"1", "2"}


def test_run_dedupes_via_toc_threshold():
    # Same item mentioned twice; second occurrence past threshold survives.
    # ITEM_RE is MULTILINE — anchors must be at line start.
    prefix = "x\n" * 50  # 100 chars; anchor starts at offset 100
    middle = "y\n" * 50  # 100 chars
    text = (
        prefix + "ITEM 1. Business\n" + middle + "ITEM 1. Business Body\nlong body content here\n"
    )
    state = SessionState()
    tid = state.store_text(text)
    result = find_anchors.run(state, {"text_id": tid, "toc_threshold": 150})
    assert result["count"] == 1
    assert state.anchors[0]["match_start"] > 150
