from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from agent.browser_session import BrowserSession
from agent.tools.registry import Tool

_URL_RE = re.compile(r"https?://[^\s)\"'<>]+")
_TRAILING_PUNCT = ".,;:!?)]}"


def _extract_urls(text: str) -> list[str]:
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0)
        while url and url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
        if url:
            out.append(url)
    return out


def _is_goto_allowed(url: str, allowlist: list[str]) -> bool:
    if not url:
        return False
    u = url.lower()
    for src in allowlist:
        s = (src or "").lower()
        if not s:
            continue
        if u in s:
            return True
    return False


_READ_LIMIT = 1600


def build_browser_tools(
    session: BrowserSession,
    *,
    restrict_goto: bool = True,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Return name->callable map (used in tests).

    The full Tool list is in build_browser_tool_list.
    """

    async def goto(url: str) -> str:
        if restrict_goto and allowlist_sources is not None:
            sources = allowlist_sources()
            allowlist = []
            for s in sources:
                allowlist.extend(_extract_urls(s))
            if allowlist and not _is_goto_allowed(url, allowlist):
                return (
                    f"ERROR: blocked goto to {url} — URL not present in prior "
                    "observations or goal. Use list_interactive + click to navigate."
                )
        try:
            await session.page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            return f"navigated to {session.page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def back() -> str:
        try:
            await session.page.go_back(wait_until="domcontentloaded", timeout=20_000)
            return f"back to {session.page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def read(offset: int = 0) -> str:
        try:
            text = await session.page.evaluate("document.body.innerText")
            return text[offset : offset + _READ_LIMIT]
        except Exception as e:
            return f"ERROR: {e}"

    async def read_grep(pattern: str, window: int = 200) -> str:
        try:
            text = await session.page.evaluate("document.body.innerText")
            idx = text.lower().find(pattern.lower())
            if idx < 0:
                return f"NOT FOUND: {pattern!r}"
            start = max(0, idx - window)
            end = min(len(text), idx + len(pattern) + window)
            return text[start:end]
        except Exception as e:
            return f"ERROR: {e}"

    async def list_interactive(offset: int = 0, limit: int = 50) -> str:
        try:
            entries = await session.snapshot(offset=offset, limit=limit)
            return json.dumps(entries, ensure_ascii=False)
        except Exception as e:
            return f"ERROR: {e}"

    async def click(id: int) -> str:
        try:
            loc = session.locator(id)
            try:
                tag = await loc.evaluate("el => el.tagName")
            except Exception:
                tag = ""
            if tag == "SELECT":
                return (
                    f"ERROR: id={id} is a <select>; clicking it does not open a "
                    f"DOM-visible dropdown. Use select_option(id={id}, value=<one of "
                    "the entries from list_interactive's `options` field>) instead."
                )
            try:
                await loc.click(timeout=3000)
            except Exception:
                await loc.evaluate("el => el.click()")
            return f"clicked id={id}"
        except Exception as e:
            return f"ERROR: {e}"

    async def type_(id: int, text: str, submit: bool = False) -> str:
        try:
            loc = session.locator(id)
            await loc.fill(text)
            if submit:
                await loc.press("Enter")
            return f"typed into id={id}{' and submitted' if submit else ''}"
        except Exception as e:
            return f"ERROR: {e}"

    async def select_option(id: int, value: str) -> str:
        try:
            loc = session.locator(id)
            await loc.select_option(value, timeout=5_000)
            return f"selected {value!r} on id={id}"
        except Exception as e:
            return f"ERROR: {e}"

    async def press_key(key: str) -> str:
        try:
            await session.page.keyboard.press(key)
            return f"pressed {key}"
        except Exception as e:
            return f"ERROR: {e}"

    return {
        "goto": goto,
        "back": back,
        "read": read,
        "read_grep": read_grep,
        "list_interactive": list_interactive,
        "click": click,
        "type": type_,
        "select_option": select_option,
        "press_key": press_key,
    }


def build_browser_tool_list(
    session: BrowserSession,
    *,
    restrict_goto: bool = True,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> list[Tool]:
    fns = build_browser_tools(
        session, restrict_goto=restrict_goto, allowlist_sources=allowlist_sources
    )
    return [
        Tool(
            "goto",
            "Navigate to a URL.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "thought": {"type": "string"},
                },
                "required": ["url"],
            },
            fns["goto"],
        ),
        Tool(
            "back",
            "Go back in browser history.",
            {
                "type": "object",
                "properties": {"thought": {"type": "string"}},
            },
            fns["back"],
        ),
        Tool(
            "read",
            "Read up to 1600 chars of visible page text from offset.",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "default": 0},
                    "thought": {"type": "string"},
                },
            },
            fns["read"],
        ),
        Tool(
            "read_grep",
            "Find first case-insensitive occurrence of pattern; return centered window.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "window": {"type": "integer", "default": 200},
                    "thought": {"type": "string"},
                },
                "required": ["pattern"],
            },
            fns["read_grep"],
        ),
        Tool(
            "list_interactive",
            "List interactive elements with assigned IDs (paginated).",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": 50},
                    "thought": {"type": "string"},
                },
            },
            fns["list_interactive"],
        ),
        Tool(
            "click",
            "Click element by ID from list_interactive.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "thought": {"type": "string"},
                },
                "required": ["id"],
            },
            fns["click"],
        ),
        Tool(
            "type",
            "Fill an input by ID; optionally press Enter to submit.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean", "default": False},
                    "thought": {"type": "string"},
                },
                "required": ["id", "text"],
            },
            fns["type"],
        ),
        Tool(
            "select_option",
            "Choose a value on a <select> element by ID.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "value": {"type": "string"},
                    "thought": {"type": "string"},
                },
                "required": ["id", "value"],
            },
            fns["select_option"],
        ),
        Tool(
            "press_key",
            "Press a keyboard key (e.g. Escape, Tab, ArrowDown).",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "thought": {"type": "string"},
                },
                "required": ["key"],
            },
            fns["press_key"],
        ),
    ]
