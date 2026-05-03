# Loop Tuning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply the five tuning changes from `docs/plans/2026-05-02-loop-tuning-design.md` — line-based diff metric (50/50), `NO_PROGRESS_GIVEUP=9` and `NOVELTY_WINDOW=8`, `restrict_goto` default `True` in browser tool builders, and a visited-URL trail feeding `allowlist_sources`.

**Architecture:** Five independent changes to `task2/src/agent/{page_diff,loop,context,tools/browser,server}.py` plus their tests. Each change is one or two failing-test-then-implement cycles followed by a commit. No new modules; no public API changes outside the agent crate. Existing TDD workflow per `task2/CLAUDE.md`: red → green → ruff → commit.

**Tech Stack:** Python 3.12, `uv` (run from `task2/`), `pytest` (`uv run pytest`), `ruff` (`uv run ruff check . && uv run ruff format .`).

---

## Conventions for every task

- Run all commands from `/home/pgi/v_coding_test2/task2/`.
- Always: `uv run pytest <path>::<name> -v` for the targeted test, then `uv run pytest` for the whole suite, then `uv run ruff check . && uv run ruff format .` before committing.
- Commits: conventional style (`feat(task2): …`, `refactor(task2): …`, `test(task2): …`).
- After every commit, the working tree must be ruff-clean and the entire `task2` test suite must be green.

---

## Task 1: Rename `diff_char_size` → `diff_line_size`, switch metric to lines

**Files:**
- Modify: `task2/src/agent/page_diff.py:115-129` (the `diff_char_size` body)
- Modify: `task2/tests/unit/test_page_diff_global.py:3,46-49` (import + the existing char-size test)

**Step 1: Update the failing test to assert line counts**

Open `task2/tests/unit/test_page_diff_global.py`. Replace lines 1-6 import block:

```python
from agent.page_diff import (
    GlobalTextCache,
    diff_line_size,
    format_small_diff,
    should_inject_diff,
)
```

Replace `test_diff_char_size_counts_added_plus_removed` (lines 46-49) with:

```python
def test_diff_line_size_counts_added_plus_removed_lines():
    # one removed line "abc def", one added "abc XYZ" → 2 lines.
    assert diff_line_size(previous="abc def\nghi\n", current="abc XYZ\nghi\n") == 2


def test_diff_line_size_counts_each_changed_line_once():
    prev = "a\nb\nc\nd\n"
    curr = "a\nB\nc\nD\n"
    # b→B and d→D: 2 removed + 2 added = 4.
    assert diff_line_size(previous=prev, current=curr) == 4


def test_diff_line_size_zero_when_unchanged():
    assert diff_line_size(previous="x\ny\n", current="x\ny\n") == 0
```

**Step 2: Run tests to verify they fail**

```
uv run pytest tests/unit/test_page_diff_global.py -v
```
Expected: `ImportError: cannot import name 'diff_line_size' from 'agent.page_diff'`.

**Step 3: Rewrite `diff_char_size` as `diff_line_size`**

In `task2/src/agent/page_diff.py`, replace the `diff_char_size` definition (currently around lines 115-129) with:

```python
def diff_line_size(*, previous: str, current: str) -> int:
    """Count of lines added + lines removed in the unified line-level diff
    between `previous` and `current`. Used as the threshold metric for
    context injection."""
    if previous == current:
        return 0
    prev_lines = previous.splitlines(keepends=False)
    curr_lines = current.splitlines(keepends=False)
    total = 0
    for line in difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            total += 1
    return total
```

Then update `should_inject_diff` (right below) to call the new name:

```python
def should_inject_diff(*, previous: str, current: str, threshold: int) -> bool:
    if previous == current:
        return False
    return diff_line_size(previous=previous, current=current) <= threshold
```

**Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_page_diff_global.py -v
uv run pytest tests/unit/test_loop_happy.py -v   # imports may be transitively affected
```
Expected: all green.

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/page_diff.py task2/tests/unit/test_page_diff_global.py
git commit -m "refactor(task2): rename diff_char_size to diff_line_size and count lines

The diff-injection threshold metric is more meaningful in lines than in
characters. Pure rename + metric swap; should_inject_diff signature is
unchanged."
```

---

## Task 2: Bump `should_inject_diff` defaults to 50 / 50 lines

