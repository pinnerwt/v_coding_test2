# Anti-Loop Diff & Tool-Hiding — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent the Task 2 browser agent from burning steps on `read`/`press_key` calls whose outcome is identical to a prior call, by adding server-side mechanical guarantees (per-offset auto-advance for `read` / `list_interactive`, global-diff hiding for `press_key`, small-diff context injection, `read_grep` dedup).

**Architecture:** A new module `agent/page_diff.py` owns three concerns: (1) the offset cache for paginated tools, (2) the previous-`innerText` cache for global diffs, (3) the diff formatter for context injection. `ReactLoop` consults the cache before every `read` / `list_interactive` / `read_grep` / `press_key` call, and after every state-changing action it captures the new `innerText` and computes the global diff. The tool-list passed to the LLM each turn is filtered down from `registry.to_openai_tools()` based on the cache state. `context.build_messages` gains an optional `page_diff` parameter that is appended to the page-header block when present.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `pytest-asyncio`, Playwright (already used by `BrowserSession`), `difflib` (stdlib), `ruff` for lint/format.

---

## Reference: design doc

The full design lives at `docs/plans/2026-05-02-anti-loop-diff-design.md`. This plan is its TDD execution. Read the design first if anything below is ambiguous.

## Reference: relevant existing files

- `task2/src/agent/loop.py` — `ReactLoop.run()` is where the per-turn glue lives; the tool list is computed at line 137 (`tools = self.registry.to_openai_tools()`) **once** before the loop. We will move it inside the loop and route it through a filter.
- `task2/src/agent/tools/registry.py` — `Tool` dataclass + `ToolRegistry`. `to_openai_tools()` returns the full list; we will add a filtered variant.
- `task2/src/agent/tools/browser.py` — `_READ_LIMIT = 2000`, `read()`, `list_interactive()`, `read_grep()`, `press_key()`. We will leave the underlying tool functions alone; the cache logic lives outside them, in the loop.
- `task2/src/agent/context.py` — `build_messages(...)` builds the OpenAI-format conversation. We will add an optional `page_diff: str | None` parameter, appended to the page-header block.
- `task2/src/agent/config.py` — `Config` dataclass with `from_env()`. We will add three knobs: `small_diff_threshold`, `max_auto_advance_hops`, `diff_inject_max_lines`.
- `task2/src/agent/browser_session.py` — owns `session.page` (Playwright `Page`). The cache asks it for `document.body.innerText` via `page.evaluate(...)`.
- Existing tests: unit tests under `task2/tests/unit/`, integration under `task2/tests/integration/`, evals under `task2/tests/evals/`. New unit tests for the cache/diff logic are pure-Python (no Playwright). Behavior in the loop is exercised with the existing `_StubBrowser` + `_mock_calls` pattern from `tests/unit/test_loop_happy.py`.

## Working agreements

- **TDD is non-negotiable** (see `CLAUDE.md`). Every task's first step is a failing test.
- **`uv` only.** Run `uv sync` once if you haven't. Run tests with `uv run pytest ...`. Lint with `uv run ruff check .` and `uv run ruff format .`.
- **`task2/` is the working directory** for this work (the Python package lives there). Run all `uv` commands from `task2/`.
- **Conventional commits**, scoped: `feat(task2): ...`, `test(task2): ...`, `refactor(task2): ...`.
- Commit at the end of every task. Do not bundle multiple tasks into one commit.
- The design's "small-diff threshold = 500", "max_auto_advance_hops = 32", "diff_inject_max_lines = 10" are defaults; surface them as `Config` fields (env-overridable) so eval cases can tune.

---

## Task 1: Scaffold `agent/page_diff.py` with the per-offset cache

**Files:**
- Create: `task2/src/agent/page_diff.py`
- Create: `task2/tests/unit/test_page_diff_offset_cache.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_page_diff_offset_cache.py`:

```python
from agent.page_diff import OffsetCache


def test_offset_cache_records_what_was_served():
    c = OffsetCache()
    c.record(offset=0, served="hello world")
    assert c.was_served(offset=0, candidate="hello world") is True
    assert c.was_served(offset=0, candidate="hello WORLD") is False


def test_offset_cache_independent_per_offset():
    c = OffsetCache()
    c.record(offset=0, served="A")
    c.record(offset=1600, served="B")
    assert c.was_served(offset=0, candidate="A") is True
    assert c.was_served(offset=1600, candidate="B") is True
    assert c.was_served(offset=0, candidate="B") is False


def test_offset_cache_clear_resets_all():
    c = OffsetCache()
    c.record(offset=0, served="A")
    c.record(offset=1600, served="B")
    c.clear()
    assert c.was_served(offset=0, candidate="A") is False
    assert c.was_served(offset=1600, candidate="B") is False


def test_offset_cache_empty_lookup_returns_false():
    c = OffsetCache()
    assert c.was_served(offset=0, candidate="anything") is False
```

**Step 2: Run test to verify it fails**

```
cd task2
uv run pytest tests/unit/test_page_diff_offset_cache.py -v
```

Expected: `ImportError: cannot import name 'OffsetCache' from 'agent.page_diff'` (module does not exist yet).

**Step 3: Write minimal implementation**

Create `task2/src/agent/page_diff.py`:

```python
from __future__ import annotations


class OffsetCache:
    """Per-offset memo of the last string served to the LLM at each offset.

    Used to detect when a `read(offset=N)` call would produce content the
    agent has already seen at that offset, so the loop can auto-advance.
    """

    def __init__(self) -> None:
        self._served: dict[int, str] = {}

    def record(self, *, offset: int, served: str) -> None:
        self._served[offset] = served

    def was_served(self, *, offset: int, candidate: str) -> bool:
        return self._served.get(offset) == candidate

    def clear(self) -> None:
        self._served.clear()
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_page_diff_offset_cache.py -v
```

Expected: 4 passed.

**Step 5: Commit**

```
git add task2/src/agent/page_diff.py task2/tests/unit/test_page_diff_offset_cache.py
git commit -m "feat(task2): add OffsetCache for per-offset read dedup"
```

---

## Task 2: Add `auto_advance` planner pure function

