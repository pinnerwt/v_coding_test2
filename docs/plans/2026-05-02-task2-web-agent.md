# Task 2 — Web Browsing Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a deployable ReAct web-browsing agent (Task 2 of `AI-Coding-Test-EN.md`) per the design at `docs/plans/2026-05-02-task2-web-agent-design.md`.

**Architecture:** Single FastAPI process holds one headless Playwright Chromium and runs a single ReAct loop calling a configurable OpenAI-compatible LLM endpoint with native tool-calling. URL-scoped notes (SQLite) provide cross-session memory; a small summarizer model writes them implicitly. UI streams the (thought, action, observation) tape over WebSocket; a `/replay` page renders saved JSONL traces.

**Tech Stack:** Python 3.11, `uv`, `ruff`, `pytest` + `pytest-asyncio`, FastAPI, `uvicorn`, `httpx` (LLM client), Playwright (Chromium), SQLite (stdlib `sqlite3`), Jinja2 + HTMX/vanilla JS, Docker, Zeabur.

**Source layout (all under `task2/`):**
```
task2/
  pyproject.toml
  uv.lock
  src/agent/
    __init__.py
    config.py
    llm.py
    tools/
      __init__.py
      registry.py
      browser.py        # goto, back, click, type, select_option, press_key, list_interactive, read, read_grep
      meta.py           # note, ask_user_question, done
    browser_session.py  # Playwright wrapper, single-concurrent lock
    notes_store.py      # SQLite URL notes
    summarizer.py
    trace.py            # JSONL writer/reader
    context.py          # prompt assembly
    loop.py             # ReAct loop, stuck-detector, replan, escalate
    server.py           # FastAPI app + WS protocol
    templates/index.html
    templates/replay.html
    static/app.js
  tests/
    unit/
    integration/
    evals/
    fixtures/
  Dockerfile
  README.md
```

**Common conventions:**
- Run all commands from `task2/` unless otherwise noted.
- Linter must be clean before each commit: `uv run ruff check . && uv run ruff format --check .`.
- Conventional commits scoped `feat(task2): …`, `test(task2): …`, etc.
- TDD is non-negotiable per `CLAUDE.md`. Red → Green → Refactor on every task.

---

### Task 1: Scaffold the `task2/` package

**Files:**
- Create: `task2/pyproject.toml`
- Create: `task2/.gitignore`
- Create: `task2/src/agent/__init__.py`
- Create: `task2/tests/__init__.py`
- Create: `task2/README.md` (1-line stub: `# Task 2 — Web Browsing Agent`)

**Step 1: Make the directory and initialise uv**

```bash
mkdir -p task2/src/agent task2/tests/{unit,integration,evals,fixtures}
cd task2
uv init --package --name agent --no-readme
```

**Step 2: Replace generated `pyproject.toml` with our config**

```toml
[project]
name = "agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 3: Add dev deps and verify environment**

```bash
uv add --dev pytest pytest-asyncio ruff
uv sync
uv run pytest --version
uv run ruff --version
```

Expected: both print versions cleanly.

**Step 4: Add `task2/.gitignore`**

```
.venv/
__pycache__/
data/
*.db
.pytest_cache/
.ruff_cache/
```

**Step 5: Commit**

```bash
git add task2/pyproject.toml task2/uv.lock task2/.gitignore task2/src/ task2/tests/ task2/README.md
git commit -m "chore(task2): scaffold package with uv, ruff, pytest"
```

---

### Task 2: Config module (env-driven, no hardcoded providers)

**Files:**
- Create: `task2/src/agent/config.py`
- Test: `task2/tests/unit/test_config.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_config.py
import os
from agent.config import Config


def test_defaults(monkeypatch):
    for k in ("AGENT_MODEL_BASE_URL", "AGENT_MODEL_NAME",
              "SUMMARIZER_MODEL_BASE_URL", "SUMMARIZER_MODEL_NAME",
              "MAX_STEPS", "URL_NOTE_QUERY_STRIP"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.agent_model_base_url == "http://localhost:8090/v1"
    assert cfg.agent_model_name == "qwen3.5-27b"
    assert cfg.summarizer_model_base_url == "http://localhost:8090/v1"
    assert cfg.summarizer_model_name == "qwen3.5-27b"
    assert cfg.max_steps == 50
    assert cfg.url_note_query_strip is True


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MAX_STEPS", "10")
    monkeypatch.setenv("URL_NOTE_QUERY_STRIP", "false")
    cfg = Config.from_env()
    assert cfg.agent_model_base_url == "https://example.test/v1"
    assert cfg.max_steps == 10
    assert cfg.url_note_query_strip is False
```

**Step 2: Run — expect ImportError / fail**

```bash
uv run pytest tests/unit/test_config.py -v
```

**Step 3: Implement minimal `Config`**

```python
# src/agent/config.py
from __future__ import annotations
import os
from dataclasses import dataclass


def _bool(s: str | None, default: bool) -> bool:
    if s is None:
        return default
    return s.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    agent_model_base_url: str
    agent_model_name: str
    summarizer_model_base_url: str
    summarizer_model_name: str
    max_steps: int
    url_note_query_strip: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            agent_model_base_url=os.getenv("AGENT_MODEL_BASE_URL", "http://localhost:8090/v1"),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", "qwen3.5-27b"),
            summarizer_model_base_url=os.getenv("SUMMARIZER_MODEL_BASE_URL", "http://localhost:8090/v1"),
            summarizer_model_name=os.getenv("SUMMARIZER_MODEL_NAME", "qwen3.5-27b"),
            max_steps=int(os.getenv("MAX_STEPS", "50")),
            url_note_query_strip=_bool(os.getenv("URL_NOTE_QUERY_STRIP"), True),
        )
```

**Step 4: Run — expect PASS**

**Step 5: Lint + commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/agent/config.py tests/unit/test_config.py
git commit -m "feat(task2): add env-driven config"
```

---

### Task 3: LLM client wrapper (OpenAI-compatible, reasoning disabled)

**Files:**
- Create: `task2/src/agent/llm.py`
- Test: `task2/tests/unit/test_llm.py`

The wrapper exposes `async chat(messages, tools=None, tool_choice=None, reasoning=False) -> dict` returning the raw assistant message. It uses `httpx.AsyncClient` against `<base_url>/chat/completions`. Reasoning-disabled is passed via `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` (Qwen vLLM/sglang convention).

**Step 1: Add deps**

```bash
uv add httpx
```

**Step 2: Write the failing test**

```python
# tests/unit/test_llm.py
import json
import pytest
from agent.llm import LLMClient


class _FakeTransport:
    def __init__(self):
        self.last_request = None

    async def handle_async_request(self, request):
        import httpx
        self.last_request = request
        body = json.loads(request.content)
        # Echo so test can assert on it
        return httpx.Response(200, json={"_echo": body, "choices": [{"message": {"role": "assistant", "content": "ok"}}]})


@pytest.mark.asyncio
async def test_reasoning_disabled_by_default():
    import httpx
    transport = _FakeTransport()
    client = LLMClient(base_url="http://test/v1", model="m", transport=httpx.MockTransport(transport.handle_async_request))
    msg = await client.chat([{"role": "user", "content": "hi"}])
    sent = transport.last_request
    body = json.loads(sent.content)
    assert body["model"] == "m"
    assert body["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert msg["content"] == "ok"


@pytest.mark.asyncio
async def test_tools_passed_through():
    import httpx
    transport = _FakeTransport()
    client = LLMClient(base_url="http://test/v1", model="m", transport=httpx.MockTransport(transport.handle_async_request))
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    await client.chat([{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto")
    body = json.loads(transport.last_request.content)
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
```

