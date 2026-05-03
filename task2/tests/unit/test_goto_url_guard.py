"""URL safety guard for the `goto` browser tool.

The agent runs as a public service driving a real Chromium with network
access. A prompt-injecting page can steer the LLM to navigate to a URL the
operator never intended — `file:///etc/passwd`, the cloud metadata
endpoint at `http://169.254.169.254/...`, services on `localhost`, or
intranet IPs reachable from the container. The body of those URLs is then
returned verbatim by the `read` tool and surfaced in the trace.

`goto` MUST refuse such URLs without invoking Playwright at all. This
suite pins the rules: scheme allowlist (http/https only) plus host
denylist (loopback, RFC1918, link-local incl. metadata IPs).
"""

from __future__ import annotations

import pytest

from agent.tools.browser import is_safe_goto_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "FILE:///etc/passwd",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "chrome://settings",
        "chrome-extension://abc/page.html",
        "view-source:https://example.com",
        "about:blank",
        "ws://example.com",
        "ftp://example.com/secret",
    ],
)
def test_blocks_non_http_schemes(url: str) -> None:
    ok, reason = is_safe_goto_url(url)
    assert ok is False
    assert reason  # human-readable reason


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "https://localhost:8000/",
        "http://LOCALHOST/",
        "http://127.0.0.1/",
        "http://127.255.255.255/",
        "http://10.0.0.1/internal",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",  # AWS / GCP metadata
        "http://169.254.170.2/v2/credentials",  # ECS task IAM creds
        "http://[::1]/",
        "http://[::1]:8000/",
        "http://0.0.0.0/",
    ],
)
def test_blocks_loopback_private_link_local_hosts(url: str) -> None:
    ok, reason = is_safe_goto_url(url)
    assert ok is False, f"{url!r} should be blocked"
    assert reason


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com/path?q=1",
        "http://example.com/",
        "https://www.google.com/search?q=hi",
        # Public IP (Google DNS) — not in any blocked range.
        "http://8.8.8.8/",
    ],
)
def test_allows_public_http_https(url: str) -> None:
    ok, reason = is_safe_goto_url(url)
    assert ok is True, f"{url!r} should be allowed (got {reason!r})"


@pytest.mark.parametrize("url", ["", "   ", "not a url", "https://", "://example.com"])
def test_blocks_malformed_or_empty(url: str) -> None:
    ok, _ = is_safe_goto_url(url)
    assert ok is False


def test_data_url_is_allowed() -> None:
    """data: URLs are opaque-origin in Chromium and contain only the bytes
    in the URL itself — they cannot reach internal services or local files.
    Blocking them would also break the integration suite, which legitimately
    uses data: URLs to fixture HTML. Pinning this so a future tightening
    doesn't silently break those tests."""
    ok, _ = is_safe_goto_url("data:text/html;base64,PGgxPmhpPC9oMT4=")
    assert ok is True


def test_goto_returns_error_for_blocked_url_without_calling_page() -> None:
    """goto() must short-circuit before hitting Playwright's page.goto."""
    import asyncio

    from agent.tools.browser import build_browser_tools

    calls: list[str] = []

    class _FakePage:
        url = "about:blank"

        async def goto(self, url, **kw):  # pragma: no cover - must NOT be called
            calls.append(url)
            raise AssertionError(f"page.goto should not be invoked for blocked url {url!r}")

    class _FakeSession:
        page = _FakePage()

    tools = build_browser_tools(_FakeSession())  # type: ignore[arg-type]
    obs = asyncio.run(tools["goto"]("file:///etc/passwd"))
    assert obs.startswith("ERROR:")
    assert "file" in obs.lower() or "scheme" in obs.lower() or "blocked" in obs.lower()
    assert calls == []