**Files:**
- Modify: `task2/src/agent/loop.py:106,108` (constructor defaults)
- Modify: `task2/src/agent/config.py:35,37` (env defaults)
- Modify: `task2/tests/unit/test_config.py:55,57` (default-asserting test)
- Add: a new test in `task2/tests/unit/test_page_diff_global.py`

**Step 1: Write a failing default-behavior test**

Append to `task2/tests/unit/test_page_diff_global.py`:

```python
def test_should_inject_diff_threshold_50_admits_30_line_diff():
    prev = "\n".join(f"old {i}" for i in range(30))
    curr = "\n".join(f"new {i}" for i in range(30))
    # 30 removed + 30 added = 60 → too big.
    assert should_inject_diff(previous=prev, current=curr, threshold=50) is False


def test_should_inject_diff_threshold_50_admits_25_line_change():
    prev = "\n".join(f"L{i}" for i in range(50))
    curr_lines = [f"L{i}" for i in range(50)]
    for i in range(25):
        curr_lines[i] = f"X{i}"
    curr = "\n".join(curr_lines)
    # 25 removed + 25 added = 50 → exactly at threshold, admit.
    assert should_inject_diff(previous=prev, current=curr, threshold=50) is True
```

Update the existing default-asserting test in `task2/tests/unit/test_config.py:54-57`:

```python
    c = Config.from_env()
    assert c.small_diff_threshold == 50
    assert c.max_auto_advance_hops == 32
    assert c.diff_inject_max_lines == 50
```

**Step 2: Run tests to verify they fail**

```
uv run pytest tests/unit/test_page_diff_global.py::test_should_inject_diff_threshold_50_admits_25_line_change tests/unit/test_config.py::test_config_loads_anti_loop_defaults -v
```
Expected: the two new page-diff cases pass already (math is metric-only); the config test fails because defaults are still 500/10.

**Step 3: Update defaults**

In `task2/src/agent/loop.py:106,108`:

```python
        small_diff_threshold: int = 50,
        max_auto_advance_hops: int = 32,
        diff_inject_max_lines: int = 50,
```

In `task2/src/agent/config.py:35,37`:

```python
            small_diff_threshold=int(os.getenv("SMALL_DIFF_THRESHOLD", "50")),
            ...
            diff_inject_max_lines=int(os.getenv("DIFF_INJECT_MAX_LINES", "50")),
```

**Step 4: Run tests to verify pass**

```
uv run pytest tests/unit/test_page_diff_global.py tests/unit/test_config.py tests/unit/test_loop_happy.py -v
uv run pytest
```
Expected: all green.

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/loop.py task2/src/agent/config.py task2/tests/unit/test_page_diff_global.py task2/tests/unit/test_config.py
git commit -m "feat(task2): default diff inject threshold and cap to 50 lines

Both ReactLoop kwargs and Config env defaults move from 500 chars / 10
lines to 50 / 50 lines. With threshold == cap, when we inject we never
truncate the diff block."
```

---

## Task 3: Tighten `NO_PROGRESS_GIVEUP` to 9 and `NOVELTY_WINDOW` to 8

**Files:**
- Modify: `task2/src/agent/loop.py:48` (`NO_PROGRESS_GIVEUP = 12`)
- Modify: `task2/src/agent/context.py:7` (`NOVELTY_WINDOW = 10`)
- Add: a new test in `task2/tests/unit/test_loop_no_progress.py`

**Step 1: Write a constants test**

Append to `task2/tests/unit/test_loop_no_progress.py`:

```python
def test_no_progress_giveup_is_9():
    from agent.loop import NO_PROGRESS_GIVEUP

    assert NO_PROGRESS_GIVEUP == 9


def test_novelty_window_is_8():
    from agent.context import NOVELTY_WINDOW

    assert NOVELTY_WINDOW == 8
```

**Step 2: Run tests to verify they fail**

```
uv run pytest tests/unit/test_loop_no_progress.py::test_no_progress_giveup_is_9 tests/unit/test_loop_no_progress.py::test_novelty_window_is_8 -v
```
Expected: both fail with `assert 12 == 9` and `assert 10 == 8`.

**Step 3: Drop the constants**

`task2/src/agent/loop.py:48`:

```python
NO_PROGRESS_GIVEUP = 9
```

`task2/src/agent/context.py:7`:

```python
NOVELTY_WINDOW = 8
```

**Step 4: Run the whole suite**

```
uv run pytest
```

Expected: green. The existing `test_no_progress_streak_forces_done_failed` only asserts `call_n["i"] <= 16` and the `_no_progress()` walker only requires `len(tape) >= NOVELTY_WINDOW + 1` (now 9), so its 30-iteration run still trips the streak comfortably. If a per-test count tightens to a number now infeasible, tighten the bound in the same commit (do **not** weaken any assertion to make it pass — if it fails for a real reason, debug instead).

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/loop.py task2/src/agent/context.py task2/tests/unit/test_loop_no_progress.py
git commit -m "feat(task2): tighten no-progress giveup to 9 and novelty window to 8

Aligns the no-progress trigger with K_RECENT=8: we now bail one step past
the visible recent-context window, instead of waiting for four duplicates
to slip into the lossy per-step summary form."
```