**Files:**
- Modify: `task2/src/agent/page_diff.py`
- Create: `task2/tests/unit/test_page_diff_auto_advance.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_page_diff_auto_advance.py`:

```python
from agent.page_diff import OffsetCache, plan_read

READ_LIMIT = 1600
MAX_HOPS = 32


def test_plan_read_no_cache_returns_requested_offset():
    text = "X" * 5000
    cache = OffsetCache()
    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=READ_LIMIT, max_hops=MAX_HOPS)
    assert plan.served_offset == 0
    assert plan.served_text == text[0:1600]
    assert plan.advanced_from is None
    assert plan.exhausted is False


def test_plan_read_advances_when_window_already_served():
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])  # already saw 'A'*1600

    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=READ_LIMIT, max_hops=MAX_HOPS)
    assert plan.served_offset == 1600
    assert plan.served_text == text[1600:3200]
    assert plan.advanced_from == 0
    assert plan.exhausted is False


def test_plan_read_walks_multiple_hops_until_changed():
    # offsets 0 and 1600 both already served as their current content
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])
    cache.record(offset=1600, served=text[1600:3200])

    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=READ_LIMIT, max_hops=MAX_HOPS)
    assert plan.served_offset == 3200
    assert plan.served_text == text[3200:4800]
    assert plan.advanced_from == 0


def test_plan_read_exhausted_when_past_end():
    text = "A" * 1600
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])

    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=READ_LIMIT, max_hops=MAX_HOPS)
    assert plan.exhausted is True
    assert plan.served_text == ""
    assert plan.advanced_from == 0


def test_plan_read_respects_max_hops():
    text = "A" * 1600 * 100  # 100 windows of identical content
    cache = OffsetCache()
    for off in range(0, len(text), 1600):
        cache.record(offset=off, served=text[off:off + 1600])

    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=1600, max_hops=4)
    assert plan.exhausted is True  # ran out of hops without finding new content


def test_plan_read_truncates_to_text_length():
    text = "ABC"
    cache = OffsetCache()
    plan = plan_read(text=text, requested_offset=0, cache=cache,
                     read_limit=1600, max_hops=MAX_HOPS)
    assert plan.served_text == "ABC"
    assert plan.exhausted is False  # served what's available
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_page_diff_auto_advance.py -v
```

Expected: `ImportError: cannot import name 'plan_read' ...`.

**Step 3: Write minimal implementation**

Append to `task2/src/agent/page_diff.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadPlan:
    served_offset: int
    served_text: str
    advanced_from: int | None  # original offset if advanced, else None
    exhausted: bool             # True iff we walked past the page or hit max hops


def plan_read(
    *,
    text: str,
    requested_offset: int,
    cache: OffsetCache,
    read_limit: int,
    max_hops: int,
) -> ReadPlan:
    """Decide what to serve for a read(offset) call, honoring the cache.

    Walks forward from `requested_offset` in `read_limit` strides while the
    candidate window has already been served at that offset. Stops when
    either (a) the window differs from what was served at that offset, or
    (b) we have walked past `len(text)`, or (c) we hit `max_hops`.
    """
    n = len(text)
    offset = requested_offset
    hops = 0
    while True:
        if offset >= n:
            return ReadPlan(
                served_offset=offset,
                served_text="",
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=True,
            )
        candidate = text[offset:offset + read_limit]
        if not cache.was_served(offset=offset, candidate=candidate):
            return ReadPlan(
                served_offset=offset,
                served_text=candidate,
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=False,
            )
        # cache hit: advance
        hops += 1
        if hops >= max_hops:
            return ReadPlan(
                served_offset=offset,
                served_text="",
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=True,
            )
        offset += read_limit
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_page_diff_auto_advance.py -v
```

Expected: 6 passed.

**Step 5: Commit**

```
git add task2/src/agent/page_diff.py task2/tests/unit/test_page_diff_auto_advance.py
git commit -m "feat(task2): add plan_read auto-advance planner"
```

---

## Task 3: Add the global-`innerText` cache and diff helpers

**Files:**
- Modify: `task2/src/agent/page_diff.py`
- Create: `task2/tests/unit/test_page_diff_global.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_page_diff_global.py`:

```python
from agent.page_diff import GlobalTextCache, format_small_diff


def test_global_cache_initial_state_no_previous():
    g = GlobalTextCache()
    assert g.previous() is None


def test_global_cache_update_returns_diff_size():
    g = GlobalTextCache()
    g.update("hello world")
    assert g.previous() == "hello world"
    g.update("hello brave new world")
    assert g.previous() == "hello brave new world"


def test_format_small_diff_renders_unified_added_removed():
    out = format_small_diff(
        previous="line A\nline B\nline C\n",
        current="line A\nline B2\nline C\n",
        max_lines=10,
    )
    assert "Page changes since last turn" in out
    assert "+ " in out and "line B2" in out
    assert "- " in out and "line B" in out


def test_format_small_diff_caps_at_max_lines():
    prev = "\n".join(f"old {i}" for i in range(50))
    curr = "\n".join(f"new {i}" for i in range(50))
    out = format_small_diff(previous=prev, current=curr, max_lines=4)
    body_lines = [ln for ln in out.splitlines() if ln.startswith(("+ ", "- "))]
    assert len(body_lines) <= 4


def test_format_small_diff_returns_empty_when_no_change():
    out = format_small_diff(previous="same", current="same", max_lines=10)
    assert out == ""
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_page_diff_global.py -v
```

Expected: ImportError for `GlobalTextCache` / `format_small_diff`.

**Step 3: Write minimal implementation**

Append to `task2/src/agent/page_diff.py`:

```python
import difflib


class GlobalTextCache:
    """Stores the most recent `document.body.innerText` for global-diff
    comparisons across turns."""

    def __init__(self) -> None:
        self._prev: str | None = None

    def previous(self) -> str | None:
        return self._prev

    def update(self, current: str) -> None:
        self._prev = current


def format_small_diff(*, previous: str, current: str, max_lines: int) -> str:
    if previous == current:
        return ""
    prev_lines = previous.splitlines(keepends=False)
    curr_lines = current.splitlines(keepends=False)
    body: list[str] = []
    for line in difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            body.append("  + " + line[1:])
        elif line.startswith("-"):
            body.append("  - " + line[1:])
        if len(body) >= max_lines:
            break
    added = sum(1 for ln in body if ln.startswith("  + "))
    removed = sum(1 for ln in body if ln.startswith("  - "))
    header = f"Page changes since last turn (+{added} / -{removed} lines):"
    return "\n".join([header, *body])
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_page_diff_global.py -v
```

