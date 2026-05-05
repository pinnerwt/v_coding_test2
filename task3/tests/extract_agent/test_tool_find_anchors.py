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


def test_run_by_item_picks_longest_body_when_duplicates_survive():
    # Intel-2001-style layout: real body anchor at offset 1000 with a long body,
    # then a tail cross-ref index packing the same item at offset 11000+ with a
    # ~20-char gap. Both are past toc_threshold=500, so both survive dedupe.
    # `by_item` must expose the longest-body anchor — last-write-wins on a dict
    # would mislead the agent into thinking the tail is the real body.
    front_pad = "x\n" * 500  # 1000 chars
    body_filler = "real body content here.\n" * 400  # ~9600 chars
    tail_index = "ITEM 1. Business\nITEM 2. Properties\n"
    text = (
        front_pad
        + "ITEM 1. Business\n"  # anchor at 1000
        + body_filler  # ~9600 chars before next ITEM heading
        + tail_index  # ITEM 1 at ~10617, ITEM 2 immediately after
    )
    state = SessionState()
    tid = state.store_text(text)
    result = find_anchors.run(state, {"text_id": tid, "toc_threshold": 500})
    item1_anchors = [a for a in state.anchors if a["item_number"] == "1"]
    assert len(item1_anchors) >= 2, "test setup: expected front+tail anchors"
    front_offset = min(a["match_start"] for a in item1_anchors)
    tail_offset = max(a["match_start"] for a in item1_anchors)
    assert result["by_item"]["1"] == front_offset, (
        f"expected longest-body offset {front_offset}, got {result['by_item']['1']} "
        f"(tail offset is {tail_offset})"
    )


def test_run_reports_duplicates_per_item():
    # Two anchors for item 1 past toc_threshold=500 → result should expose
    # `duplicates` so the agent knows there's ambiguity (and which item).
    front_pad = "x\n" * 500  # 1000 chars
    text = (
        front_pad
        + "ITEM 1. Business\n"
        + ("body " * 200)
        + "\nITEM 1. Business Repeat\nshort\n"
    )
    state = SessionState()
    tid = state.store_text(text)
    result = find_anchors.run(state, {"text_id": tid, "toc_threshold": 500})
    assert "duplicates" in result
    assert result["duplicates"].get("1", 0) >= 2
