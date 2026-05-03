"""click()/type()/select_option() must fail in well under 1 second when
the eid no longer maps to a live DOM element.

Before this change, click() invoked Locator.evaluate(...) without timeout=,
so Playwright waited 30s for the element. Triple that for the
tagName-probe + click-fallback chain and a single bad eid burned 60+s."""
from __future__ import annotations

import time

import pytest

from agent.tools.browser import build_browser_tools


class _DeadLocator:
    """A Locator whose count() returns 0 — element no longer in DOM."""
    async def count(self) -> int:
        return 0

    async def evaluate(self, *_a, **_k):
        # If anything reaches evaluate, the test fails wall-time-wise.
        # Simulate Playwright's slow wait — shortened from 31s to 2s so the
        # red-bar dev cycle doesn't burn 30s. Test contract is elapsed < 1.0,
        # so a 2s sleep still proves the old slow path before the fast-fail.
        import asyncio
        await asyncio.sleep(2)
        raise RuntimeError("Locator.evaluate: Timeout 30000ms exceeded")

    async def click(self, **_k):
        await self.evaluate()

    async def fill(self, *_a, **_k):
        await self.evaluate()

    async def select_option(self, *_a, **_k):
        await self.evaluate()

    async def press(self, *_a, **_k):
        await self.evaluate()


class _LiveLocator:
    """A Locator whose count() returns 1 and whose actions succeed instantly."""
    def __init__(self):
        self.click_calls = 0
        self.fill_calls = 0
        self.select_calls = 0

    async def count(self) -> int:
        return 1

    async def evaluate(self, expr, *_a, **_k):
        if "tagName" in expr:
            return "DIV"
        return None

    async def click(self, **_k):
        self.click_calls += 1

    async def fill(self, *_a, **_k):
        self.fill_calls += 1

    async def press(self, *_a, **_k):
        pass

    async def select_option(self, *_a, **_k):
        self.select_calls += 1


class _Sess:
    def __init__(self, locators: dict[int, object]):
        self._locs = locators

    def locator(self, eid):
        return self._locs[eid]

    @property
    def page(self):
        raise RuntimeError("not used")


@pytest.mark.asyncio
async def test_click_fast_fails_when_count_zero():
    sess = _Sess({3: _DeadLocator()})
    tools = build_browser_tools(sess, restrict_goto=False)
    t0 = time.monotonic()
    obs = await tools["click"](id=3)
    elapsed = time.monotonic() - t0
    assert "no longer in DOM" in obs
    assert "list_interactive" in obs
    assert elapsed < 1.0, f"click took {elapsed:.2f}s — must fast-fail"


@pytest.mark.asyncio
async def test_click_proceeds_when_count_positive():
    loc = _LiveLocator()
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["click"](id=3)
    assert obs == "clicked id=3"
    assert loc.click_calls == 1


@pytest.mark.asyncio
async def test_type_fast_fails_when_count_zero():
    sess = _Sess({3: _DeadLocator()})
    tools = build_browser_tools(sess, restrict_goto=False)
    t0 = time.monotonic()
    obs = await tools["type"](id=3, text="hi", submit=False)
    elapsed = time.monotonic() - t0
    assert "no longer in DOM" in obs
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_type_proceeds_when_count_positive():
    loc = _LiveLocator()
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["type"](id=3, text="hi", submit=False)
    assert "typed into id=3" in obs
    assert loc.fill_calls == 1


@pytest.mark.asyncio
async def test_select_option_fast_fails_when_count_zero():
    sess = _Sess({3: _DeadLocator()})
    tools = build_browser_tools(sess, restrict_goto=False)
    t0 = time.monotonic()
    obs = await tools["select_option"](id=3, value="x")
    elapsed = time.monotonic() - t0
    assert "no longer in DOM" in obs
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_select_option_proceeds_when_count_positive():
    loc = _LiveLocator()
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["select_option"](id=3, value="x")
    assert "selected 'x' on id=3" in obs
    assert loc.select_calls == 1
