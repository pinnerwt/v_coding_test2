"""Goto-grounding scans page-grounded sources, not the agent's own narrative.

Earlier (T8) the allowlist was built from per-step (url, args string values,
reason). That design is self-defeating: the agent can put any URL into its own
`reason` text or into the `args` of a *blocked* goto, and the very next goto
to that URL will pass the check. Trace 7bd13ccc demonstrated the loophole —
agent typed the target URL into a checkpoint reason after a blocked goto, then
re-issued goto and was admitted.

Sources that are evidence the URL was actually exposed by the world (not
fabricated by the agent):
  - the goal text (user-supplied)
  - prior step `url` field (page URL the browser actually loaded)
  - prior step `obs` field (page text the browser actually rendered)
  - the visited-URL chain
NOT sources: the agent's `reason` text or `args` strings.
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
    """Lower-level helper: _is_goto_allowed itself is source-agnostic. The
    grounding decision (what counts as a valid source) lives in
    _build_allowlist_sources_from_tape, tested separately below."""
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


def _allowlist_from(tape, **kw):
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url=kw.get("page_url", ""),
        goal=kw.get("goal", ""),
        visited_urls=kw.get("visited_urls", deque()),
    )
    out: list[str] = []
    for s in sources:
        out.extend(_extract_urls(s))
    return out


def test_allowlist_excludes_url_only_in_reason():
    """Regression: agent fabricates a URL in `reason`, then goto's it.
    Before this fix, this URL would land in the allowlist via reason text.
    Trace 7bd13ccc is the canonical case (step 10 reason → step 11 goto)."""
    tape = [
        {
            "url": "https://github.com/",
            "action": "reason",
            "args": {"text": "I'll navigate directly", "reason": "checkpoint"},
            "reason": "Next: I'll goto https://github.com/huggingface/transformers",
            "obs": "noted",
        }
    ]
    allowlist = _allowlist_from(tape, page_url="https://github.com/")
    assert not _is_goto_allowed(
        "https://github.com/huggingface/transformers", allowlist
    )


def test_allowlist_excludes_url_only_in_action_args():
    """Regression: a *blocked* goto still leaves its target URL in args. If
    args were a source, the agent could just retry and be admitted on the
    second try. They aren't."""
    tape = [
        {
            "url": "https://start.example/",
            "action": "goto",
            "args": {"url": "https://target.example/page"},
            "reason": "navigate",
            "obs": "ERROR: blocked goto to https://target.example/page",
        }
    ]
    # The blocked-goto's *obs* mentions the URL — but obs URLs only count when
    # they were rendered by the page, not when they were echoed back inside an
    # error message that quoted the agent's own input. We accept that obs-as-
    # source is slightly loose here (the URL did make it into a real obs
    # string), but not via args alone:
    sources = _build_allowlist_sources_from_tape(
        tape=[{**tape[0], "obs": "blocked"}],  # strip URL from obs
        page_url="",
        goal="g",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert not _is_goto_allowed("https://target.example/page", allowlist)


def test_allowlist_includes_url_from_obs():
    """A URL that appeared in real page text (an `obs`) IS grounded — that's
    how the agent navigates from a search results page, etc."""
    tape = [
        {
            "url": "https://google.com/search",
            "action": "read",
            "args": {},
            "reason": "looking for the repo",
            "obs": (
                "search results: https://github.com/huggingface/transformers"
                " - State-of-the-art ML"
            ),
        }
    ]
    allowlist = _allowlist_from(tape)
    assert _is_goto_allowed(
        "https://github.com/huggingface/transformers", allowlist
    )


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
    allowlist = _allowlist_from(tape)
    assert _is_goto_allowed("https://visited.example/path", allowlist)


def test_allowlist_sources_skips_non_string_args():
    """Non-string args (e.g. id ints, bools) must not crash extraction.
    Args themselves are no longer a source, but the function still walks
    them defensively — extraction should not raise."""
    tape = [
        {
            "url": "",
            "action": "click",
            "args": {"id": 7, "submit": True},
            "reason": "click button",
            "obs": "clicked",
        }
    ]
    sources = _build_allowlist_sources_from_tape(
        tape=tape,
        page_url="",
        goal="",
        visited_urls=deque(),
    )
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
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