Expected: 5 passed.

**Step 5: Commit**

```
git add task2/src/agent/page_diff.py task2/tests/unit/test_page_diff_global.py
git commit -m "feat(task2): add GlobalTextCache and unified small-diff formatter"
```

---

## Task 4: Add diff-size threshold gate

**Files:**
- Modify: `task2/src/agent/page_diff.py`
- Modify: `task2/tests/unit/test_page_diff_global.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_page_diff_global.py`:

```python
from agent.page_diff import diff_char_size, should_inject_diff


def test_diff_char_size_counts_added_plus_removed():
    size = diff_char_size(previous="abc def\nghi\n", current="abc XYZ\nghi\n")
    # one removed line "abc def" (7 chars) + one added "abc XYZ" (7 chars) = 14
    assert size == 14


def test_should_inject_diff_below_threshold():
    assert should_inject_diff(previous="A", current="AB", threshold=10) is True


def test_should_inject_diff_above_threshold_returns_false():
    huge_prev = "x" * 5000
    huge_curr = "y" * 5000
    assert should_inject_diff(previous=huge_prev, current=huge_curr, threshold=500) is False


def test_should_inject_diff_no_change_returns_false():
    assert should_inject_diff(previous="same", current="same", threshold=500) is False
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_page_diff_global.py -v
```

Expected: ImportError for `diff_char_size` / `should_inject_diff`.

**Step 3: Write minimal implementation**

Append to `task2/src/agent/page_diff.py`:

```python
def diff_char_size(*, previous: str, current: str) -> int:
    """Sum of characters added and removed (line-level) between the two
    strings. Used as the threshold metric for context injection."""
    if previous == current:
        return 0
    prev_lines = previous.splitlines(keepends=False)
    curr_lines = current.splitlines(keepends=False)
    total = 0
    for line in difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            total += len(line) - 1  # strip the leading +/-
    return total


def should_inject_diff(*, previous: str, current: str, threshold: int) -> bool:
    if previous == current:
        return False
    return diff_char_size(previous=previous, current=current) <= threshold
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_page_diff_global.py -v
```

Expected: 9 passed (5 prior + 4 new).

**Step 5: Commit**

```
git add task2/src/agent/page_diff.py task2/tests/unit/test_page_diff_global.py
git commit -m "feat(task2): add diff size + injection threshold helpers"
```

---

## Task 5: Add config knobs

**Files:**
- Modify: `task2/src/agent/config.py`
- Modify: `task2/tests/unit/test_config.py`

**Step 1: Write the failing test**

Open `task2/tests/unit/test_config.py` and append:

```python
def test_config_loads_anti_loop_defaults(monkeypatch):
    for k in ("SMALL_DIFF_THRESHOLD", "MAX_AUTO_ADVANCE_HOPS", "DIFF_INJECT_MAX_LINES"):
        monkeypatch.delenv(k, raising=False)
    from agent.config import Config
    c = Config.from_env()
    assert c.small_diff_threshold == 500
    assert c.max_auto_advance_hops == 32
    assert c.diff_inject_max_lines == 10


def test_config_overrides_anti_loop_via_env(monkeypatch):
    monkeypatch.setenv("SMALL_DIFF_THRESHOLD", "200")
    monkeypatch.setenv("MAX_AUTO_ADVANCE_HOPS", "8")
    monkeypatch.setenv("DIFF_INJECT_MAX_LINES", "5")
    from agent.config import Config
    c = Config.from_env()
    assert c.small_diff_threshold == 200
    assert c.max_auto_advance_hops == 8
    assert c.diff_inject_max_lines == 5
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_config.py -v
```

Expected: AttributeError on `c.small_diff_threshold`.

**Step 3: Write minimal implementation**

Edit `task2/src/agent/config.py`:

```python
@dataclass(frozen=True)
class Config:
    agent_model_base_url: str
    agent_model_name: str
    agent_api_key: str | None
    summarizer_model_base_url: str
    summarizer_model_name: str
    summarizer_api_key: str | None
    max_steps: int
    url_note_query_strip: bool
    restrict_goto: bool
    small_diff_threshold: int
    max_auto_advance_hops: int
    diff_inject_max_lines: int

    @classmethod
    def from_env(cls) -> Config:
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        return cls(
            agent_model_base_url=os.getenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com"),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", "deepseek-chat"),
            agent_api_key=deepseek_key,
            summarizer_model_base_url=os.getenv(
                "SUMMARIZER_MODEL_BASE_URL", "https://api.deepseek.com"
            ),
            summarizer_model_name=os.getenv("SUMMARIZER_MODEL_NAME", "deepseek-chat"),
            summarizer_api_key=deepseek_key,
            max_steps=int(os.getenv("MAX_STEPS", "50")),
            url_note_query_strip=_bool(os.getenv("URL_NOTE_QUERY_STRIP"), True),
            restrict_goto=_bool(os.getenv("AGENT_RESTRICT_GOTO"), True),
            small_diff_threshold=int(os.getenv("SMALL_DIFF_THRESHOLD", "500")),
            max_auto_advance_hops=int(os.getenv("MAX_AUTO_ADVANCE_HOPS", "32")),
            diff_inject_max_lines=int(os.getenv("DIFF_INJECT_MAX_LINES", "10")),
        )
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_config.py -v
```

Expected: all tests pass (including any prior config tests — make sure nothing regressed).

**Step 5: Commit**

```
git add task2/src/agent/config.py task2/tests/unit/test_config.py
git commit -m "feat(task2): add anti-loop config knobs (threshold, hops, max lines)"
```

---

## Task 6: Extend `build_messages` to accept and render an optional `page_diff`

**Files:**
- Modify: `task2/src/agent/context.py`
- Modify or create: `task2/tests/unit/test_context.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_context.py`:

