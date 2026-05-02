from __future__ import annotations

import json
from typing import Any

from agent.browser_session import BrowserSession
from agent.tools.registry import Tool

_READ_LIMIT = 2000


def build_browser_tools(session: BrowserSession) -> dict[str, Any]:
    """Return name->callable map (used in tests).

    The full Tool list is in build_browser_tool_list.
    """

    async def goto(url: str) -> str:
        try:
            await session.page.goto(url, wait_until="networkidle", timeout=10_000)
            return f"navigated to {session.page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def back() -> str:
        try:
            await session.page.go_back(wait_until="networkidle", timeout=10_000)
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

    return {
        "goto": goto,
        "back": back,
        "read": read,
        "read_grep": read_grep,
        "list_interactive": list_interactive,
    }


def build_browser_tool_list(session: BrowserSession) -> list[Tool]:
    fns = build_browser_tools(session)
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
            "Read up to 2000 chars of visible page text from offset.",
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
    ]
