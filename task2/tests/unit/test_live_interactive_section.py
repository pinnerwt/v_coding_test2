"""build_messages appends a `## Interactive elements (live)` section to the
user message when given the `interactive_elements` kwarg."""
from agent.context import build_messages


def _user_text(msgs: list[dict]) -> str:
    return next(m["content"] for m in msgs if m["role"] == "user")


def test_no_section_when_kwarg_omitted():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
    )
    assert "## Interactive elements" not in _user_text(msgs)


def test_no_section_when_kwarg_none():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
        interactive_elements=None,
    )
    assert "## Interactive elements" not in _user_text(msgs)


def test_section_appended_when_kwarg_set():
    payload = '[{"id": 0, "role": "link", "name": "Home"}]'
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
        interactive_elements=payload,
    )
    user = _user_text(msgs)
    assert "## Interactive elements (live)" in user
    assert payload in user


def test_section_after_recent_obs():
    """The live-elements section comes after the recent-obs block, so the
    most-recent context the LLM sees is the live element list."""
    tape = [
        {"action": "read", "args": {}, "obs": "old1", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "old2", "url": "", "reason": ""},
    ]
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="HDR",
        replan_hint=None,
        interactive_elements="[{\"id\":0}]",
    )
    user = _user_text(msgs)
    obs_idx = user.index("## Recent observations")
    elem_idx = user.index("## Interactive elements (live)")
    assert obs_idx < elem_idx