---

## Task 4: Default `restrict_goto=True` in browser tool builders

**Files:**
- Modify: `task2/src/agent/tools/browser.py:45,165` (the two `restrict_goto: bool = False` defaults)
- Modify: `task2/tests/unit/test_goto_guard.py:97-101` (the "unrestricted by default" test) and any tests that relied on the old default

**Step 1: Flip the existing test to assert restriction-by-default**

Replace `test_goto_unrestricted_by_default` in `task2/tests/unit/test_goto_guard.py:95-101` with:

```python
@pytest.mark.asyncio
async def test_goto_restricted_by_default_blocks_unknown_url():
    sess = _fake_session("https://anything.test/")
    tools = build_browser_tools(
        sess, allowlist_sources=lambda: ["goal mentions only example.com"]
    )
    obs = await tools["goto"]("https://anything.test/")
    assert obs.startswith("ERROR: blocked goto")
    sess.page.goto.assert_not_awaited()


@pytest.mark.asyncio
async def test_goto_explicit_unrestricted_still_works():
    sess = _fake_session("https://anything.test/")
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["goto"]("https://anything.test/")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()
```

Audit the rest of `task2/tests/unit/test_goto_guard.py` — for any other test that calls `build_browser_tools(sess)` without `restrict_goto=` *and* expects a `goto` to succeed (e.g. lines 100, 127, 138 in the current file), add an explicit `restrict_goto=False` so the test continues to exercise its actual subject. **Do not** add an allowlist as a workaround — these tests are about behaviour orthogonal to the guard.