```python
def test_build_messages_appends_page_diff_when_present():
    from agent.context import build_messages
    msgs = build_messages(
        system="sys", goal="g", qa=[], url_notes="", tape=[],
        page_header="URL=https://x.test/",
        replan_hint=None,
        page_diff="Page changes since last turn (+1 / -0 lines):\n  + 'Sort: Params'",
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Page changes since last turn" in user["content"]
    assert "Sort: Params" in user["content"]


def test_build_messages_omits_page_diff_when_none():
    from agent.context import build_messages
    msgs = build_messages(
        system="sys", goal="g", qa=[], url_notes="", tape=[],
        page_header="URL=https://x.test/",
        replan_hint=None,
        page_diff=None,
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Page changes" not in user["content"]


def test_build_messages_page_diff_default_is_none():
    # All existing callers must still compile without passing page_diff.
    from agent.context import build_messages
    msgs = build_messages(
        system="sys", goal="g", qa=[], url_notes="", tape=[],
        page_header="URL=https://x.test/", replan_hint=None,
    )
    assert any(m["role"] == "user" for m in msgs)
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_context.py -v
```

Expected: `TypeError: build_messages() got an unexpected keyword argument 'page_diff'`.

**Step 3: Write minimal implementation**

Edit `task2/src/agent/context.py`'s `build_messages` signature and body:

```python
def build_messages(
    *,
    system: str,
    goal: str,
    qa: list[tuple[str, str]],
    url_notes: str,
    tape: list[dict[str, Any]],
    page_header: str,
    replan_hint: str | None,
    page_diff: str | None = None,
) -> list[dict]:
    sys_parts = [system]
    if replan_hint:
        sys_parts.append("")
        sys_parts.append(replan_hint)

    user_parts = [f"Goal: {goal}"]
    if qa:
        user_parts.append("")
        for q, a in qa:
            user_parts.append(f"Q: {q}\nA: {a}")
    user_parts.append("")
    user_parts.append("URL notes:")
    user_parts.append(url_notes or "(none)")
    user_parts.append("")
    user_parts.append(page_header)

    if page_diff:
        user_parts.append("")
        user_parts.append(page_diff)

    # ... rest unchanged
```

(Leave the histogram, novelty, older-steps blocks, and the recent tool-call render loop exactly as they are.)

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_context.py -v
uv run pytest tests/unit/  # nothing else regressed
```

Expected: all passing.

**Step 5: Commit**

```
git add task2/src/agent/context.py task2/tests/unit/test_context.py
git commit -m "feat(task2): build_messages accepts optional page_diff block"
```

---

## Task 7: Add `ToolRegistry.to_openai_tools_filtered(exclude=...)`

**Files:**
- Modify: `task2/src/agent/tools/registry.py`
- Modify or create: `task2/tests/unit/test_registry.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_registry.py`:

```python
def test_to_openai_tools_filtered_excludes_named_tools():
    from agent.tools.registry import Tool, ToolRegistry

    async def noop(**_):
        return ""

    reg = ToolRegistry()
    reg.register(Tool("read", "r", {"type": "object"}, noop))
    reg.register(Tool("press_key", "p", {"type": "object"}, noop))
    reg.register(Tool("click", "c", {"type": "object"}, noop))

    full = reg.to_openai_tools()
    filtered = reg.to_openai_tools_filtered(exclude={"press_key"})
    assert {t["function"]["name"] for t in full} == {"read", "press_key", "click"}
    assert {t["function"]["name"] for t in filtered} == {"read", "click"}


def test_to_openai_tools_filtered_empty_exclude_returns_all():
    from agent.tools.registry import Tool, ToolRegistry

    async def noop(**_):
        return ""

    reg = ToolRegistry()
    reg.register(Tool("read", "r", {"type": "object"}, noop))
    assert reg.to_openai_tools_filtered(exclude=set()) == reg.to_openai_tools()
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_registry.py -v
```

Expected: AttributeError on `to_openai_tools_filtered`.

**Step 3: Write minimal implementation**

Edit `task2/src/agent/tools/registry.py`. Add a method:

```python
def to_openai_tools_filtered(self, *, exclude: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in self._tools.values()
        if t.name not in exclude
    ]
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_registry.py -v
```

Expected: passes.

**Step 5: Commit**

```
git add task2/src/agent/tools/registry.py task2/tests/unit/test_registry.py
git commit -m "feat(task2): registry filtered openai tool list"
```

---

## Task 8: Wire `OffsetCache` + auto-advance into `ReactLoop` for `read`

**Files:**
- Modify: `task2/src/agent/loop.py`
- Create: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_loop_anti_repeat.py`. It uses the same `_StubBrowser` + `_mock_calls` pattern as `test_loop_happy.py`, with a stub browser whose `page.evaluate("document.body.innerText")` returns scripted text.

```python
import json
import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _StubPage:
    url = "https://x.test/"

    def __init__(self, text: str):
        self._text = text

    async def evaluate(self, script: str):
        # Only document.body.innerText is requested; return current text.
        return self._text


class _StubBrowser:
    def __init__(self, text: str):
        self.page = _StubPage(text)

    def set_text(self, text: str):
        self.page._text = text


def _mock_llm_calls(calls):
    it = iter(calls)

    async def handler(request):
        nxt = next(it)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "thinking",
                            "tool_calls": [{
                                "id": "c1", "type": "function",
                                "function": {"name": nxt[0],
                                             "arguments": json.dumps(nxt[1])},
                            }],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def _build_read_tool(browser, *, read_limit=1600):
    async def read(offset: int = 0, thought: str = ""):
        text = await browser.page.evaluate("document.body.innerText")
        return text[offset:offset + read_limit]
    return Tool("read", "read",
                {"type": "object",
                 "properties": {"offset": {"type": "integer"},
                                "thought": {"type": "string"}}}, read)


@pytest.mark.asyncio
async def test_read_auto_advances_on_repeat(tmp_path):
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read", {"offset": 0, "thought": "first read"}),
        ("read", {"offset": 0, "thought": "second read at same offset"}),
        ("done", {"status": "success", "answer": "ok"}),
    ])
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    # Step 0 obs is the 'A'*1600 window.
    # Step 1 (the second read at offset=0) MUST have been auto-advanced to 1600 → 'B'*1600.
    step1_obs = loop.tape[1]["obs"]
    assert step1_obs.startswith("[auto-advanced 0→1600")
    assert ("B" * 1600) in step1_obs
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
```

