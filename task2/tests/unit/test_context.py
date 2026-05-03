from agent.context import build_messages


def test_older_tape_with_empty_obs_does_not_crash():
    """Tools may return '' (e.g. some no-op observations). The narrative
    history must render fine for empty-obs steps (the old _short helper
    used to crash with IndexError on ''.splitlines())."""
    tape = [{"reason": "", "action": "read", "args": {}, "obs": ""} for _ in range(10)]
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
    # Narrative is rendered into the system message now.
    assert "step 0 | " in msgs[0]["content"]


def test_ordering_and_compression():
    tape = [{"reason": f"t{i}", "action": "read", "args": {}, "obs": f"o{i}"} for i in range(12)]
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
    # Narrative (step lines) lives in the system message now.
    assert "step 0 | " in sys
    assert "step 3 | " in sys

    # Conversation is exactly system + user.
    assert len(msgs) == 2


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


def test_build_messages_appends_page_diff_when_present():
    from agent.context import build_messages

    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="URL=https://x.test/",
        replan_hint=None,
        page_diff="Page changes since last turn (+1 / -0 lines):\n  + 'Sort: Params'",
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Page changes since last turn" in user["content"]
    assert "Sort: Params" in user["content"]


def test_build_messages_omits_page_diff_when_none():
    from agent.context import build_messages

    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="URL=https://x.test/",
        replan_hint=None,
        page_diff=None,
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Page changes" not in user["content"]


def test_build_messages_page_diff_default_is_none():
    from agent.context import build_messages

    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="URL=https://x.test/",
        replan_hint=None,
    )
    assert any(m["role"] == "user" for m in msgs)


def test_reason_log_renders_under_dedicated_block():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
        reason_log=["a", "b"],
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Reasoning so far:" in user["content"]
    assert "- a" in user["content"]
    assert "- b" in user["content"]


def test_reason_log_block_says_none_when_empty():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
        reason_log=[],
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Reasoning so far:\n(none)" in user["content"]


def test_reason_log_block_omitted_when_kwarg_not_passed():
    """Backward-compat: callers that don't yet pass reason_log get the
    same prompt as before — no Reasoning so far: block at all."""
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Reasoning so far:" not in user["content"]


def test_reason_log_fifo_trims_oldest_when_over_4kb():
    """Cap is 4 KB encoded; oldest lines drop first."""
    big = ["line0: " + "x" * 200] + [f"line{i}: " + "y" * 200 for i in range(1, 30)]
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
        reason_log=big,
    )
    user = next(m for m in msgs if m["role"] == "user")
    # Find the rendered reason block
    reason_section = user["content"].split("Reasoning so far:\n", 1)[1]
    # Stop at the next double-newline (next prompt section)
    reason_section = reason_section.split("\n\n", 1)[0]
    assert len(reason_section.encode()) <= 4096
    assert "line29" in reason_section  # newest kept
    assert "line0" not in reason_section  # oldest dropped
