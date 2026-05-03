from __future__ import annotations

import re
from collections.abc import Callable, Iterable
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


def _build_allowlist_sources_from_tape(
    *,
    tape: Iterable[dict],
    page_url: str,
    goal: str,
    visited_urls: Iterable[str],
) -> list[str]:
    """Build the goto-grounding allowlist sources from narrative history.

    Per-step contributions are: the step's `url`, every string-typed value in
    `args`, and the `reason` text. Obs strings are intentionally NOT included
    (they are no longer in the agent's context window beyond the recent-3
    raw obs, so grounding against them would be too narrow).
    """
    sources: list[str] = [goal or ""]
    for step in tape:
        url = step.get("url", "")
        if url:
            sources.append(url)
        args = step.get("args") or {}
        if isinstance(args, dict):
            for v in args.values():
                if isinstance(v, str):
                    sources.append(v)
        reason = step.get("reason", "")
        if reason:
            sources.append(reason)
    sources.append(page_url or "")
    sources.extend(visited_urls)
    return sources


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

    async def read_grep(
        pattern: str,
        context: int = 80,
        max_matches: int = 10,
        offset: int = 0,
    ) -> str:
        try:
            text = await session.page.evaluate("document.body.innerText")
        except Exception as e:
            return f"ERROR: {e}"
        n = len(text)
        if not pattern:
            return f"NO MATCH for '' in page text ({n} chars)."
        needle = pattern.lower()
        hay = text.lower()
        positions: list[int] = []
        i = 0
        while True:
            j = hay.find(needle, i)
            if j < 0:
                break
            positions.append(j)
            i = j + max(1, len(needle))
        total = len(positions)
        if total == 0:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            seen: set[str] = set()
            vocab: list[str] = []
            for ln in sorted(lines, key=len, reverse=True):
                key = ln.lower()
                if key in seen:
                    continue
                seen.add(key)
                vocab.append(ln if len(ln) <= 40 else ln[:37] + "…")
                if len(vocab) >= 5:
                    break
            if vocab:
                quoted = ", ".join(f'"{v}"' for v in vocab)
                return (
                    f"NO MATCH for {pattern!r} in page text ({n} chars). "
                    f"The text contains: {quoted} — try one of these, or call done()."
                )
            return f"NO MATCH for {pattern!r} in page text ({n} chars)."
        if offset >= total:
            return (
                f"NO MORE MATCHES for {pattern!r} at offset={offset} "
                f"({total} total in page). Call read_grep with offset=0..{total - 1} or done()."
            )
        end = min(total, offset + max_matches)
        shown = positions[offset:end]
        snippets: list[str] = []
        for p in shown:
            s = max(0, p - context)
            e = min(n, p + len(pattern) + context)
            chunk = text[s:e].replace("\n", "\\n")
            snippets.append(f"  [@{p}] …{chunk}…")
        if total == 1:
            header = f"1 match for {pattern!r} in page text ({n} chars):"
            body = "\n".join(snippets)
            return f"{header}\n{body}"
        header = (
            f"{total} matches for {pattern!r} in page text ({n} chars). "
            f"Showing matches {offset}-{end - 1} of {total}:"
        )
        tail = ""
        if end < total:
            more = positions[end:]
            more_preview = ", ".join(str(p) for p in more[:5])
            if len(more) > 5:
                more_preview += f", … (+{len(more) - 5} more)"
            tail = (
                f"\n({len(more)} more matches at offsets {more_preview} — call "
                f"read_grep(pattern={pattern!r}, offset={end}) for the rest.)"
            )
        return f"{header}\n" + "\n".join(snippets) + tail

    async def list_interactive(offset: int = 0, limit: int = 50) -> str:
        try:
            entries = await session.snapshot(offset=offset, limit=limit)
            return (
                f"snapshot taken: {len(entries)} elements (offset={offset}, "
                f"limit={limit}); see '## Interactive elements (live)' "
                "section in user message for the current id list"
            )
        except Exception as e:
            return f"ERROR: {e}"

    async def click(id: int, value: str | None = None) -> str:
        try:
            loc = session.locator(id)
            if await loc.count() == 0:
                return (
                    f"ERROR: id={id} no longer in DOM. Call list_interactive "
                    "to refresh — eids are reassigned each snapshot."
                )
            try:
                tag = await loc.evaluate("el => el.tagName", timeout=3000)
            except Exception:
                tag = ""
            is_select = tag == "SELECT"
            if is_select and value is None:
                return (
                    f"ERROR: id={id} is a <select>; pass value=<one of the "
                    "`options` entries from list_interactive>."
                )
            if not is_select and value is not None:
                return (
                    f"ERROR: id={id} is not a <select>; `value` is only for "
                    "<select>. Call list_interactive to refresh — eids are "
                    "reassigned each snapshot."
                )
            if is_select:
                await loc.select_option(value, timeout=5_000)
                return f"selected {value!r} on id={id}"
            try:
                await loc.click(timeout=3000)
            except Exception:
                await loc.evaluate("el => el.click()", timeout=3000)
            return f"clicked id={id}"
        except Exception as e:
            return f"ERROR: {e}"

    async def type_(id: int, text: str, submit: bool = False) -> str:
        try:
            loc = session.locator(id)
            if await loc.count() == 0:
                return (
                    f"ERROR: id={id} no longer in DOM. Call list_interactive "
                    "to refresh — eids are reassigned each snapshot."
                )
            await loc.fill(text)
            if submit:
                await loc.press("Enter")
            return f"typed into id={id}{' and submitted' if submit else ''}"
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
                    "reason": {"type": "string"},
                },
                "required": ["url", "reason"],
            },
            fns["goto"],
        ),
        Tool(
            "back",
            "Go back in browser history.",
            {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
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
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
            fns["read"],
        ),
        Tool(
            "read_grep",
            "Find all case-insensitive occurrences of pattern; returns a "
            "paginated index with character-offset markers. context controls "
            "snippet width only; offset skips the first N matches.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "context": {"type": "integer", "default": 80},
                    "max_matches": {"type": "integer", "default": 10},
                    "offset": {"type": "integer", "default": 0},
                    "reason": {"type": "string"},
                },
                "required": ["pattern", "reason"],
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
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
            fns["list_interactive"],
        ),
        Tool(
            "click",
            "Click an element by ID; for <select> elements, pass `value` to choose an option.",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "value": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
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
                    "reason": {"type": "string"},
                },
                "required": ["id", "text", "reason"],
            },
            fns["type"],
        ),
        Tool(
            "press_key",
            "Press a keyboard key (e.g. Escape, Tab, ArrowDown).",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["key", "reason"],
            },
            fns["press_key"],
        ),
    ]
