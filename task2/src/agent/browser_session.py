from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)

_INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "menuitem",
    "tab",
    "switch",
    "slider",
}


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._element_map: dict[int, Locator] = {}

    @property
    def page(self) -> Page:
        assert self._page is not None, "call start() first"
        return self._page

    def lock(self) -> asyncio.Lock:
        return self._lock

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._ctx = await self._browser.new_context()
        self._page = await self._ctx.new_page()

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def snapshot(self, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        # Pull the accessibility tree via CDP — Playwright dropped page.accessibility
        # in newer releases, but the underlying Chrome DevTools Protocol still exposes it.
        assert self._ctx is not None, "call start() first"
        cdp = await self._ctx.new_cdp_session(self.page)
        try:
            await cdp.send("Accessibility.enable")
            res = await cdp.send("Accessibility.getFullAXTree")
        finally:
            await cdp.detach()

        flat: list[dict[str, Any]] = []
        self._element_map.clear()
        next_id = 0
        counts: Counter[tuple[str, str]] = Counter()

        for node in res.get("nodes", []):
            role = (node.get("role") or {}).get("value", "") or ""
            name = (node.get("name") or {}).get("value", "") or ""
            value_obj = node.get("value")
            if role in _INTERACTIVE_ROLES:
                eid = next_id
                next_id += 1
                entry: dict[str, Any] = {"id": eid, "role": role, "name": name}
                if value_obj is not None:
                    entry["value"] = value_obj.get("value")
                flat.append(entry)
                # Disambiguate duplicate (role, name) pairs with .nth(k) so strict mode
                # doesn't choke when multiple elements share the same accessible name.
                key = (role, name)
                k = counts[key]
                counts[key] += 1
                with contextlib.suppress(Exception):
                    base = (
                        self.page.get_by_role(role, name=name)
                        if name
                        else self.page.get_by_role(role)
                    )
                    self._element_map[eid] = base.nth(k)

        # For each combobox that resolves to a real <select>, surface its
        # <option> text values inline. Without this the model has to guess
        # what `value` to pass to select_option and Playwright blocks for
        # 30s on a non-match (canirun.ai bench case 113).
        page_slice = flat[offset : offset + limit]
        for entry in page_slice:
            if entry["role"] != "combobox":
                continue
            loc = self._element_map.get(entry["id"])
            if loc is None:
                continue
            with contextlib.suppress(Exception):
                opts = await loc.evaluate(
                    "el => el.tagName === 'SELECT'"
                    " ? Array.from(el.options).map(o => (o.textContent || '').trim())"
                    " : null"
                )
                if isinstance(opts, list):
                    entry["options"] = opts[:100]

        return page_slice

    def locator(self, eid: int) -> Locator:
        if eid not in self._element_map:
            raise KeyError(f"unknown element id {eid}")
        return self._element_map[eid]
