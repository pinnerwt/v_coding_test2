"""The server's allowlist_sources closure must include URLs we have visited
this run, even after we navigate away from them, so subsequent goto() calls
back to a click-discovered URL are not blocked. Cap the trail at 64 (FIFO)."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.tools.browser import _extract_urls, _is_goto_allowed, build_browser_tool_list


def test_visit_trail_dequeue_keeps_last_64():
    trail: deque[str] = deque(maxlen=64)
    for i in range(100):
        trail.append(f"https://h{i}.test/")
    assert len(trail) == 64
    assert trail[0] == "https://h36.test/"
    assert trail[-1] == "https://h99.test/"


def test_allowlist_includes_visit_trail():
    goal = "do a thing on https://anchor.test/"
    tape_obs: list[str] = ["clicked id=5", "read offset 0"]
    current_url = "https://elsewhere.test/"
    visited: list[str] = ["https://anchor.test/article/123"]

    sources = [goal, *tape_obs, current_url, *visited]
    allowlist: list[str] = []
    for s in sources:
        allowlist.extend(_extract_urls(s))

    assert _is_goto_allowed("https://anchor.test/article/123", allowlist) is True
    assert _is_goto_allowed("https://anchor.test/article/124", allowlist) is False


@pytest.mark.asyncio
async def test_browser_tools_consume_live_visit_trail():
    """Wire build_browser_tool_list with restrict_goto=True and an
    allowlist_sources closure that returns the live deque. After we
    append a URL to the deque (simulating on_visit firing), a goto()
    to that URL must be allowed even when no other source mentions it.
    """
    visited: deque[str] = deque(maxlen=64)
    # Goal contains a URL so the allowlist is non-empty (otherwise the
    # guard skips entirely). The discovered URL is NOT in the goal.
    goal = "start from https://anchor.test/"

    def allowlist_sources():
        return [goal, *visited]

    browser = MagicMock()
    browser.page = MagicMock()
    browser.page.url = "https://elsewhere.test/"

    async def fake_goto(url, *, wait_until=None, timeout=None):
        browser.page.url = url
        return None

    browser.page.goto = AsyncMock(side_effect=fake_goto)

    tools = build_browser_tool_list(
        browser,
        restrict_goto=True,
        allowlist_sources=allowlist_sources,
    )
    goto_tool = next(t for t in tools if t.name == "goto")

    # Before append: blocked.
    res = await goto_tool.handler(url="https://discovered.test/article/9")
    assert "blocked" in res.lower()

    # Simulate the loop's on_visit callback firing.
    visited.append("https://discovered.test/article/9")

    # After append: allowed (no block-marker; goto reports navigated).
    res2 = await goto_tool.handler(url="https://discovered.test/article/9")
    assert "blocked" not in res2.lower()
    assert "navigated to" in res2.lower()


@pytest.mark.asyncio
async def test_server_threads_visit_trail_into_allowlist(tmp_path, monkeypatch):
    """End-to-end: build_app with restrict_goto=True. First LLM step issues
    goto() to an off-allowlist URL — must be blocked. Second LLM step calls
    a custom marker tool that mutates the visited deque the server owns.
    Without the wiring, there's no way to reach the deque, so we simulate
    the server-side on_visit by stubbing BrowserSession so that .page.url
    returns the click-discovered URL after a click — this gets picked up by
    on_visit and (once wired) into the allowlist for a follow-up goto.
    """
    import json

    import httpx
    from httpx import ASGITransport, AsyncClient

    from agent.config import Config
    from agent.server import build_app

    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "true")
    monkeypatch.setenv("MAX_STEPS", "6")

    discovered = "https://discovered.test/page/42"

    # Scripted LLM: click -> back -> goto(discovered) -> done.
    # The click "navigates" us to `discovered` (via stubbed BrowserSession),
    # on_visit fires and appends to visited_urls. Then back/elsewhere navigates
    # us away. Then goto(discovered) must be allowed (URL only known via the
    # visit trail, not in goal/tape/current_url).
    script = iter(
        [
            ("click", {"id": 1, "reason": "x"}),
            ("goto", {"url": "https://elsewhere.test/", "reason": "x"}),
            ("goto", {"url": discovered, "reason": "x"}),
            ("done", {"status": "succeeded", "answer": "ok", "reason": "x"}),
        ]
    )

    async def llm_handler(request):
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": ""}}]},
            )
        n, a = next(script)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "type": "function",
                                    "function": {
                                        "name": n,
                                        "arguments": json.dumps(a),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(llm_handler)

    # Stub BrowserSession so we don't need a real Playwright browser. The
    # click handler "navigates" the page to `discovered`. Subsequent goto
    # calls update page.url. snapshot returns one clickable element.
    from agent import server as server_mod

    class FakePage:
        def __init__(self):
            self.url = "https://start.test/"
            self.keyboard = MagicMock()

        async def goto(self, url, **kw):
            self.url = url

        async def go_back(self, **kw):
            self.url = "https://start.test/"

        async def evaluate(self, expr):
            if "innerText" in expr:
                return ""
            return "A"

    class FakeSession:
        def __init__(self):
            self.page = FakePage()

        async def start(self):
            return None

        async def close(self):
            return None

        async def snapshot(self, offset=0, limit=50):
            return [{"id": 1, "tag": "a", "text": "go", "href": discovered}]

        def locator(self, _id):
            page = self.page

            class Loc:
                async def count(self):
                    return 1

                async def click(self, **kw):
                    page.url = discovered

                async def evaluate(self, expr, **kw):
                    return "A"

                async def fill(self, *a, **kw):
                    return None

                async def press(self, *a, **kw):
                    return None

                async def select_option(self, *a, **kw):
                    return None

            return Loc()

    monkeypatch.setattr(server_mod, "BrowserSession", FakeSession)

    app = build_app(
        cfg=Config.from_env(),
        data_dir=tmp_path,
        llm_transport=transport,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/run_sync",
            # Goal mentions elsewhere.test (so the intermediate goto succeeds)
            # but NOT `discovered`. Only the visit-trail wiring can let the
            # final goto to `discovered` through.
            json={"goal": "navigate via https://elsewhere.test/ then come back"},
        )
        assert r.status_code == 200

    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert traces, "no trace written"
    events = [json.loads(line) for line in traces[0].read_text().splitlines()]

    # The decisive assertion: the goto to `discovered` must NOT be blocked.
    # Without the visit-trail wiring, this URL is not in goal/tape/current_url
    # at the time of the goto step (we navigated to elsewhere.test in between).
    blocked_targets = [
        ev["payload"].get("url") for ev in events if ev.get("type") == "goto_blocked"
    ]
    assert discovered not in blocked_targets, (
        f"goto to discovered URL was blocked; trail not wired. Blocked: {blocked_targets}"
    )
