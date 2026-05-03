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
        # Clear stale data-agent-eid attributes from any prior snapshot so a
        # surviving DOM node can't shadow a fresh id with an old one.
        with contextlib.suppress(Exception):
            await self.page.evaluate(
                "() => document.querySelectorAll('[data-agent-eid]')"
                ".forEach(el => el.removeAttribute('data-agent-eid'))"
            )
        cdp = await self._ctx.new_cdp_session(self.page)
        try:
            await cdp.send("Accessibility.enable")
            res = await cdp.send("Accessibility.getFullAXTree")

            flat: list[dict[str, Any]] = []
            self._element_map.clear()
            next_id = 0

            def _ax_prop(n: dict[str, Any], key: str) -> Any:
                for prop in n.get("properties", []) or []:
                    if prop.get("name") == key:
                        return (prop.get("value") or {}).get("value")
                return None

            nodes = res.get("nodes", []) or []
            # Reverse map child→parent (CDP's AX nodes only carry childIds).
            parent_of: dict[str, str] = {}
            for n in nodes:
                pid = n.get("nodeId")
                for cid in n.get("childIds", []) or []:
                    parent_of[cid] = pid
            expanded_listbox_ids: set[str] = set()
            # Map backendDOMNodeId → AX nodeId so we can resolve `controls` relations.
            ax_id_by_backend: dict[Any, str] = {}
            for n in nodes:
                bdid = n.get("backendDOMNodeId")
                nid = n.get("nodeId")
                if bdid is not None and nid is not None:
                    ax_id_by_backend[bdid] = nid
            for n in nodes:
                role_n = (n.get("role") or {}).get("value", "") or ""
                # Direct case (covers any future Chromium fix where role=listbox
                # carries expanded).
                if role_n == "listbox" and _ax_prop(n, "expanded") is True:
                    expanded_listbox_ids.add(n.get("nodeId"))
                # Bridging case: combobox.expanded=true → listbox via `controls`
                # relation. Chromium drops `aria-expanded` on role=listbox (not a
                # valid ARIA state for that role), so we follow the combobox.
                if role_n == "combobox" and _ax_prop(n, "expanded") is True:
                    for prop in n.get("properties", []) or []:
                        if prop.get("name") == "controls":
                            for rn in (prop.get("value") or {}).get("relatedNodes", []) or []:
                                bdid = rn.get("backendDOMNodeId")
                                if bdid in ax_id_by_backend:
                                    expanded_listbox_ids.add(ax_id_by_backend[bdid])

            # Each interactive AX node carries a backendDOMNodeId pointing
            # at its real DOM element. We resolve that to a Runtime
            # objectId and tag the element with `data-agent-eid="<eid>"`,
            # then drive every subsequent action through a CSS locator on
            # that attribute. This sidesteps the (role, name)+nth ordering
            # bug that hit canirun.ai (case 113): the AX-tree enumeration
            # order is not guaranteed to align with Playwright's role
            # matcher when the AX tree contains synthetic nodes.
            for node in nodes:
                role = (node.get("role") or {}).get("value", "") or ""
                if role == "option":
                    cur = parent_of.get(node.get("nodeId"))
                    for _ in range(64):
                        if cur is None or cur in expanded_listbox_ids:
                            break
                        cur = parent_of.get(cur)
                    if cur is None or cur not in expanded_listbox_ids:
                        continue
                elif role not in _INTERACTIVE_ROLES:
                    continue
                bnid = node.get("backendDOMNodeId")
                if not bnid:
                    continue
                try:
                    resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": bnid})
                    object_id = resolved["object"]["objectId"]
                except Exception:
                    continue
                eid = next_id
                try:
                    try:
                        await cdp.send(
                            "Runtime.callFunctionOn",
                            {
                                "functionDeclaration": (
                                    "function(id){ this.setAttribute('data-agent-eid', id);}"
                                ),
                                "objectId": object_id,
                                "arguments": [{"value": str(eid)}],
                            },
                        )
                    except Exception:
                        continue
                    name = (node.get("name") or {}).get("value", "") or ""
                    entry: dict[str, Any] = {"id": eid, "role": role, "name": name}
                    value_obj = node.get("value")
                    if value_obj is not None:
                        entry["value"] = value_obj.get("value")
                    if role == "link":
                        with contextlib.suppress(Exception):
                            href = await cdp.send(
                                "Runtime.callFunctionOn",
                                {
                                    "functionDeclaration": "function(){ return this.href || ''; }",
                                    "objectId": object_id,
                                    "returnByValue": True,
                                },
                            )
                            raw = (href.get("result") or {}).get("value") or ""
                            if raw:
                                entry["href"] = raw
                    if role == "textbox":
                        with contextlib.suppress(Exception):
                            res = await cdp.send(
                                "Runtime.callFunctionOn",
                                {
                                    "functionDeclaration": (
                                        "function(){ return"
                                        " this.getAttribute('placeholder') || ''; }"
                                    ),
                                    "objectId": object_id,
                                    "returnByValue": True,
                                },
                            )
                            raw = (res.get("result") or {}).get("value") or ""
                            if raw:
                                entry["placeholder"] = raw
                    for prop_name in ("expanded", "disabled", "checked", "selected"):
                        pv = _ax_prop(node, prop_name)
                        # `checked` is emitted as a tristate string ("true"/"false"/"mixed");
                        # `expanded`/`disabled`/`selected` are plain booleans. Normalize.
                        if pv is True or pv == "true":
                            entry[prop_name] = True
                    flat.append(entry)
                    self._element_map[eid] = self.page.locator(f'[data-agent-eid="{eid}"]')
                    next_id += 1
                finally:
                    with contextlib.suppress(Exception):
                        await cdp.send("Runtime.releaseObject", {"objectId": object_id})
        finally:
            await cdp.detach()

        # For each combobox that resolves to a real <select>, surface its
        # <option> text values inline. Without this the model has to guess
        # what `value` to pass to click(id, value=...) and Playwright blocks
        # for 30s on a non-match (canirun.ai bench case 113).
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