Expected: AssertionError — the second `read({offset:0})` returns the unchanged `'A'*1600` window, no `[auto-advanced ...]` annotation.

**Step 3: Write minimal implementation**

Edit `task2/src/agent/loop.py`:

1. Import the helpers at the top:

```python
from agent.page_diff import OffsetCache, plan_read
```

2. Add `OffsetCache` instances in `ReactLoop.__init__`:

```python
self.read_cache = OffsetCache()
self.list_interactive_cache = OffsetCache()
self._read_limit = 2000  # matches _READ_LIMIT in tools/browser.py
self._max_auto_advance_hops = 32
```

3. Inside `run()`, *before* `obs = await self.registry.call(name, args)` (line 200), intercept `read` calls:

```python
auto_advance_prefix: str | None = None
if name == "read" and isinstance(args, dict):
    requested_offset = int(args.get("offset", 0) or 0)
    try:
        text = await self.browser.page.evaluate("document.body.innerText")
    except Exception:
        text = ""
    plan = plan_read(
        text=text,
        requested_offset=requested_offset,
        cache=self.read_cache,
        read_limit=self._read_limit,
        max_hops=self._max_auto_advance_hops,
    )
    if plan.exhausted:
        obs = (
            f"(end of page; tried offsets up to {plan.served_offset}, "
            f"page length {len(text)}). Try read_grep or done()."
        )
        # Skip the actual tool call — synthetic obs.
        self.tape.append({"thought": thought, "action": name, "args": args, "obs": obs})
        self.trace.write({"type": "step", "payload": {
            "n": step_idx, "thought": thought, "action": name,
            "args": args, "obs": obs}})
        # Continue to next loop iteration without invoking the tool.
        continue
    # Override the args so the actual `read` call returns what the planner picked.
    args = {**args, "offset": plan.served_offset}
    self.read_cache.record(offset=plan.served_offset, served=plan.served_text)
    if plan.advanced_from is not None:
        auto_advance_prefix = (
            f"[auto-advanced {plan.advanced_from}→{plan.served_offset}: "
            f"{plan.advanced_from} unchanged since prior read]"
        )
```

After the actual tool call (`obs = await self.registry.call(...)`), prepend the prefix when set:

```python
if auto_advance_prefix is not None and isinstance(obs, str):
    obs = f"{auto_advance_prefix} {obs}"
```

**Important note on `continue`:** the original code records the step and writes the trace *after* the tool call. In the `exhausted` branch we are recording manually and skipping. The manual block above mirrors the existing recording shape. Audit `loop.py` to ensure the no-progress streak counter and other post-step bookkeeping are also handled in the synthetic branch — copy the relevant lines (the `_obs_fingerprint` / `no_progress_streak` block at lines 233-239) into the synthetic branch.

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
```

Expected: passes.

Also re-run the full unit suite:

```
uv run pytest tests/unit/ -v
```

Expected: no regressions.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): auto-advance read when offset already served"
```

---

## Task 9: Hide `read` when end-of-page is reached; re-expose on page mutation

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_read_hidden_after_end_of_page_until_mutation(tmp_path):
    """When auto-advance walks past len(text), the synthetic obs is
    returned AND `read` must be removed from next turn's tool list. After
    a state-changing tool produces a global diff, `read` is exposed again."""
    short_text = "A" * 1600
    browser = _StubBrowser(short_text)
    transport = _mock_llm_calls([
        ("read", {"offset": 0, "thought": "first read"}),
        ("read", {"offset": 0, "thought": "second read; should auto-advance and exhaust"}),
        ("done", {"status": "success", "answer": "ok"}),
    ])
    # The third LLM call should NOT see `read` in its tool list. We capture
    # the tool list from the third request via the mock transport.
    captured_tools: list[list[str]] = []

    async def capturing_handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        # Reuse the scripted call sequence:
        return await transport.handler(request)

    capturing_transport = httpx.MockTransport(capturing_handler)

    llm = LLMClient("http://t/v1", "m", transport=capturing_transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    # Third turn (index 2) should not have `read` in tools.
    assert "read" not in captured_tools[2]
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py::test_read_hidden_after_end_of_page_until_mutation -v
```

Expected: AssertionError — `read` is still in the tools list.

**Step 3: Write minimal implementation**

In `loop.py`:

1. Add `self._hidden_tools: set[str] = set()` in `__init__`.
2. In the `exhausted` branch from Task 8, add `self._hidden_tools.add("read")`.
3. Change the `tools = self.registry.to_openai_tools()` line — move it INSIDE the loop and make it filtered:

```python
for step_idx in range(self.max_steps):
    tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)
    # ... rest of loop
```

4. We also need to clear `_hidden_tools` after a state-changing action. We don't yet have the global-text-cache wired in — that comes in Task 10. For now, **defer the re-exposure logic to Task 10** and keep this test passing by having `read` stay hidden for the rest of the run (the third turn, where `done` is called, is enough to satisfy the assertion).

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
```

Expected: passes.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): hide read when auto-advance exhausts the page"
```

---

## Task 10: Wire `GlobalTextCache`, small-diff injection, cache invalidation, and re-exposure

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_small_diff_injected_after_state_change(tmp_path):
    text_v1 = "Sort: Score\nlist of items\n"
    text_v2 = "Sort: Params\nlist of items\n"
    browser = _StubBrowser(text_v1)

    captured_user_prompts: list[str] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        for m in body["messages"]:
            if m["role"] == "user":
                captured_user_prompts.append(m["content"])
        # Scripted: click then done. After click, switch the page text.
        idx = len(captured_user_prompts) - 1
        if idx == 0:
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "click",
                                             "arguments": json.dumps({"id": 1})}}]}}]})
        elif idx == 1:
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "done",
                                             "arguments": json.dumps(
                                                 {"status": "success", "answer": "ok"})}}]}}]})
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)

    async def click(id: int, thought: str = ""):
        browser.set_text(text_v2)  # mutate page
        return f"clicked id={id}"

    reg.register(Tool("click", "click",
                      {"type": "object",
                       "properties": {"id": {"type": "integer"},
                                      "thought": {"type": "string"}},
                       "required": ["id"]}, click))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    # The second user prompt (turn index 1) should include the small diff.
    second_prompt = captured_user_prompts[1]
    assert "Page changes since last turn" in second_prompt
    assert "Sort: Params" in second_prompt
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py::test_small_diff_injected_after_state_change -v
```

