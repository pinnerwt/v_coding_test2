from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.tools.browser import _extract_urls, _is_goto_allowed, build_browser_tools


def test_extract_urls_finds_http_and_https():
    s = "see https://en.wikipedia.org/wiki/Tokyo and http://x.test/a?b=c here"
    assert _extract_urls(s) == [
        "https://en.wikipedia.org/wiki/Tokyo",
        "http://x.test/a?b=c",
    ]


def test_extract_urls_strips_trailing_punctuation():
    s = "From https://arxiv.org. Visit https://github.com/repo) now."
    assert _extract_urls(s) == ["https://arxiv.org", "https://github.com/repo"]


def test_extract_urls_handles_empty():
    assert _extract_urls("") == []
    assert _extract_urls("no urls here") == []


def test_is_goto_allowed_substring_of_observed():
    # observed url is longer; requested is a prefix → allowed
    assert _is_goto_allowed(
        "https://en.wikipedia.org",
        ["https://en.wikipedia.org/wiki/Tokyo"],
    )


def test_is_goto_allowed_blocks_extension_under_observed_origin():
    # Observed is a prefix of requested → BLOCKED.
    # Otherwise observing any URL on a domain implicitly allows arbitrary
    # deeper paths under it, defeating the grounding guard. Regression for
    # the GAN case: observing `https://arxiv.org` must not allow the agent
    # to recall `https://arxiv.org/abs/1406.2661` from training data.
    assert not _is_goto_allowed(
        "https://en.wikipedia.org/wiki/Tokyo",
        ["https://en.wikipedia.org"],
    )
    assert not _is_goto_allowed(
        "https://arxiv.org/abs/1406.2661",
        ["https://arxiv.org"],
    )


def test_is_goto_allowed_case_insensitive():
    assert _is_goto_allowed(
        "https://Arxiv.org/abs/1406.2661",
        ["https://arxiv.org/abs/1406.2661"],
    )


def test_is_goto_allowed_blocked_when_unrelated():
    assert not _is_goto_allowed(
        "https://arxiv.org/abs/1406.2661",
        ["https://en.wikipedia.org"],  # different domain entirely
    )


def test_is_goto_allowed_empty_allowlist_blocks():
    assert not _is_goto_allowed("https://x.test", [])


def _fake_session(landing_url: str = "https://done.test/"):
    s = MagicMock()
    s.page = MagicMock()
    s.page.url = landing_url
    s.page.goto = AsyncMock(return_value=None)
    return s


@pytest.mark.asyncio
async def test_goto_blocked_when_url_not_in_sources():
    sources = ["https://en.wikipedia.org"]
    sess = _fake_session()
    tools = build_browser_tools(sess, restrict_goto=True, allowlist_sources=lambda: sources)
    obs = await tools["goto"]("https://arxiv.org/abs/1406.2661")
    assert obs.startswith("ERROR: blocked goto to ")
    assert "list_interactive" in obs
    sess.page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_goto_allowed_when_url_in_sources():
    sources = ["Goal: Go to https://arxiv.org and find ..."]
    sess = _fake_session("https://arxiv.org/")
    tools = build_browser_tools(sess, restrict_goto=True, allowlist_sources=lambda: sources)
    obs = await tools["goto"]("https://arxiv.org")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_unrestricted_by_default():
    sess = _fake_session("https://anything.test/")
    tools = build_browser_tools(sess)  # restrict_goto defaults to False
    obs = await tools["goto"]("https://anything.test/")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_unrestricted_when_sources_callback_returns_no_urls():
    """Per design: if no URL in any source, restriction is disabled
    (constraint only applies when there's an anchor)."""
    sess = _fake_session("https://anywhere.test/")
    tools = build_browser_tools(
        sess, restrict_goto=True, allowlist_sources=lambda: ["plain text goal, no urls"]
    )
    obs = await tools["goto"]("https://anywhere.test/")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_uses_domcontentloaded_with_20s_timeout():
    # `networkidle` rarely settles within 10s on adtech-heavy news sites,
    # producing false-negative timeouts on otherwise readable pages
    # (regression: WebVoyager case 110 BBC News, 2026-05-02 trace
    # fbf9c12d56974335a04965ddf0b3e62a — step 0 timed out, step 12 same
    # URL succeeded after 11 wasted steps).
    sess = _fake_session("https://example.test/")
    tools = build_browser_tools(sess)
    await tools["goto"]("https://example.test/")
    sess.page.goto.assert_awaited_once_with(
        "https://example.test/", wait_until="domcontentloaded", timeout=20_000
    )


@pytest.mark.asyncio
async def test_back_uses_domcontentloaded_with_20s_timeout():
    sess = _fake_session("https://example.test/")
    sess.page.go_back = AsyncMock(return_value=None)
    tools = build_browser_tools(sess)
    await tools["back"]()
    sess.page.go_back.assert_awaited_once_with(
        wait_until="domcontentloaded", timeout=20_000
    )
