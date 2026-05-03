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
