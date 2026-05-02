from agent.context import build_messages


def test_older_tape_with_empty_obs_does_not_crash():
    """Tools may return '' (e.g. some no-op observations). Once such a step
    falls into the older-tape summary window, _short used to crash with
    IndexError because ''.splitlines() == []. Caught Wolfram bench at step 15."""
    tape = [{"thought": "", "action": "read", "args": {}, "obs": ""} for _ in range(10)]
    msgs = build_messages(
        system="SYS",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="h",
        replan_hint=None,
    )
    assert msgs[1]["role"] == "user"
    assert "step 0: read" in msgs[1]["content"]


def test_ordering_and_compression():
    tape = [{"thought": f"t{i}", "action": "read", "args": {}, "obs": f"o{i}"} for i in range(12)]
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

    assert msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert "Goal: buy soap" in user
    assert "Q: size?" in user and "A: M" in user
    assert "tried X" in user
    assert "URL=x" in user
    assert "step 0: read" in user
    assert "step 3: read" in user

    expanded = msgs[2:]
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


def test_emits_user_message_with_goal():
    """Local Qwen chat template requires at least one role=user message;
    stuffing the goal into 'system' causes a 500 with
    'No user query found in messages.'"""
    msgs = build_messages(
        system="SYS",
        goal="buy soap",
        qa=[("size?", "M")],
        url_notes="- prev: tried X",
        tape=[],
        page_header="URL=x title=T elems=10",
        replan_hint=None,
    )
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert user_msgs, "must emit at least one role=user message"
    user_content = user_msgs[0]["content"]
    assert "buy soap" in user_content
    assert "URL=x" in user_content