Expected: AssertionError — the second prompt has no diff block.

**Step 3: Write minimal implementation**

Edit `loop.py`:

1. Import: `from agent.page_diff import GlobalTextCache, format_small_diff, should_inject_diff, diff_char_size`.
2. In `__init__`, add `self.global_cache = GlobalTextCache()` and remember the config knobs (or accept a `config: Config` kwarg — simplest is an extra constructor param with sensible defaults; thread it through `server.py` in Task 13).
3. At the top of each loop iteration, **before** `build_messages`, compute the page diff once:

```python
try:
    current_text = await self.browser.page.evaluate("document.body.innerText")
except Exception:
    current_text = ""

prev_text = self.global_cache.previous()
diff_block: str | None = None
if prev_text is not None:
    if should_inject_diff(previous=prev_text, current=current_text,
                          threshold=self._small_diff_threshold):
        diff_block = format_small_diff(previous=prev_text, current=current_text,
                                       max_lines=self._diff_inject_max_lines)
    if prev_text != current_text:
        # Page mutated → invalidate offset caches and re-expose hidden tools.
        self.read_cache.clear()
        self.list_interactive_cache.clear()
        self._hidden_tools.clear()
self.global_cache.update(current_text)
```

4. Pass `page_diff=diff_block` to `build_messages`.

**Important:** the `current_text` capture must happen *before* the LLM call in the loop iteration, but the diff is between this turn's `current_text` and the *previous* iteration's. The `update()` at the end of the block stores `current_text` for next turn's comparison.

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
uv run pytest tests/unit/ -v   # full unit regression
```

Expected: all pass.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): inject small page diffs and clear caches on mutation"
```

---

## Task 11: Hide `press_key` when its prior call produced zero global diff

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_press_key_hidden_when_no_global_diff(tmp_path):
    text = "static text only\n"
    browser = _StubBrowser(text)

    captured_tools: list[list[str]] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        idx = len(captured_tools) - 1
        if idx == 0:
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "press_key",
                                             "arguments": json.dumps({"key": "Home"})}}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "done",
                                         "arguments": json.dumps(
                                             {"status": "success", "answer": "ok"})}}]}}]})

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)

    async def press_key(key: str, thought: str = ""):
        return f"pressed {key}"  # does not mutate browser text

    reg.register(Tool("press_key", "press",
                      {"type": "object",
                       "properties": {"key": {"type": "string"},
                                      "thought": {"type": "string"}},
                       "required": ["key"]}, press_key))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    # Turn 0: press_key was available. Turn 1: press_key must be hidden because
    # its previous call produced zero global diff.
    assert "press_key" in captured_tools[0]
    assert "press_key" not in captured_tools[1]
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py::test_press_key_hidden_when_no_global_diff -v
```

Expected: AssertionError — `press_key` still in the second turn.

**Step 3: Write minimal implementation**

In `loop.py` after the diff computation block (Task 10):

```python
# If the previous step was press_key and the page didn't change, hide it.
if self.tape and self.tape[-1]["action"] == "press_key" and prev_text == current_text:
    self._hidden_tools.add("press_key")
```

The existing cache-clear branch (`if prev_text != current_text`) will lift the hide on the next mutation.

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
uv run pytest tests/unit/ -v
```

Expected: all pass.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): hide press_key after a no-op call"
```

---

## Task 12: Apply auto-advance to `list_interactive`

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_list_interactive_auto_advances(tmp_path):
    """list_interactive at the same offset twice on an unchanged page should
    auto-advance just like read."""
    # Snapshot is a JSON list. We approximate by returning fixed strings.
    pages = ["snap-A", "snap-B", "snap-C"]
    # Each "offset" maps to a different page in our stub.

    async def list_interactive(offset: int = 0, limit: int = 50, thought: str = ""):
        idx = offset // limit
        if idx >= len(pages):
            return ""
        return pages[idx]

    text = "irrelevant"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("list_interactive", {"offset": 0, "limit": 1, "thought": "first"}),
        ("list_interactive", {"offset": 0, "limit": 1, "thought": "second"}),
        ("done", {"status": "success", "answer": "ok"}),
    ])
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)
    reg.register(Tool("list_interactive", "li",
                      {"type": "object",
                       "properties": {"offset": {"type": "integer"},
                                      "limit": {"type": "integer"},
                                      "thought": {"type": "string"}}},
                      list_interactive))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    # Step 1 should have been auto-advanced.
    step1 = loop.tape[1]
    assert step1["action"] == "list_interactive"
    assert step1["obs"].startswith("[auto-advanced 0→1")  # advanced by `limit` of 1
    assert "snap-B" in step1["obs"]
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py::test_list_interactive_auto_advances -v
```

Expected: AssertionError.

**Step 3: Write minimal implementation**

In `loop.py`, generalize the `read`-interception block. The cleanest approach is to factor it out:

```python
async def _maybe_auto_advance(
    self, *, name: str, args: dict, cache: OffsetCache, scope: str,
) -> tuple[dict, str | None, bool]:
    """Returns (possibly-modified args, optional auto-advance prefix, exhausted).

    `scope` is the unit ('chars' for read, 'items' for list_interactive) used
    to fetch the underlying corpus."""
    if scope == "chars":
        try:
            corpus = await self.browser.page.evaluate("document.body.innerText")
        except Exception:
            corpus = ""
        stride = self._read_limit
    else:
        # For list_interactive we don't have a precomputed corpus length; we
        # pretend the corpus is unbounded and rely on the underlying tool
        # returning empty / shorter content at end-of-list. We probe by
        # calling the tool itself with the candidate offset before deciding.
        # Simpler: just compare cache hits per offset and let the underlying
        # tool's empty return signal end-of-list.
        ...
```

