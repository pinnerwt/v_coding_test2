from agent.context import build_messages


def test_ordering_and_compression():
    tape = [
        {"thought": f"t{i}", "action": "read", "args": {}, "obs": f"o{i}"}
        for i in range(12)
    ]
    msgs = build_messages(
        system="SYS",
        goal="buy soap",
        qa=[("size?", "M")],
        url_notes="- prev: tried X",
        tape=tape,
        page_header="URL=x title=T elems=10",
        replan_hint=None,
    )
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert "SYS" in sys
    assert "Goal: buy soap" in sys
    assert "Q: size?" in sys and "A: M" in sys
    assert "tried X" in sys
    assert "URL=x" in sys
    assert "step 0: read" in sys
    assert "step 3: read" in sys
    expanded = msgs[1:]
    assert len(expanded) == 8 * 2
    assert expanded[0]["role"] == "assistant"
    assert expanded[1]["role"] == "tool"
    assert any("t11" in m.get("content", "") for m in expanded)


def test_replan_hint_appended_to_system():
    msgs = build_messages(
        system="SYS",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="h",
        replan_hint="REPLAN: try a different element",
    )
    assert "REPLAN" in msgs[0]["content"]
