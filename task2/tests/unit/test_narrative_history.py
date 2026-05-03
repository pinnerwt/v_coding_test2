"""Narrative history rendering — full session, unbounded, in system prompt."""

from agent.context import build_messages


def _step(url: str, action: str, args: dict, reason: str, obs: str) -> dict:
    return {"url": url, "action": action, "args": args, "reason": reason, "obs": obs}


def test_narrative_renders_in_system_for_each_step():
    tape = [
        _step(
            "https://a.com",
            "goto",
            {"url": "https://b.com"},
            "navigating to b",
            "navigated to https://b.com",
        ),
        _step("https://b.com", "read", {}, "checking content", "<text>"),
    ]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=https://b.com",
        replan_hint=None,
    )
    sys_content = msgs[0]["content"]
    assert "## Action history" in sys_content
    # Narrative line format: step N | url | action_call | reason
    assert "step 0 | https://a.com | goto" in sys_content
    assert "navigating to b" in sys_content
    assert "step 1 | https://b.com | read" in sys_content
    assert "checking content" in sys_content


def test_narrative_unbounded():
    tape = [_step(f"https://x{i}.com", "read", {}, f"reason {i}", f"obs {i}") for i in range(25)]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=",
        replan_hint=None,
    )
    sys_content = msgs[0]["content"]
    for i in range(25):
        assert f"step {i} |" in sys_content
        assert f"reason {i}" in sys_content


def test_narrative_flattens_multiline_reason():
    """A reason with embedded newlines must not break the one-line-per-step format."""
    tape = [
        _step("https://a.com", "read", {}, "line1\nline2\nline3", "obs"),
        _step("https://b.com", "click", {"id": 7}, "next step", "obs"),
    ]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=",
        replan_hint=None,
    )
    sys_content = msgs[0]["content"]
    # The step-0 line stays one line: contains all three reason fragments separated by spaces.
    assert "step 0 | https://a.com | read() | line1 line2 line3" in sys_content
    # And the step-1 line still appears as its own line, not as a fragment of step 0's reason.
    assert "\nstep 1 | https://b.com |" in sys_content


def test_user_message_includes_last_5_obs_raw():
    tape = [_step("https://a.com", "read", {}, f"r{i}", f"obs-content-{i}") for i in range(8)]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=",
        replan_hint=None,
    )
    user_content = msgs[1]["content"]
    assert "## Recent observations (last 5)" in user_content
    # Exactly the last 5, in order
    for i in (3, 4, 5, 6, 7):
        assert f"obs-content-{i}" in user_content
    # NOT the older ones
    for i in (0, 1, 2):
        assert f"obs-content-{i}" not in user_content
    assert "---" in user_content


def test_user_message_obs_when_tape_short():
    tape = [
        _step("https://a.com", "read", {}, "r0", "obs0"),
        _step("https://a.com", "read", {}, "r1", "obs1"),
    ]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=",
        replan_hint=None,
    )
    user_content = msgs[1]["content"]
    assert "obs0" in user_content
    assert "obs1" in user_content


def test_no_assistant_tool_pair_messages():
    """Conversation is exactly system + user. No interleaved tool_calls."""
    tape = [_step("https://a.com", "read", {}, f"r{i}", f"obs-{i}") for i in range(10)]
    msgs = build_messages(
        system="<sys>",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=",
        replan_hint=None,
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