Implementation note: for `list_interactive`, the simplest correct approach is to check the cache *after* the tool runs once at the requested offset, and if the cache hit, repeat with offset+limit, until `max_hops` or the tool returns an obs identical to a previously-served one. This avoids needing a separate corpus-length probe.

Concretely, for `list_interactive`:

```python
if name == "list_interactive" and isinstance(args, dict):
    requested_offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 50) or 50)
    served_offset = requested_offset
    served = await self.registry.call(name, {**args, "offset": served_offset})
    hops = 0
    while self.list_interactive_cache.was_served(
        offset=served_offset, candidate=served if isinstance(served, str) else json.dumps(served)
    ):
        hops += 1
        if hops >= self._max_auto_advance_hops:
            break
        served_offset += limit
        served = await self.registry.call(name, {**args, "offset": served_offset})
    served_str = served if isinstance(served, str) else json.dumps(served)
    self.list_interactive_cache.record(offset=served_offset, served=served_str)
    if served_offset != requested_offset:
        served_str = (
            f"[auto-advanced {requested_offset}→{served_offset}: "
            f"{requested_offset} unchanged since prior list_interactive] {served_str}"
        )
    obs = served_str
    args = {**args, "offset": served_offset}
    # Skip the normal `obs = await registry.call(...)` path for this tool.
```

Implement either as inline branches or a small helper. Either is fine — keep readability over symmetry.

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
uv run pytest tests/unit/ -v
```

Expected: passes.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): auto-advance list_interactive on cache hits"
```

---

## Task 13: `read_grep` dedup synthetic obs

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/tests/unit/test_loop_anti_repeat.py`

**Step 1: Write the failing test**

Append to `task2/tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_read_grep_dedup_returns_synthetic_when_pattern_repeats(tmp_path):
    text = "the quick brown fox jumps over the lazy dog needle here\n"
    browser = _StubBrowser(text)

    async def read_grep(pattern: str, window: int = 200, thought: str = ""):
        body = await browser.page.evaluate("document.body.innerText")
        idx = body.lower().find(pattern.lower())
        if idx < 0:
            return f"NOT FOUND: {pattern!r}"
        return body[max(0, idx - window): idx + len(pattern) + window]

    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "needle", "thought": "first grep"}),
        ("read_grep", {"pattern": "needle", "thought": "same pattern again"}),
        ("done", {"status": "success", "answer": "ok"}),
    ])
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/",
                            question_channel=qc)
    reg.register(Tool("read_grep", "rg",
                      {"type": "object",
                       "properties": {"pattern": {"type": "string"},
                                      "window": {"type": "integer"},
                                      "thought": {"type": "string"}},
                       "required": ["pattern"]}, read_grep))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=5,
    )
    await loop.run("anything")

    step1 = loop.tape[1]
    assert step1["action"] == "read_grep"
    assert "already searched" in step1["obs"].lower()
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_anti_repeat.py::test_read_grep_dedup_returns_synthetic_when_pattern_repeats -v
```

Expected: AssertionError — repeat call returns the actual grep window again.

**Step 3: Write minimal implementation**

In `loop.py`:

1. Add `self._read_grep_seen: dict[str, int] = {}` in `__init__` (maps pattern → step index when first served).
2. Before the existing `_check_read_grep_grounding` call, intercept exact-pattern repeats:

```python
if name == "read_grep" and isinstance(args, dict):
    pat = (args.get("pattern", "") or "").lower()
    if pat and pat in self._read_grep_seen:
        prior_step = self._read_grep_seen[pat]
        obs = (
            f'(pattern "{args.get("pattern", "")}" already searched at '
            f"step {prior_step}, no new matches.)"
        )
        # synthetic — bypass tool call. Append step manually as in Task 8.
        ...
```

3. After a successful `read_grep` call (i.e. the obs is not synthetic, not a `NOT FOUND`, not an `ERROR:`), record `self._read_grep_seen[pat] = step_idx`.
4. **Cache invalidation**: when `prev_text != current_text` (the existing block in Task 10), also clear `self._read_grep_seen`. The page changed → grepping the same pattern is now meaningful again.

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/test_loop_anti_repeat.py -v
uv run pytest tests/unit/ -v
```

Expected: all pass.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): read_grep dedup synthetic obs on repeat patterns"
```

---

## Task 14: Surface knobs through `server.py` and any `ReactLoop` constructors

**Files:**
- Modify: `task2/src/agent/server.py`
- Modify: `task2/src/agent/loop.py`

**Step 1: Write the failing test**

The unit tests already exercise `ReactLoop` directly. Add one integration-light test that asserts the wiring from `Config.from_env` reaches `ReactLoop`:

Append to `task2/tests/unit/test_loop_happy.py`:

```python
def test_react_loop_accepts_anti_loop_config_kwargs(tmp_path):
    from agent.loop import ReactLoop
    # No-op stub for everything; we're only checking the constructor signature.
    class _Stub:
        page = type("P", (), {"url": "https://a.test/"})()
    loop = ReactLoop(
        llm=None, registry=None, notes=None, summarizer=None, trace=None,
        browser=_Stub(), question_channel=None, max_steps=1,
        small_diff_threshold=200,
        max_auto_advance_hops=8,
        diff_inject_max_lines=4,
    )
    assert loop._small_diff_threshold == 200
    assert loop._max_auto_advance_hops == 8
    assert loop._diff_inject_max_lines == 4
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_happy.py::test_react_loop_accepts_anti_loop_config_kwargs -v
```

Expected: TypeError on the kwargs.

**Step 3: Write minimal implementation**

1. In `loop.py`, add the kwargs to `__init__`:

```python
def __init__(
    self, *, ..., max_steps: int = 50, send_transient=None,
    small_diff_threshold: int = 500,
    max_auto_advance_hops: int = 32,
    diff_inject_max_lines: int = 10,
):
    ...
    self._small_diff_threshold = small_diff_threshold
    self._max_auto_advance_hops = max_auto_advance_hops
    self._diff_inject_max_lines = diff_inject_max_lines