**Step 3: Run — expect FAIL**

**Step 4: Implement**

```python
# src/agent/llm.py
from __future__ import annotations
from typing import Any
import httpx


class LLMClient:
    def __init__(self, base_url: str, model: str, *, timeout: float = 120.0, transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": reasoning}},
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        r = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]

    async def aclose(self) -> None:
        await self._client.aclose()
```

**Step 5: Run — expect PASS**

**Step 6: Lint + commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/agent/llm.py tests/unit/test_llm.py pyproject.toml uv.lock
git commit -m "feat(task2): add OpenAI-compatible LLM client with reasoning-off default"
```

---

### Task 4: URL notes store (SQLite)

**Files:**
- Create: `task2/src/agent/notes_store.py`
- Test: `task2/tests/unit/test_notes_store.py`

Behaviour:
- `get(url) -> str` returns notes (newline-joined) or empty string.
- `append(url, line)` appends `line` plus newline; trims oldest lines so total length ≤ 2048.
- URL key is normalised: `query_strip=True` removes `?...` and fragment.

**Step 1: Write the failing test**

```python
# tests/unit/test_notes_store.py
from agent.notes_store import NotesStore


def test_append_and_get(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.append("https://a.test/x", "first")
    s.append("https://a.test/x", "second")
    assert s.get("https://a.test/x") == "first\nsecond"


def test_query_string_stripped_by_default(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.append("https://a.test/x?token=1", "one")
    assert s.get("https://a.test/x?token=2") == "one"


def test_query_string_kept_when_disabled(tmp_path):
    s = NotesStore(tmp_path / "n.db", query_strip=False)
    s.append("https://a.test/x?a=1", "a")
    assert s.get("https://a.test/x?a=2") == ""


def test_cap_2kb(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    line = "x" * 200
    for _ in range(50):
        s.append("https://a.test/", line)
    assert len(s.get("https://a.test/")) <= 2048
```

**Step 2: Run — FAIL**

**Step 3: Implement**

```python
# src/agent/notes_store.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_MAX_BYTES = 2048


def _normalise(url: str, query_strip: bool) -> str:
    if not query_strip:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class NotesStore:
    def __init__(self, path: Path | str, *, query_strip: bool = True):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._query_strip = query_strip
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS url_notes ("
            "  url TEXT PRIMARY KEY,"
            "  notes TEXT NOT NULL,"
            "  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._conn.commit()

    def get(self, url: str) -> str:
        key = _normalise(url, self._query_strip)
        row = self._conn.execute("SELECT notes FROM url_notes WHERE url = ?", (key,)).fetchone()
        return row[0] if row else ""

    def append(self, url: str, line: str) -> None:
        key = _normalise(url, self._query_strip)
        existing = self.get(url)
        merged = (existing + "\n" + line).strip("\n") if existing else line
        while len(merged.encode()) > _MAX_BYTES and "\n" in merged:
            merged = merged.split("\n", 1)[1]
        self._conn.execute(
            "INSERT INTO url_notes(url, notes, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(url) DO UPDATE SET notes=excluded.notes, updated_at=CURRENT_TIMESTAMP",
            (key, merged),
        )
        self._conn.commit()
```

**Step 4: PASS — commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/agent/notes_store.py tests/unit/test_notes_store.py
git commit -m "feat(task2): add SQLite URL notes store"
```

---

### Task 5: Trace JSONL writer/reader

**Files:**
- Create: `task2/src/agent/trace.py`
- Test: `task2/tests/unit/test_trace.py`

API:
- `TraceWriter(path).write(event: dict)` → appends one JSON line.
- `read_trace(path) -> list[dict]` → loads all events.
- Event shape: `{"type": "step"|"question"|"answer"|"done"|"error", "ts": iso8601, "payload": {...}}`.

**Step 1: Failing test**

```python
# tests/unit/test_trace.py
from agent.trace import TraceWriter, read_trace


def test_round_trip(tmp_path):
    p = tmp_path / "t.jsonl"
    w = TraceWriter(p)
    w.write({"type": "step", "payload": {"n": 1, "action": "goto", "args": {"url": "x"}}})
    w.write({"type": "done", "payload": {"status": "success", "answer": "ok"}})
    events = read_trace(p)
    assert [e["type"] for e in events] == ["step", "done"]
    assert events[0]["payload"]["action"] == "goto"
    assert all("ts" in e for e in events)
```

**Step 2: Run — FAIL**

**Step 3: Implement**

```python
# src/agent/trace.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        out = dict(event)
        out.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with self._path.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


def read_trace(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
```

**Step 4: PASS — commit**

```bash
git add src/agent/trace.py tests/unit/test_trace.py
git commit -m "feat(task2): add JSONL trace writer/reader"
```

---

### Task 6: Tool registry + JSON schemas

**Files:**
- Create: `task2/src/agent/tools/__init__.py`
- Create: `task2/src/agent/tools/registry.py`
- Test: `task2/tests/unit/test_registry.py`

The registry holds `Tool(name, description, parameters_json_schema, handler)` records. `to_openai_tools()` emits the `tools=[...]` array for the LLM. Handlers are async.

**Step 1: Failing test**

```python
# tests/unit/test_registry.py
import pytest
from agent.tools.registry import Tool, ToolRegistry


@pytest.mark.asyncio
async def test_register_and_call():
    r = ToolRegistry()

    async def handler(*, x: int) -> int:
        return x + 1

    r.register(Tool(
        name="inc",
        description="increment",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=handler,
    ))
    out = r.to_openai_tools()
    assert out[0]["function"]["name"] == "inc"
    assert out[0]["function"]["parameters"]["required"] == ["x"]
    result = await r.call("inc", {"x": 1})
    assert result == 2


@pytest.mark.asyncio
async def test_unknown_tool_raises():
    r = ToolRegistry()
    with pytest.raises(KeyError):
        await r.call("nope", {})
```

**Step 2: FAIL**

**Step 3: Implement**

```python
# src/agent/tools/registry.py
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self._tools.values()
        ]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(name)
        return await self._tools[name].handler(**args)

    def names(self) -> list[str]:
        return list(self._tools)
```

**Step 4: PASS — commit**

```bash
git add src/agent/tools/__init__.py src/agent/tools/registry.py tests/unit/test_registry.py
git commit -m "feat(task2): add tool registry with OpenAI tool-schema export"
```

---

### Task 7: Browser session wrapper (Playwright, single-concurrent)

**Files:**
- Create: `task2/src/agent/browser_session.py`
- Test: `task2/tests/integration/test_browser_session.py`

Wraps `playwright.async_api`. Exposes:
- `async start()` / `async close()`
- `async with session.lock(): ...` — `asyncio.Lock` so only one ReAct loop drives the browser at a time.
- `page` property after `start()`.
- `async snapshot()` returns the accessibility tree as a list of dicts (used by `list_interactive`).
- Element ID map: `snapshot()` assigns ints to interactive elements and stores Playwright `Locator`s in an internal map keyed by ID; subsequent `click(id)` looks up the locator.

**Step 1: Add deps and install Chromium**

```bash
uv add playwright
uv run playwright install chromium
```

**Step 2: Failing test (integration — uses real Chromium against a data URL)**

```python
# tests/integration/test_browser_session.py
import pytest
from agent.browser_session import BrowserSession


HTML = """<!doctype html><html><body>
<button id=b1>Hello</button>
<input id=i1 type=text aria-label="search">
<a id=a1 href="#x">Link</a>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_assigns_ids():
    s = BrowserSession()
    await s.start()
    try:
        await s.page.set_content(HTML)
        snap = await s.snapshot()
        names = {e["name"] for e in snap}
        roles = {e["role"] for e in snap}
        assert "Hello" in names
        assert "search" in names or "" in names  # input may have empty name
        assert "button" in roles
        assert "link" in roles
        # IDs are unique ints
        ids = [e["id"] for e in snap]
        assert ids == sorted(set(ids))
    finally:
        await s.close()
```

**Step 3: FAIL**

**Step 4: Implement**

```python
# src/agent/browser_session.py
from __future__ import annotations
import asyncio
from typing import Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Locator

_INTERACTIVE_ROLES = {"button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem", "tab", "switch", "slider"}


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
        if self._ctx: await self._ctx.close()
        if self._browser: await self._browser.close()
        if self._pw: await self._pw.stop()

    async def snapshot(self, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        # Use Playwright's built-in accessibility snapshot, then flatten.
        tree = await self.page.accessibility.snapshot(interesting_only=True) or {}
        flat: list[dict] = []
        self._element_map.clear()
        next_id = 0

        def visit(node: dict, path: list[int]) -> None:
            nonlocal next_id
            role = node.get("role", "")
            name = node.get("name", "") or ""
            value = node.get("value")
            if role in _INTERACTIVE_ROLES or node.get("focusable"):
                eid = next_id
                next_id += 1
                entry = {"id": eid, "role": role, "name": name}
                if value is not None:
                    entry["value"] = value
                flat.append(entry)
                # Best-effort locator: by role+name where possible, else by name only
                try:
                    self._element_map[eid] = self.page.get_by_role(role, name=name) if name else self.page.get_by_role(role)
                except Exception:
                    pass
            for child in node.get("children", []) or []:
                visit(child, path)

        visit(tree, [])
        return flat[offset:offset + limit]

    def locator(self, eid: int) -> Locator:
        if eid not in self._element_map:
            raise KeyError(f"unknown element id {eid}")
        return self._element_map[eid]
```

**Step 5: PASS**

**Step 6: Commit**

```bash
git add src/agent/browser_session.py tests/integration/test_browser_session.py pyproject.toml uv.lock
git commit -m "feat(task2): add Playwright session wrapper with a11y snapshot + id map"
```

---

### Task 8: Browser tools — navigation + reading

**Files:**
- Create: `task2/src/agent/tools/browser.py`
- Test: `task2/tests/integration/test_browser_tools_nav_read.py`

Tools wrapped here: `goto`, `back`, `list_interactive`, `read`, `read_grep`. Each returns a string observation (LLM-friendly). Errors are caught and returned as `"ERROR: <message>"`.

**Step 1: Failing test**

```python
# tests/integration/test_browser_tools_nav_read.py
import pytest
from agent.browser_session import BrowserSession
from agent.tools.browser import build_browser_tools

HTML = """<!doctype html><html><head><title>T</title></head><body>
<h1>Welcome</h1><p>The quick brown fox jumps over the lazy dog. Needle here. End.</p>
<button>Go</button></body></html>"""


@pytest.mark.asyncio
async def test_goto_read_grep_list(tmp_path):
    s = BrowserSession()
    await s.start()
    try:
        # Serve HTML via data URL
        url = "data:text/html;base64," + __import__("base64").b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s)

        obs = await tools["goto"](url=url)
        assert "navigated" in obs.lower()

        text = await tools["read"]()
        assert "Welcome" in text and len(text) <= 2000

        grep = await tools["read_grep"](pattern="needle", window=20)
        assert "Needle" in grep

        listing = await tools["list_interactive"]()
        assert "button" in listing.lower() and "Go" in listing
    finally:
        await s.close()
```

**Step 2: FAIL**

**Step 3: Implement**

```python
# src/agent/tools/browser.py
from __future__ import annotations
import json
from typing import Any
from agent.browser_session import BrowserSession
from agent.tools.registry import Tool

_READ_LIMIT = 2000


def build_browser_tools(session: BrowserSession) -> dict[str, Any]:
    """Return name->callable map (used in tests). The full Tool list is in build_browser_tool_list."""
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
            return text[offset:offset + _READ_LIMIT]
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

    return {"goto": goto, "back": back, "read": read, "read_grep": read_grep, "list_interactive": list_interactive}


def build_browser_tool_list(session: BrowserSession) -> list[Tool]:
    fns = build_browser_tools(session)
    return [
        Tool("goto", "Navigate to a URL.",
             {"type": "object", "properties": {"url": {"type": "string"}, "thought": {"type": "string"}}, "required": ["url"]},
             fns["goto"]),
        Tool("back", "Go back in browser history.",
             {"type": "object", "properties": {"thought": {"type": "string"}}}, fns["back"]),
        Tool("read", "Read up to 2000 chars of visible page text from offset.",
             {"type": "object", "properties": {"offset": {"type": "integer", "default": 0}, "thought": {"type": "string"}}}, fns["read"]),
        Tool("read_grep", "Find first case-insensitive occurrence of pattern; return centered window.",
             {"type": "object", "properties": {"pattern": {"type": "string"}, "window": {"type": "integer", "default": 200}, "thought": {"type": "string"}}, "required": ["pattern"]},
             fns["read_grep"]),
        Tool("list_interactive", "List interactive elements with assigned IDs (paginated).",
             {"type": "object", "properties": {"offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 50}, "thought": {"type": "string"}}},
             fns["list_interactive"]),
    ]
```

`thought` is accepted in every schema but ignored by handlers (it's the agent's reasoning surface; the loop logs it).

**Step 4: PASS — commit**

```bash
git add src/agent/tools/browser.py tests/integration/test_browser_tools_nav_read.py
git commit -m "feat(task2): add navigation and reading browser tools"
```

---

### Task 9: Browser tools — interaction (click, type, select_option, press_key)

**Files:**
- Modify: `task2/src/agent/tools/browser.py`
- Test: `task2/tests/integration/test_browser_tools_interact.py`

Add `click(id)`, `type(id, text, submit=False)`, `select_option(id, value)`, `press_key(key)`. `click` retries with JS dispatch on failure.

**Step 1: Failing test**

```python
# tests/integration/test_browser_tools_interact.py
import pytest, base64
from agent.browser_session import BrowserSession
from agent.tools.browser import build_browser_tools

HTML = """<!doctype html><html><body>
<button id=b onclick="document.title='clicked'">Go</button>
<input id=i type=text>
<select id=s><option>a</option><option value=b>B</option></select>
<script>
document.getElementById('i').addEventListener('keydown', e => { if (e.key==='Enter') document.title='submitted'; });
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_click_type_select_press(tmp_path):
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s)
        await tools["goto"](url=url)
        snap = await s.snapshot()
        by_role = {(e["role"], e["name"]): e["id"] for e in snap}

        b_id = by_role[("button", "Go")]
        await tools["click"](id=b_id)
        assert await s.page.title() == "clicked"

        i_id = by_role[("textbox", "")]
        await tools["type"](id=i_id, text="hello", submit=True)
        assert await s.page.title() == "submitted"
        assert await s.page.input_value("#i") == "hello"

        s_id = by_role[("combobox", "")]
        await tools["select_option"](id=s_id, value="b")
        assert await s.page.eval_on_selector("#s", "el => el.value") == "b"

        await tools["press_key"](key="Escape")  # smoke test, no assertion needed
    finally:
        await s.close()
```

**Step 2: FAIL**

**Step 3: Extend `build_browser_tools` and add Tool entries**

```python
# add inside build_browser_tools():

async def click(id: int) -> str:
    try:
        loc = session.locator(id)
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
        await loc.select_option(value)
        return f"selected {value!r} on id={id}"
    except Exception as e:
        return f"ERROR: {e}"

async def press_key(key: str) -> str:
    try:
        await session.page.keyboard.press(key)
        return f"pressed {key}"
    except Exception as e:
        return f"ERROR: {e}"

# extend the returned dict:
return {..., "click": click, "type": type_, "select_option": select_option, "press_key": press_key}
```

And in `build_browser_tool_list` append four new `Tool(...)` entries with matching JSON schemas (each accepts a `thought` string).

**Step 4: PASS — commit**

```bash
git add src/agent/tools/browser.py tests/integration/test_browser_tools_interact.py
git commit -m "feat(task2): add click/type/select/press interaction tools"
```

---

### Task 10: Meta tools (`note`, `ask_user_question`, `done`)

**Files:**
- Create: `task2/src/agent/tools/meta.py`
- Test: `task2/tests/unit/test_meta_tools.py`

`ask_user_question` is special: it must block the loop until the UI delivers an answer. Implement as `await question_channel.ask(question) -> str` where `question_channel` is an `asyncio.Future`-based queue. `done` raises a `LoopDone` exception caught by the loop. `note` writes to the `NotesStore` and returns `"noted"`.

**Step 1: Failing test**

```python
# tests/unit/test_meta_tools.py
import asyncio
import pytest
from agent.tools.meta import LoopDone, build_meta_tools, QuestionChannel
from agent.notes_store import NotesStore


@pytest.mark.asyncio
async def test_done_raises_with_payload():
    tools = build_meta_tools(notes=None, current_url=lambda: "x", question_channel=QuestionChannel())
    with pytest.raises(LoopDone) as exc:
        await tools["done"](status="success", answer="yay")
    assert exc.value.status == "success" and exc.value.answer == "yay"


@pytest.mark.asyncio
async def test_note_appends(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    tools = build_meta_tools(notes=notes, current_url=lambda: "https://a.test/", question_channel=QuestionChannel())
    out = await tools["note"](text="learned X")
    assert out == "noted"
    assert "learned X" in notes.get("https://a.test/")


@pytest.mark.asyncio
async def test_ask_user_question_blocks_until_answered():
    ch = QuestionChannel()
    tools = build_meta_tools(notes=None, current_url=lambda: "x", question_channel=ch)
    task = asyncio.create_task(tools["ask_user_question"](question="size?"))
    await asyncio.sleep(0)  # let task start
    assert ch.pending() == "size?"
    ch.answer("M")
    result = await task
    assert result == "user said: M"
```

**Step 2: FAIL**

**Step 3: Implement**

```python
# src/agent/tools/meta.py
from __future__ import annotations
import asyncio
from collections.abc import Callable
from agent.notes_store import NotesStore


class LoopDone(Exception):
    def __init__(self, status: str, answer: str):
        super().__init__(f"done({status})")
        self.status = status
        self.answer = answer


class QuestionChannel:
    def __init__(self) -> None:
        self._pending: str | None = None
        self._fut: asyncio.Future | None = None

    def pending(self) -> str | None:
        return self._pending

    async def ask(self, question: str) -> str:
        loop = asyncio.get_event_loop()
        self._pending = question
        self._fut = loop.create_future()
        try:
            return await self._fut
        finally:
            self._pending = None
            self._fut = None

    def answer(self, text: str) -> None:
        if self._fut and not self._fut.done():
            self._fut.set_result(text)


def build_meta_tools(*, notes: NotesStore | None, current_url: Callable[[], str], question_channel: QuestionChannel) -> dict:
    async def note(text: str) -> str:
        if notes is not None:
            notes.append(current_url(), text)
        return "noted"

    async def ask_user_question(question: str) -> str:
        ans = await question_channel.ask(question)
        return f"user said: {ans}"

    async def done(status: str, answer: str) -> str:
        raise LoopDone(status, answer)

    return {"note": note, "ask_user_question": ask_user_question, "done": done}
```

Add corresponding `Tool(...)` records in a new `build_meta_tool_list(...)` that mirrors the browser pattern with JSON schemas including `thought`.

**Step 4: PASS — commit**

```bash
git add src/agent/tools/meta.py tests/unit/test_meta_tools.py
git commit -m "feat(task2): add note/ask_user_question/done meta tools"
```

---

### Task 11: Implicit summarizer

**Files:**
- Create: `task2/src/agent/summarizer.py`
- Test: `task2/tests/unit/test_summarizer.py`

`Summarizer(llm, notes_store)` exposes `async maybe_summarize(trigger, *, prior_url, tape_slice, existing_notes) -> None`. Trigger ∈ {`"goto"`, `"error"`, `"failed"`}. Builds a short prompt asking for 1–3 bullet lines. Appends each line via `notes_store.append(prior_url, line)`.

**Step 1: Failing test (LLM mocked at HTTP level via `httpx.MockTransport`)**

```python
# tests/unit/test_summarizer.py
import json, pytest, httpx
from agent.llm import LLMClient
from agent.notes_store import NotesStore
from agent.summarizer import Summarizer


def _mock(content: str):
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_appends_bullets(tmp_path):
    llm = LLMClient(base_url="http://t/v1", model="m", transport=_mock("- found search at id 12\n- login button hidden behind cookie banner"))
    notes = NotesStore(tmp_path / "n.db")
    s = Summarizer(llm, notes)
    await s.maybe_summarize("goto", prior_url="https://a.test/", tape_slice=[{"action": "click", "obs": "ERROR"}], existing_notes="")
    body = notes.get("https://a.test/")
    assert "found search at id 12" in body
    assert "login button" in body
```

**Step 2: FAIL**

**Step 3: Implement**

```python
# src/agent/summarizer.py
from __future__ import annotations
from agent.llm import LLMClient
from agent.notes_store import NotesStore


_PROMPT = """You compress a browsing agent's recent activity at a single URL into 1–3 short bullet lines (each ≤120 chars). Focus on what failed, what was found, what to remember next visit. Output bullets only, one per line, prefixed by "- ". No prose, no headers."""


class Summarizer:
    def __init__(self, llm: LLMClient, notes: NotesStore) -> None:
        self._llm = llm
        self._notes = notes

    async def maybe_summarize(self, trigger: str, *, prior_url: str, tape_slice: list[dict], existing_notes: str) -> None:
        if not prior_url:
            return
        user = (
            f"Trigger: {trigger}\nURL: {prior_url}\n"
            f"Existing notes:\n{existing_notes or '(none)'}\n\n"
            f"Recent activity:\n{tape_slice}"
        )
        msg = await self._llm.chat(
            [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user}],
            reasoning=False,
        )
        for line in (msg.get("content") or "").splitlines():
            line = line.strip()
            if line.startswith("- ") and len(line) <= 200:
                self._notes.append(prior_url, line[2:].strip())
```

**Step 4: PASS — commit**

```bash
git add src/agent/summarizer.py tests/unit/test_summarizer.py
git commit -m "feat(task2): add implicit URL-note summarizer"
```

---

### Task 12: Context builder (prompt assembly)

**Files:**
- Create: `task2/src/agent/context.py`
- Test: `task2/tests/unit/test_context.py`

`build_messages(system, goal, qa, url_notes, tape, page_header, replan_hint=None) -> list[dict]` returns OpenAI-style messages. Tape compression: keep last K=8 in full as alternating assistant tool_call + tool result; older steps collapsed into a single system bullet list.

**Step 1: Failing test**

```python
# tests/unit/test_context.py
from agent.context import build_messages


def test_ordering_and_compression():
    tape = [{"thought": f"t{i}", "action": "read", "args": {}, "obs": f"o{i}"} for i in range(12)]
    msgs = build_messages(
        system="SYS",
        goal="buy soap",
        qa=[("size?", "M")],
        url_notes="- prev: tried X",
        tape=tape,
        page_header="URL=x title=T elems=10",
        replan_hint=None,
    )
    # System first, with goal+qa+url_notes folded into the system content
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert "SYS" in sys
    assert "Goal: buy soap" in sys
    assert "Q: size?" in sys and "A: M" in sys
    assert "tried X" in sys
    assert "URL=x" in sys
    # Older steps collapsed into one system note
    assert "step 0: read" in sys
    assert "step 3: read" in sys
    # Last K=8 expanded as alternating assistant+tool messages
    expanded = msgs[1:]
    assert len(expanded) == 8 * 2
    assert expanded[0]["role"] == "assistant"
    assert expanded[1]["role"] == "tool"
    # Content includes thoughts for kept steps
    assert any("t11" in m.get("content", "") for m in expanded)


def test_replan_hint_appended_to_system():
    msgs = build_messages(system="SYS", goal="g", qa=[], url_notes="", tape=[], page_header="h",
                         replan_hint="REPLAN: try a different element")
    assert "REPLAN" in msgs[0]["content"]
```

**Step 2: FAIL**

**Step 3: Implement**

```python
# src/agent/context.py
from __future__ import annotations
import json
from typing import Any

K_RECENT = 8


def _short(action: str, args: dict, obs: str) -> str:
    obs1 = (obs or "").splitlines()[0][:120]
    return f"{action}({json.dumps(args, ensure_ascii=False)[:80]}) -> {obs1}"


def build_messages(*, system: str, goal: str, qa: list[tuple[str, str]], url_notes: str,
                   tape: list[dict[str, Any]], page_header: str, replan_hint: str | None) -> list[dict]:
    parts = [system, "", f"Goal: {goal}"]
    if qa:
        parts.append("")
        for q, a in qa:
            parts.append(f"Q: {q}\nA: {a}")
    parts.append("")
    parts.append("URL notes:")
    parts.append(url_notes or "(none)")
    parts.append("")
    parts.append(page_header)

    older = tape[:-K_RECENT] if len(tape) > K_RECENT else []
    recent = tape[-K_RECENT:] if len(tape) > K_RECENT else tape
    if older:
        parts.append("")
        parts.append("Earlier steps:")
        for i, step in enumerate(older):
            parts.append(f"- step {i}: {_short(step['action'], step.get('args', {}), step.get('obs', ''))}")

    if replan_hint:
        parts.append("")
        parts.append(replan_hint)

    msgs: list[dict] = [{"role": "system", "content": "\n".join(parts)}]
    base_idx = len(tape) - len(recent)
    for off, step in enumerate(recent):
        idx = base_idx + off
        tool_call_id = f"call_{idx}"
        msgs.append({
            "role": "assistant",
            "content": step.get("thought", ""),
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": step["action"], "arguments": json.dumps(step.get("args", {}))},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": tool_call_id, "content": step.get("obs", "")})
    return msgs
```

**Step 4: PASS — commit**

```bash
git add src/agent/context.py tests/unit/test_context.py
git commit -m "feat(task2): add ReAct context builder with K=8 tape compression"
```

---

### Task 13: ReAct loop — happy path

**Files:**
- Create: `task2/src/agent/loop.py`
- Test: `task2/tests/unit/test_loop_happy.py`

`ReactLoop(llm, registry, notes, summarizer, trace, browser, question_channel, max_steps).run(goal) -> dict` returns `{"status", "answer"}`. Pulls current URL via `browser.page.url`. After each step writes the event to `trace`. On `LoopDone`, exits cleanly.

**Step 1: Failing test (LLM mocked to call `done` immediately)**

```python
# tests/unit/test_loop_happy.py
import json, pytest, httpx
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry, Tool
from agent.tools.meta import LoopDone, QuestionChannel, build_meta_tools
from agent.trace import TraceWriter, read_trace
from agent.loop import ReactLoop


class _StubBrowser:
    class page:
        url = "https://a.test/"
    page = page()


def _mock_calls(calls):
    """Cycle through canned tool_calls."""
    it = iter(calls)
    async def handler(request):
        body = json.loads(request.content)
        nxt = next(it)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant",
            "content": "thinking",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": nxt[0], "arguments": json.dumps(nxt[1])}}],
        }}]})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_loop_runs_done(tmp_path):
    transport = _mock_calls([("done", {"status": "success", "answer": "42"})])
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://a.test/", question_channel=qc)
    reg.register(Tool("done", "done",
                      {"type": "object", "properties": {"status": {"type": "string"}, "answer": {"type": "string"}}, "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(llm=llm, registry=reg, notes=None, summarizer=None,
                     trace=trace, browser=_StubBrowser(), question_channel=qc, max_steps=5)
    out = await loop.run("get the answer")
    assert out == {"status": "success", "answer": "42"}
    events = read_trace(tmp_path / "t.jsonl")
    assert events[-1]["type"] == "done"
```

**Step 2: FAIL**

**Step 3: Implement (no stuck-detector yet)**

```python
# src/agent/loop.py
from __future__ import annotations
import json
from typing import Any
from agent.context import build_messages
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry
from agent.tools.meta import LoopDone, QuestionChannel
from agent.trace import TraceWriter

_SYSTEM = """You are a web-browsing ReAct agent. Each turn, pick exactly one tool to call. Always include a brief `thought` argument explaining your choice. Element IDs come from list_interactive — never invent CSS selectors. Call done(status, answer) when the user goal is satisfied or impossible."""


class ReactLoop:
    def __init__(self, *, llm: LLMClient, registry: ToolRegistry, notes, summarizer,
                 trace: TraceWriter, browser, question_channel: QuestionChannel, max_steps: int = 50):
        self.llm = llm
        self.registry = registry
        self.notes = notes
        self.summarizer = summarizer
        self.trace = trace
        self.browser = browser
        self.qc = question_channel
        self.max_steps = max_steps
        self.tape: list[dict[str, Any]] = []
        self.qa: list[tuple[str, str]] = []

    def _current_url(self) -> str:
        try:
            return self.browser.page.url
        except Exception:
            return ""

    def _page_header(self) -> str:
        return f"URL={self._current_url()}"

    async def run(self, goal: str) -> dict:
        tools = self.registry.to_openai_tools()
        for step_idx in range(self.max_steps):
            url = self._current_url()
            url_notes = self.notes.get(url) if self.notes else ""
            messages = build_messages(
                system=_SYSTEM, goal=goal, qa=list(self.qa),
                url_notes=url_notes, tape=self.tape,
                page_header=self._page_header(), replan_hint=None,
            )
            msg = await self.llm.chat(messages, tools=tools, tool_choice="auto", reasoning=False)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self.trace.write({"type": "error", "payload": {"reason": "no tool call"}})
                return {"status": "failed", "answer": "agent produced no tool call"}
            tc = tool_calls[0]
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            thought = args.pop("thought", "") if isinstance(args, dict) else ""
            try:
                obs = await self.registry.call(name, args)
            except LoopDone as d:
                self.trace.write({"type": "step", "payload": {"n": step_idx, "thought": thought, "action": name, "args": args, "obs": f"done({d.status})"}})
                self.trace.write({"type": "done", "payload": {"status": d.status, "answer": d.answer}})
                return {"status": d.status, "answer": d.answer}
            obs_str = obs if isinstance(obs, str) else json.dumps(obs)
            self.tape.append({"thought": thought, "action": name, "args": args, "obs": obs_str})
            self.trace.write({"type": "step", "payload": {"n": step_idx, "thought": thought, "action": name, "args": args, "obs": obs_str}})
        self.trace.write({"type": "done", "payload": {"status": "failed", "answer": "max steps"}})
        return {"status": "failed", "answer": "max steps"}
```

**Step 4: PASS — commit**

```bash
git add src/agent/loop.py tests/unit/test_loop_happy.py
git commit -m "feat(task2): add ReAct loop happy path"
```

---

### Task 14: ReAct loop — stuck-detector → replan → escalate

**Files:**
- Modify: `task2/src/agent/loop.py`
- Test: `task2/tests/unit/test_loop_stuck.py`

Behaviour: when the last 3 steps share the same `(url, action, args, obs)`, the next call passes a `replan_hint` *and* sets `tool_choice="auto"`. If after the replan the very next step is *still* the same `(url, action, args, obs)`, force `ask_user_question`. If the user's reply produces yet another identical step, force `done(failed)`.

**Step 1: Failing test**

```python
# tests/unit/test_loop_stuck.py
import json, pytest, httpx
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry, Tool
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.trace import TraceWriter
from agent.loop import ReactLoop


class _Browser:
    class page:
        url = "https://a.test/"
    page = page()


@pytest.mark.asyncio
async def test_replan_hint_after_3_repeats(tmp_path):
    seen_hints = []

    async def handler(request):
        body = json.loads(request.content)
        sys_msg = body["messages"][0]["content"]
        seen_hints.append("REPLAN" in sys_msg)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "noop", "arguments": "{}"}}],
        }}]})

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noop():
        return "same"

    reg.register(Tool("noop", "x", {"type": "object", "properties": {}}, noop))
    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(llm=llm, registry=reg, notes=None, summarizer=None,
                    trace=trace, browser=_Browser(), question_channel=qc, max_steps=5)
    await loop.run("g")
    # First 3 calls have no hint; 4th call (after 3rd repeat) should carry REPLAN
    assert seen_hints[:3] == [False, False, False]
    assert seen_hints[3] is True
```

**Step 2: FAIL**

**Step 3: Modify `loop.run` to track repeats and inject hints**

Sketch:

```python
def _last_three_match(self) -> bool:
    if len(self.tape) < 3: return False
    a, b, c = self.tape[-3], self.tape[-2], self.tape[-1]
    key = lambda s: (s.get("action"), json.dumps(s.get("args"), sort_keys=True), s.get("obs"))
    return key(a) == key(b) == key(c)

# in run():
replan_hint = None
forced_choice = "auto"
if self._last_three_match():
    last = self.tape[-1]
    replan_hint = ("REPLAN: You repeated the same action 3 times with the same observation. "
                   "Re-read URL notes and pick a DIFFERENT action this turn — different element, "
                   "navigate elsewhere, call note() to record the failure, or ask_user_question.")
    # Re-pin URL notes already happens via context builder; hint goes into system.
# After getting the model response and computing (name, args):
if self._last_three_match() and (name, json.dumps(args, sort_keys=True)) == (last["action"], json.dumps(last["args"], sort_keys=True)):
    # Stuck again after replan: force ask_user_question
    name, args = "ask_user_question", {"question": f"I'm stuck on {self._current_url()}: same action keeps yielding the same result. What should I try?"}
```

If after the user answers the next step is still the same identical (url, action, args, obs), call `done` with status `failed`. Track this with a small state machine: `STUCK_NONE → STUCK_HINTED → STUCK_ASKED → STUCK_GIVEUP`.

**Step 4: Run test — PASS**

**Step 5: Commit**

```bash
git add src/agent/loop.py tests/unit/test_loop_stuck.py
git commit -m "feat(task2): stuck-detector forces replan, then ask, then fail"
```

---

### Task 15: Loop wires summarizer triggers + url note retrieval

**Files:**
- Modify: `task2/src/agent/loop.py`
- Test: `task2/tests/unit/test_loop_summarizer_trigger.py`

After every step, if action is `goto` or observation starts with `ERROR:` or final `done.status == "failed"`, call `summarizer.maybe_summarize(...)` with the appropriate trigger and `prior_url`. For `goto`, prior URL is the URL before navigation.

**Step 1: Failing test**

```python
# tests/unit/test_loop_summarizer_trigger.py
import json, pytest, httpx
from unittest.mock import AsyncMock
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry, Tool
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.notes_store import NotesStore
from agent.trace import TraceWriter
from agent.loop import ReactLoop


class _Browser:
    def __init__(self):
        self._url = "https://a.test/"
        self.page = type("P", (), {})()
        self.page.url = self._url


@pytest.mark.asyncio
async def test_summarizer_called_on_error(tmp_path):
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "fail_tool", "arguments": "{}"}}],
        }}]})

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()
    async def fail_tool(): return "ERROR: kaboom"
    reg.register(Tool("fail_tool", "x", {"type": "object", "properties": {}}, fail_tool))
    qc = QuestionChannel()
    notes = NotesStore(tmp_path / "n.db")
    summarizer = AsyncMock()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(llm=llm, registry=reg, notes=notes, summarizer=summarizer,
                    trace=trace, browser=_Browser(), question_channel=qc, max_steps=2)
    await loop.run("g")
    assert summarizer.maybe_summarize.await_count >= 1
    args = summarizer.maybe_summarize.await_args.kwargs
    assert args["trigger"] == "error"
```

**Step 2: FAIL → Step 3: implement → Step 4: PASS**

**Step 5: Commit**

```bash
git add src/agent/loop.py tests/unit/test_loop_summarizer_trigger.py
git commit -m "feat(task2): wire summarizer triggers into loop"
```

---

### Task 16: FastAPI server + WebSocket protocol

**Files:**
- Create: `task2/src/agent/server.py`
- Create: `task2/src/agent/templates/index.html`
- Create: `task2/src/agent/templates/replay.html`
- Create: `task2/src/agent/static/app.js`
- Test: `task2/tests/integration/test_server_ws.py`

WebSocket protocol:
- Client → server first message: `{"type": "goal", "goal": "..."}`.
- Server → client during run: `{"type": "step", "n", "thought", "action", "args", "obs"}`, `{"type": "question", "question": "..."}`, `{"type": "done", "status", "answer"}`.
- Client → server during a question: `{"type": "answer", "text": "..."}`.

Single-concurrent semaphore wraps the loop run.

**Step 1: Add deps**

```bash
uv add fastapi uvicorn jinja2
uv add --dev pytest-asyncio httpx
```

**Step 2: Failing test**

```python
# tests/integration/test_server_ws.py
import json, pytest, httpx
from agent.server import build_app
from agent.config import Config


def _llm_mock_done(answer="ok"):
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "done", "arguments": json.dumps({"status": "success", "answer": answer})}}],
        }}]})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ws_streams_done(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "5")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_llm_mock_done("42"))
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Use websockets via test client — fall back to httpx-ws if available.
        # For simplicity, exercise an HTTP endpoint that triggers run() and returns final result.
        r = await ac.post("/api/run_sync", json={"goal": "g"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success" and body["answer"] == "42"
```

> Note: WebSocket testing is awkward without `starlette.testclient` (sync). Add a thin `/api/run_sync` endpoint that wraps the same loop machinery for CI. The WS handler is a thin shell on top of the same coroutine; smoke-test the WS path manually after Task 18.

**Step 3: Implement** `build_app(cfg, data_dir, llm_transport=None)` factory that wires everything:

```python
# src/agent/server.py (skeleton)
from __future__ import annotations
import asyncio, uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from agent.config import Config
from agent.llm import LLMClient
from agent.notes_store import NotesStore
from agent.summarizer import Summarizer
from agent.trace import TraceWriter, read_trace
from agent.tools.registry import ToolRegistry
from agent.tools.browser import build_browser_tool_list
from agent.tools.meta import QuestionChannel, build_meta_tools, LoopDone
from agent.browser_session import BrowserSession
from agent.loop import ReactLoop


def build_app(*, cfg: Config, data_dir: Path, llm_transport=None) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    sem = asyncio.Semaphore(1)
    notes = NotesStore(data_dir / "url_notes.db", query_strip=cfg.url_note_query_strip)

    async def make_llm(model_url, model_name):
        return LLMClient(model_url, model_name, transport=llm_transport)

    async def run_loop(goal: str, send_event, ask_user) -> dict:
        async with sem:
            session_id = uuid.uuid4().hex
            trace = TraceWriter(data_dir / "traces" / f"{session_id}.jsonl")
            browser = BrowserSession()
            await browser.start()
            try:
                qc = QuestionChannel()
                agent_llm = LLMClient(cfg.agent_model_base_url, cfg.agent_model_name, transport=llm_transport)
                summ_llm = LLMClient(cfg.summarizer_model_base_url, cfg.summarizer_model_name, transport=llm_transport)
                summarizer = Summarizer(summ_llm, notes)
                reg = ToolRegistry()
                for t in build_browser_tool_list(browser):
                    reg.register(t)
                meta = build_meta_tools(notes=notes, current_url=lambda: browser.page.url, question_channel=qc)
                from agent.tools.browser import _meta_schemas  # see Task 10 — expose JSON schemas via build_meta_tool_list
                # Register meta tools; reuse the schemas defined in Task 10
                # ... (detail elided; copy from build_meta_tool_list)

                # Bridge: trace.write also forwards to send_event
                orig = trace.write
                def write_and_send(ev):
                    orig(ev)
                    asyncio.create_task(send_event(ev))
                trace.write = write_and_send  # type: ignore

                # Bridge questions: when QuestionChannel has pending, push to UI; resume on answer
                async def question_pump():
                    while True:
                        await asyncio.sleep(0.05)
                        q = qc.pending()
                        if q:
                            await send_event({"type": "question", "payload": {"question": q}})
                            ans = await ask_user()
                            qc.answer(ans)

                pump_task = asyncio.create_task(question_pump())
                try:
                    loop = ReactLoop(llm=agent_llm, registry=reg, notes=notes, summarizer=summarizer,
                                    trace=trace, browser=browser, question_channel=qc, max_steps=cfg.max_steps)
                    return await loop.run(goal)
                finally:
                    pump_task.cancel()
            finally:
                await browser.close()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/replay", response_class=HTMLResponse)
    async def replay_index(request: Request):
        traces_dir = data_dir / "traces"
        sessions = sorted([p.stem for p in traces_dir.glob("*.jsonl")], reverse=True) if traces_dir.exists() else []
        return templates.TemplateResponse("replay.html", {"request": request, "sessions": sessions, "selected": None, "events": []})

    @app.get("/replay/{sid}", response_class=HTMLResponse)
    async def replay_one(request: Request, sid: str):
        traces_dir = data_dir / "traces"
        sessions = sorted([p.stem for p in traces_dir.glob("*.jsonl")], reverse=True)
        events = read_trace(traces_dir / f"{sid}.jsonl")
        return templates.TemplateResponse("replay.html", {"request": request, "sessions": sessions, "selected": sid, "events": events})

    @app.post("/api/run_sync")
    async def run_sync(payload: dict):
        async def send_event(ev): pass
        async def ask_user(): return ""
        return await run_loop(payload["goal"], send_event, ask_user)

    @app.websocket("/ws")
    async def ws(ws: WebSocket):
        await ws.accept()
        try:
            first = await ws.receive_json()
            assert first["type"] == "goal"
            answer_q: asyncio.Queue[str] = asyncio.Queue()

            async def send_event(ev):
                await ws.send_json(ev)

            async def ask_user():
                return await answer_q.get()

            run_task = asyncio.create_task(run_loop(first["goal"], send_event, ask_user))
            while not run_task.done():
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.1)
                    if msg.get("type") == "answer":
                        await answer_q.put(msg.get("text", ""))
                except asyncio.TimeoutError:
                    pass
                except WebSocketDisconnect:
                    run_task.cancel()
                    break
            result = await run_task
            await ws.send_json({"type": "result", "payload": result})
        except WebSocketDisconnect:
            pass

    return app
```

**Step 4: Index template (`templates/index.html`)** — minimal HTMX/vanilla:

```html
<!doctype html>
<html><head><meta charset="utf-8"><title>Web Agent</title>
<script src="/static/app.js" defer></script>
<style>
body{font-family:system-ui;margin:2rem;max-width:900px}
.card{border:1px solid #ccc;border-radius:6px;padding:.6rem;margin:.4rem 0}
.thought{color:#666;font-style:italic}
.action{font-family:monospace}
.obs{white-space:pre-wrap;font-family:monospace;background:#f7f7f7;padding:.4rem}
#qa{display:none;margin:1rem 0;padding:1rem;border:2px solid #f80;border-radius:6px}
</style></head>
<body>
<h1>Web Agent</h1>
<form id=goal-form>
  <textarea id=goal rows=3 cols=80 placeholder="Goal..."></textarea><br>
  <button type=submit>Run</button>
  <a href="/replay" style="margin-left:1rem">Replay past sessions →</a>
</form>
<div id=qa>
  <div id=qa-question></div>
  <input id=qa-answer><button id=qa-send>Send</button>
</div>
<div id=trace></div>
<div id=result></div>
</body></html>
```

**Step 5: `static/app.js`** — opens WS, renders events:

```js
const $ = id => document.getElementById(id);
$("goal-form").addEventListener("submit", e => {
  e.preventDefault();
  $("trace").innerHTML = ""; $("result").innerHTML = ""; $("qa").style.display = "none";
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.addEventListener("open", () => ws.send(JSON.stringify({type:"goal", goal: $("goal").value})));
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "step") {
      const p = m.payload;
      const el = document.createElement("div"); el.className = "card";
      el.innerHTML = `<div class=thought>${escapeHtml(p.thought||"")}</div>
                      <div class=action>${escapeHtml(p.action)}(${escapeHtml(JSON.stringify(p.args))})</div>
                      <div class=obs>${escapeHtml(p.obs||"")}</div>`;
      $("trace").appendChild(el);
    } else if (m.type === "question") {
      $("qa-question").innerText = m.payload.question;
      $("qa").style.display = "block";
      $("qa-send").onclick = () => { ws.send(JSON.stringify({type:"answer", text: $("qa-answer").value})); $("qa").style.display="none"; $("qa-answer").value=""; };
    } else if (m.type === "done") {
      $("result").innerHTML = `<h2>${escapeHtml(m.payload.status)}</h2><p>${escapeHtml(m.payload.answer)}</p>`;
    }
  });
});
function escapeHtml(s){return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
```

**Step 6: `templates/replay.html`**:

```html
<!doctype html><html><head><meta charset="utf-8"><title>Replay</title>
<style>body{display:flex;font-family:system-ui;margin:0}aside{width:240px;border-right:1px solid #ccc;padding:1rem;height:100vh;overflow:auto}main{flex:1;padding:1rem;overflow:auto}.card{border:1px solid #ccc;border-radius:6px;padding:.6rem;margin:.4rem 0}.obs{white-space:pre-wrap;font-family:monospace;background:#f7f7f7;padding:.4rem}</style>
</head><body>
<aside><h3>Sessions</h3><ul>{% for s in sessions %}<li><a href="/replay/{{s}}">{{s}}</a></li>{% endfor %}</ul></aside>
<main>{% if selected %}<h2>{{selected}}</h2>{% for e in events %}{% if e.type=="step" %}
<div class=card><div><i>{{e.payload.thought}}</i></div><div><code>{{e.payload.action}}({{e.payload.args|tojson}})</code></div><div class=obs>{{e.payload.obs}}</div></div>
{% elif e.type=="question" %}<div class=card style="border-color:#f80"><b>?</b> {{e.payload.question}}</div>
{% elif e.type=="done" %}<div class=card style="border-color:#080"><b>{{e.payload.status}}</b>: {{e.payload.answer}}</div>{% endif %}{% endfor %}{% else %}<p>Select a session.</p>{% endif %}</main>
</body></html>
```

**Step 7: Run test — PASS**

```bash
uv run pytest tests/integration/test_server_ws.py -v
```

**Step 8: Manual smoke**

```bash
uv run uvicorn agent.server:build_app --factory --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000/`, enter a goal, confirm trace cards stream and replay sidebar populates.

**Step 9: Commit**

```bash
git add src/agent/server.py src/agent/templates/ src/agent/static/ tests/integration/test_server_ws.py pyproject.toml uv.lock
git commit -m "feat(task2): FastAPI server with WS streaming and replay UI"
```

---

### Task 17: Eval harness scaffold

**Files:**
- Create: `task2/tests/evals/conftest.py` — fixture serving `tests/fixtures/sites/` over an `aiohttp` or `http.server` thread.
- Create: `task2/tests/fixtures/sites/simple/index.html` — a tiny site with a search box.
- Create: `task2/tests/evals/test_simple_search.py` — eval case.

**Step 1: Add the fixture site**

```html
<!-- tests/fixtures/sites/simple/index.html -->
<!doctype html><html><body>
<h1>Find a Word</h1>
<input id=q aria-label="search">
<button onclick="document.getElementById('out').innerText='you searched: '+document.getElementById('q').value">Search</button>
<p id=out></p>
</body></html>
```

**Step 2: Write the eval test (it will be slow — mark it)**

```python
# tests/evals/test_simple_search.py
import json, pytest, httpx, base64
from agent.config import Config
from agent.server import build_app
from httpx import ASGITransport, AsyncClient

CANNED = [
    ("goto", {"url": "PLACEHOLDER", "thought": "open site"}),
    ("list_interactive", {"thought": "see elements"}),
    ("type", {"id": 0, "text": "hello", "submit": False, "thought": "type"}),
    ("click", {"id": 1, "thought": "search"}),
    ("read", {"thought": "verify"}),
    ("done", {"status": "success", "answer": "you searched: hello", "thought": "done"}),
]


def _scripted_llm(url):
    canned = [(n, {**a, "url": url} if n == "goto" else a) for n, a in CANNED]
    it = iter(canned)
    async def handler(request):
        n, a = next(it)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}],
        }}]})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_simple_search_eval(tmp_path, monkeypatch, http_fixture_server):
    url = http_fixture_server("simple/index.html")
    monkeypatch.setenv("MAX_STEPS", "10")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_scripted_llm(url))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "search for hello"})
        body = r.json()
        assert body["status"] == "success"
        assert "hello" in body["answer"]
```

**Step 3: `tests/evals/conftest.py`** — fixture HTTP server:

```python
import threading, http.server, socketserver, contextlib, pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent / "fixtures" / "sites"


@pytest.fixture
def http_fixture_server():
    with contextlib.ExitStack() as stack:
        def start(rel_path):
            handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(ROOT), **kw)
            httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            stack.callback(httpd.shutdown)
            return f"http://127.0.0.1:{port}/{rel_path}"
        yield start
```

**Step 4: Run eval — PASS**

```bash
uv run pytest tests/evals/ -v
```

**Step 5: Commit**

```bash
git add tests/evals tests/fixtures
git commit -m "test(task2): add eval harness with fixture site + canned-LLM run"
```

---

### Task 18: Dockerfile + Zeabur deployment

**Files:**
- Create: `task2/Dockerfile`
- Create: `task2/.dockerignore`
- Modify: `task2/README.md`

**Step 1: Dockerfile**

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "agent.server:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 2: `.dockerignore`**

```
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
tests/
data/
```

**Step 3: README — how to run + Zeabur env vars**

Sections: Overview, Local dev (`uv sync`, `uv run pytest`, `uvicorn agent.server:build_app --factory`), Docker, Zeabur env-var table, AI usage notes.

**Step 4: Local Docker smoke test**

```bash
cd task2 && docker build -t task2-agent . && docker run --rm -p 8000:8000 \
  -e AGENT_MODEL_BASE_URL=http://host.docker.internal:8090/v1 task2-agent
```

Visit `http://localhost:8000/` and confirm boot.

**Step 5: Push to Zeabur**

Out-of-band: connect the repo, point the service at `task2/`, set env vars, attach a 1 GB volume at `/app/data`, deploy. Capture URL for README.

**Step 6: Commit**

```bash
git add task2/Dockerfile task2/.dockerignore task2/README.md
git commit -m "chore(task2): add Dockerfile and deployment docs"
```

---

### Task 19: Final lint pass + green-bar verification

**Step 1: Lint, format, full test run**

```bash
cd task2
uv run ruff check . && uv run ruff format --check .
uv run pytest -v
```

**Step 2: Confirm Zeabur URL works end-to-end** with a small goal (e.g. "Find the year HTML5 was finalised on the Wikipedia HTML5 page"). Save the trace; link to it from README.

**Step 3: Final commit if anything changed**

```bash
git add -A
git commit -m "chore(task2): final cleanup + green bar"
```

---

## Done criteria

- All tests green (`uv run pytest`).
- Ruff clean.
- App boots locally via `uvicorn agent.server:build_app --factory`.
- Docker image builds and runs.
- Zeabur URL reachable; one end-to-end run captured in `data/traces/` and linked from README.
- `prompts/task2.md` (already exists from brainstorming) and `docs/plans/2026-05-02-task2-web-agent-design.md` referenced from `task2/README.md`.