Audit `task2/tests/integration/test_browser_tools_nav_read.py` and `test_browser_tools_interact.py`. Same rule: any `build_browser_tools(s)` whose test exercises a real `goto`/nav path needs `restrict_goto=False` added explicitly. (Most interact tests don't call `goto` and need no change; double-check by searching for `goto(` inside each test function.)

**Step 2: Run tests to verify the new ones fail and any audited ones still pass**

```
uv run pytest tests/unit/test_goto_guard.py -v
uv run pytest tests/integration/test_browser_tools_nav_read.py tests/integration/test_browser_tools_interact.py -v
```

Expected: `test_goto_restricted_by_default_blocks_unknown_url` fails (default is still `False`); other audited tests pass (you've added explicit `restrict_goto=False` where needed).

**Step 3: Flip the defaults**

`task2/src/agent/tools/browser.py:45`:

```python
def build_browser_tools(
    session: BrowserSession,
    *,
    restrict_goto: bool = True,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
```

`task2/src/agent/tools/browser.py:165`:

```python
def build_browser_tool_list(
    session: BrowserSession,
    *,
    restrict_goto: bool = True,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> list[Tool]:
```

**Step 4: Full suite**

```
uv run pytest
```

Expected: green. If anything else fails, the test was implicitly relying on the old default — add `restrict_goto=False` (or a real allowlist) at that callsite in the same commit and re-run.

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/tools/browser.py task2/tests/unit/test_goto_guard.py task2/tests/integration/test_browser_tools_nav_read.py task2/tests/integration/test_browser_tools_interact.py
git commit -m "feat(task2): default restrict_goto=True in browser tool builders

Production server already opted in via Config.restrict_goto. This makes
the function defaults match, so any caller that wants unrestricted goto
must say so explicitly. Tests that exercise real navigation pass
restrict_goto=False or a real allowlist."
```

---

## Task 5a: `ReactLoop` exposes an `on_visit` callback

**Files:**
- Modify: `task2/src/agent/loop.py:95-134` (`__init__`) and `loop.py:189-217` (top of `run`)
- Add: a unit test in `task2/tests/unit/test_loop_visit_trail.py` (new file)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_loop_visit_trail.py`:

```python
"""ReactLoop fires `on_visit(url)` once per turn at the top of the iteration,
deduping consecutive identical URLs. The server uses this to maintain a
visited-URL trail for the goto allowlist."""

from __future__ import annotations

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.loop import ReactLoop
from agent.trace import TraceWriter


class _Browser:
    def __init__(self, urls: list[str]):
        self._urls = urls
        self._i = 0
        self.page = self  # quack: page.url and page.evaluate

    @property
    def url(self) -> str:
        u = self._urls[min(self._i, len(self._urls) - 1)]
        self._i += 1
        return u

    async def evaluate(self, _expr: str) -> str:
        return ""


@pytest.mark.asyncio
async def test_on_visit_invoked_each_turn_with_dedup(tmp_path):
    seen: list[str] = []
    call_n = {"i": 0}

    async def handler(request):
        call_n["i"] += 1
        # First two turns: no-op tool. Third turn: done().
        name = "noop" if call_n["i"] < 3 else "done"
        args = "{}" if name == "noop" else json.dumps({"status": "success", "answer": "ok"})
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
                                    "function": {"name": name, "arguments": args},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noop():
        return "noop-obs"

    async def done(status: str, answer: str):
        from agent.tools.meta import LoopDone

        raise LoopDone(status, answer)

    reg.register(Tool("noop", "x", {"type": "object", "properties": {}}, noop))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            done,
        )
    )

    # url stream: A, A, B → expect dedup to record [A, B] (consecutive A
    # collapses to one).
    browser = _Browser(["https://a.test/", "https://a.test/", "https://b.test/"])

    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=TraceWriter(tmp_path / "t.jsonl"),
        browser=browser,
        question_channel=QuestionChannel(),
        max_steps=10,
        on_visit=seen.append,
    )
    await loop.run("g")
    assert seen == ["https://a.test/", "https://b.test/"]
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_loop_visit_trail.py -v
```
Expected: `TypeError: __init__() got an unexpected keyword argument 'on_visit'`.

**Step 3: Add `on_visit` to `ReactLoop` and call it once per turn with dedup**

In `task2/src/agent/loop.py`, add to `__init__` (after the existing kwargs):

```python
        on_visit: Callable[[str], None] | None = None,
```

(Add `from collections.abc import Callable` to the imports if not already present.)

Store it:

```python
        self._on_visit = on_visit
        self._last_visit: str | None = None
```

At the very top of the per-iteration body in `run()` — after the `step_idx == self.max_steps - 1` short-circuit but **before** anything else (including the `current_text` evaluate) — add:

```python
            if self._on_visit is not None:
                u = self._current_url()
                if u and u != self._last_visit:
                    self._on_visit(u)
                    self._last_visit = u
```

**Step 4: Run test to verify pass**

```
uv run pytest tests/unit/test_loop_visit_trail.py -v
uv run pytest
```
Expected: green.

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/loop.py task2/tests/unit/test_loop_visit_trail.py
git commit -m "feat(task2): add on_visit callback to ReactLoop

Fires once per turn at the top of the iteration with the current URL,
deduping consecutive identical URLs. Server will use this to feed a
visited-URL trail into the goto allowlist."
```

---

## Task 5b: Server wires the trail into `allowlist_sources` (FIFO cap 64)

**Files:**
- Modify: `task2/src/agent/server.py:73-90` (the `allowlist_sources` closure and the `ReactLoop(...)` construction)
- Add: an integration test in `task2/tests/unit/test_server_visit_trail.py` (or extend an existing server test if one already covers the closure)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_server_visit_trail.py`:

```python
"""The server's allowlist_sources closure must include URLs we have visited
this run, even after we navigate away from them, so subsequent goto() calls
back to a click-discovered URL are not blocked. Cap the trail at 64 (FIFO)."""

from __future__ import annotations

from collections import deque

from agent.tools.browser import _extract_urls, _is_goto_allowed


def test_visit_trail_dequeue_keeps_last_64(monkeypatch):
    trail: deque[str] = deque(maxlen=64)
    for i in range(100):
        trail.append(f"https://h{i}.test/")
    assert len(trail) == 64
    assert trail[0] == "https://h36.test/"
    assert trail[-1] == "https://h99.test/"


def test_allowlist_includes_visit_trail():
    """A URL in the trail unblocks a later goto to it even when no
    obs string contains the URL and the current page is elsewhere."""
    goal = "do a thing on https://anchor.test/"
    tape_obs: list[str] = ["clicked id=5", "read offset 0"]  # no URLs
    current_url = "https://elsewhere.test/"
    visited: list[str] = ["https://anchor.test/article/123"]

    sources = [goal, *tape_obs, current_url, *visited]
    allowlist: list[str] = []
    for s in sources:
        allowlist.extend(_extract_urls(s))

    assert _is_goto_allowed("https://anchor.test/article/123", allowlist) is True
    assert _is_goto_allowed("https://anchor.test/article/124", allowlist) is False
```

**Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_server_visit_trail.py -v
```

Expected: both pass already (they test free helpers and a stdlib `deque`). They are *contract documentation* — they pin the behavior the server must produce. They will only fail if a future change breaks `_is_goto_allowed` or `_extract_urls`. The real wiring assertion comes next.

If you'd rather have a hard failing-first test for this task, add this third case (which **will** fail until step 3 lands):

```python
import pytest


@pytest.mark.asyncio
async def test_server_threads_visit_trail_into_allowlist(monkeypatch):
    """Smoke: assemble a Config + a hand-built loop wiring identical to
    server.py's, drive on_visit twice with two URLs, and observe that
    allowlist_sources() returns them."""
    from collections import deque

    from agent.config import Config
    from agent.tools.browser import build_browser_tool_list

    visited: deque[str] = deque(maxlen=64)
    goal = "test"

    def allowlist_sources():
        return [goal] + list(visited)

    # Pretend the loop fired on_visit twice.
    visited.append("https://a.test/")
    visited.append("https://b.test/")

    sources = allowlist_sources()
    flat: list[str] = []
    from agent.tools.browser import _extract_urls

    for s in sources:
        flat.extend(_extract_urls(s))

    assert "https://a.test/" in flat
    assert "https://b.test/" in flat
    _ = Config  # silence unused
    _ = build_browser_tool_list  # silence unused
```

This is still a contract test — it doesn't exercise `server.py` end-to-end (that is what `tests/integration/test_server_ws.py` does). The actual wiring is verified by manual inspection in step 3 plus the existing integration test still passing.

**Step 3: Wire the trail into `server.py`**

In `task2/src/agent/server.py`, replace the `allowlist_sources` closure and the `ReactLoop(...)` construction (currently around lines 73-118):

```python
                from collections import deque

                visited_urls: deque[str] = deque(maxlen=64)

                def allowlist_sources():
                    if not loop_holder:
                        return [goal, *visited_urls]
                    return (
                        [goal]
                        + [step.get("obs", "") for step in loop_holder[0].tape]
                        + [browser.page.url or ""]
                        + list(visited_urls)
                    )

                for t in build_browser_tool_list(
                    browser,
                    restrict_goto=cfg.restrict_goto,
                    allowlist_sources=allowlist_sources,
                ):
                    reg.register(t)
                # ... (meta tools registration unchanged)

                # When constructing ReactLoop, add on_visit:
                loop = ReactLoop(
                    ...,
                    on_visit=visited_urls.append,
                )
                loop_holder.append(loop)
```

Keep all other args to `ReactLoop` exactly as they were.

**Step 4: Run the full suite**

```
uv run pytest
```

Expected: green. If `tests/integration/test_server_ws.py` fails because it doesn't mock the new `on_visit` argument, no fix needed at that callsite — `on_visit` is a kwarg on `ReactLoop`, and the server change is purely additive on the construction side.

**Step 5: Ruff and commit**

```
uv run ruff check . && uv run ruff format .
git add task2/src/agent/server.py task2/tests/unit/test_server_visit_trail.py
git commit -m "feat(task2): feed visited-URL trail into goto allowlist

Server now maintains a FIFO deque of URLs the browser actually reached
(via on_visit) and includes it in allowlist_sources, so a goto() back to
a click-discovered URL is not blocked even after we navigate away. Cap
64 entries to bound growth on long runs."
```

---

## Final verification

After Task 5b:

```
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git log --oneline -10
```

Expected: full suite green, ruff clean, five new commits on top of `07db9bf` matching the rollout order in the design doc.

## Out of scope (explicitly do not do in this plan)

- Per-tool nav hooks (we picked top-of-turn `on_visit`).
- Site-wide URL notes aggregation.
- Removing or making `K_RECENT` configurable.
- Any change to how the diff block is rendered (header line, +/- prefix).
- Any new permission for `goto` to *invent* URLs not in goal/notes/obs/trail.
