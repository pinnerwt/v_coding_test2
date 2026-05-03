"""Goto-grounding scans narrative urls/actions/reasons, not obs.

After T6 the recent-3-obs window is too narrow to ground new navigations against.
The narrative-history (T5) IS in context, so the allowlist source moves from prior
obs strings to per-step (url, args string values, reason).
"""

from collections import deque

from agent.tools.browser import (
    _build_allowlist_sources_from_tape,
    _extract_urls,
    _is_goto_allowed,
)


def test_url_extracted_from_reason():
    urls = _extract_urls("found https://example.com/foo earlier")
    assert "https://example.com/foo" in urls


def test_goto_allowed_when_url_in_reason_text():
    sources = ["I noticed https://example.com/foo on the page"]
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://example.com/foo", allowlist)


def test_goto_blocked_when_url_nowhere():
    sources = ["nothing relevant"]
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert not _is_goto_allowed("https://malicious.example/x", allowlist)


def test_allowlist_sources_extract_url_from_reason_only():
    """The URL appears ONLY in reason — not in obs, not in args, not in url field.
    This proves the new behavior: reason text contributes to the allowlist.

    Regression-shaped: under the OLD obs-based extraction, this tape's only
    obs string contains no URL, so the allowlist would be empty and the goto
    blocked. Documenting that explicitly:
    """
    tape = [
        {
            "url": "https://other.example/",
            "action": "read",
            "args": {},
            "reason": "found https://example.com/foo on the page",
            "obs": "page contained no useful URLs",
        }
    ]
    # Sanity: the obs intentionally has no URL — old extraction would fail.
    assert "https://example.com/foo" not in tape[0]["obs"]

    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="goal text",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://example.com/foo", allowlist)


def test_allowlist_sources_extract_url_from_action_args():
    tape = [
        {
            "url": "https://start.example/",
            "action": "goto",
            "args": {"url": "https://target.example/page"},
            "reason": "navigate",
            "obs": "navigated",
        }
    ]
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="g",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://target.example/page", allowlist)


def test_allowlist_sources_extract_url_from_step_url_field():
    tape = [
        {
            "url": "https://visited.example/path",
            "action": "read",
            "args": {},
            "reason": "looking",
            "obs": "<text>",
        }
    ]
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="g",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://visited.example/path", allowlist)


def test_allowlist_sources_skips_non_string_args():
    """Non-string args (e.g. id ints, bools) must not crash extraction."""
    tape = [
        {
            "url": "",
            "action": "click",
            "args": {"id": 7, "submit": True},
            "reason": "click button",
            "obs": "clicked",
        }
    ]
    # Should not raise.
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    # No URL anywhere in this tape.
    assert allowlist == []


def test_allowlist_sources_includes_goal_page_and_visited():
    tape = []
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="https://current.example/now",
        goal="visit https://goal.example/x",
        visited_urls=deque(["https://past.example/p"]),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://goal.example/x", allowlist)
    assert _is_goto_allowed("https://current.example/now", allowlist)
    assert _is_goto_allowed("https://past.example/p", allowlist)


def test_allowlist_sources_does_not_include_obs():
    """The whole point of T8: obs is no longer a source."""
    tape = [
        {
            "url": "https://a.example/",
            "action": "read",
            "args": {},
            "reason": "looking",
            "obs": "page mentions https://obs-only.example/x",
        }
    ]
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="g",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert not _is_goto_allowed("https://obs-only.example/x", allowlist)