```

2. Replace any earlier hardcoded values inside `run()` with these instance attributes.
3. In `server.py`, find every place that constructs `ReactLoop(...)` and pass the values from `Config.from_env()`. Search:

```
grep -n "ReactLoop(" task2/src/agent/server.py
```

For each occurrence, add:
```python
small_diff_threshold=cfg.small_diff_threshold,
max_auto_advance_hops=cfg.max_auto_advance_hops,
diff_inject_max_lines=cfg.diff_inject_max_lines,
```

**Step 4: Run test to verify it passes**

```
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v   # ensure server still works
```

Expected: all pass.

**Step 5: Commit**

```
git add task2/src/agent/loop.py task2/src/agent/server.py task2/tests/unit/test_loop_happy.py
git commit -m "feat(task2): thread anti-loop knobs from Config to ReactLoop"
```

---

## Task 15: End-to-end eval case

**Files:**
- Create: `task2/tests/evals/test_anti_loop_canirun.py` (replay-based, not live web)
- Reference: `task2/data/traces/c20b9a834288421fb308cd7101e530c2.jsonl` (the failed bench)

**Step 1: Write the failing test**

The cheapest meaningful eval is a *replay* test: given the same DOM snapshots the failed run encountered, drive the new loop and assert it doesn't loop. Reading 50 raw events into a stub browser is heavy; a minimal version is:

```python
"""Replay-style eval. Drives the loop with a stub browser whose innerText
mimics the canirun.ai page after the GPU-filter step. Confirms the agent
doesn't loop more than 3 times on read at the same offset before either
auto-advancing past it or the tool being hidden."""

import json
import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


CANIRUN_TEXT = (
    "CanIRun.ai\nupdated 26d ago\n[compare]\n[tier list]\n[docs]\n[why]\n"
    + ("filler line.\n" * 100)
    + "Gemma 4 E4B IT\nGemma\n5d ago\n4.6\nGB\n19%\nRUNS GREAT\n96/100\n"
)


class _Page:
    url = "https://canirun.ai/device/rtx-3090"
    def __init__(self, text): self._t = text
    async def evaluate(self, _): return self._t


class _Browser:
    def __init__(self, text): self.page = _Page(text)


@pytest.mark.asyncio
async def test_canirun_no_read_loop(tmp_path):
    """If the agent calls read({offset:0}) ten times in a row, the new
    machinery must auto-advance every repeat. By the third repeat the
    served offset must be > 0."""
    browser = _Browser(CANIRUN_TEXT)

    # Script: 10 read({offset:0}) calls, then done().
    scripted = [("read", {"offset": 0, "thought": f"r{i}"}) for i in range(10)]
    scripted.append(("done", {"status": "success", "answer": "Gemma 4 E4B IT"}))

    it = iter(scripted)

    async def handler(request):
        nxt = next(it)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": nxt[0],
                                         "arguments": json.dumps(nxt[1])}}]}}]})

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://canirun.ai/",
                            question_channel=qc)

    async def read(offset: int = 0, thought: str = ""):
        body = await browser.page.evaluate("")
        return body[offset:offset + 2000]

    reg.register(Tool("read", "r",
                      {"type": "object",
                       "properties": {"offset": {"type": "integer"},
                                      "thought": {"type": "string"}}}, read))
    reg.register(Tool("done", "done",
                      {"type": "object",
                       "properties": {"status": {"type": "string"},
                                      "answer": {"type": "string"}},
                       "required": ["status", "answer"]},
                      meta["done"]))
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm, registry=reg, notes=None, summarizer=None, trace=trace,
        browser=browser, question_channel=qc, max_steps=15,
    )
    await loop.run("RTX 3090 best LLM?")

    # By the third read in the tape, served offset must have advanced.
    read_steps = [s for s in loop.tape if s["action"] == "read"]
    assert len(read_steps) >= 3
    third = read_steps[2]
    assert third["args"]["offset"] > 0, (
        f"Third read should have been auto-advanced, but args were {third['args']}"
    )
```

**Step 2: Run test to verify it fails or passes**

```
uv run pytest tests/evals/test_anti_loop_canirun.py -v
```

Expected: passes (Tasks 8-13 should already make this work). If it doesn't pass, that's a real signal that the implementation is incomplete — debug before moving on.

**Step 3: No additional implementation if test passes**

If it passed in step 2, this task is verifying coverage. If it failed, identify which earlier task's implementation is incomplete and fix it (still TDD — the eval is now the failing test).

**Step 4: Run the full suite**

```
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

Expected: all green, ruff clean.

**Step 5: Commit**

```
git add task2/tests/evals/test_anti_loop_canirun.py
git commit -m "test(task2): replay-based eval for canirun.ai read-loop fix"
```

---

## Task 16: Final lint & full regression sweep

**Files:** none

**Step 1: Run full test suite**

```
cd task2
uv run pytest -v
```

All unit, integration, and eval tests must pass.

**Step 2: Lint and format**

```
uv run ruff check .
uv run ruff format --check .
```

Expected: zero diagnostics. If anything fires, fix it (no `# noqa` shortcuts).

**Step 3: Inspect the diff one last time**

```
git log --oneline main..HEAD
git diff main...HEAD --stat
```

Read the file list. Anything that's not directly motivated by the design doc (e.g. drive-by refactors, reformat-only changes elsewhere) — revert it. Per CLAUDE.md, scope discipline is a hard rule.

**Step 4: Commit (only if anything changed)**

If lint produced fixes:

```
git add -A
git commit -m "chore(task2): ruff format/lint sweep after anti-loop work"
```

Otherwise: nothing to commit.

**Step 5: Hand back**

Report:
- All tests passing.
- Branch ahead of `main` by N commits.
- Eval `test_canirun_no_read_loop` passing.
- Ready for review / merge.

---

## What's intentionally NOT in this plan

Per the design doc's "Out of scope" section — these are separate brainstorms:

- A stopping rule that nudges the agent toward `done()` once goal-shaped keywords are present (finding #1 of `observations.md`).
- Sort-cycling detection (finding #4).
- Planner weighting for goal-shaped link names like `[tier list]` (finding #6).

If you find yourself adding logic for any of those — stop, brainstorm separately, write a new plan.
