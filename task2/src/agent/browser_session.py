from __future__ import annotations

import asyncio
import contextlib
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

        for node in res.get("nodes", []):
            role = (node.get("role") or {}).get("value", "") or ""
            name = (node.get("name") or {}).get("value", "") or ""
            value_obj = node.get("value")
            properties = node.get("properties") or []
            focusable = any(
                p.get("name") == "focusable" and p.get("value", {}).get("value") for p in properties
            )
            if role in _INTERACTIVE_ROLES or focusable:
                eid = next_id
                next_id += 1
                entry: dict[str, Any] = {"id": eid, "role": role, "name": name}
                if value_obj is not None:
                    entry["value"] = value_obj.get("value")
                flat.append(entry)
                # Best-effort locator: by role+name where possible, else by role only.
                with contextlib.suppress(Exception):
                    self._element_map[eid] = (
                        self.page.get_by_role(role, name=name)
                        if name
                        else self.page.get_by_role(role)
                    )

        return flat[offset : offset + limit]

    def locator(self, eid: int) -> Locator:
        if eid not in self._element_map:
            raise KeyError(f"unknown element id {eid}")
        return self._element_map[eid]
