# Live Interactive Section + Click Fast-Fail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the 30 s `Locator.evaluate` timeout failures that bled
~5 min across cases 101/102/105 in the latest bench, by (1) re-snapshotting
on every turn while `list_interactive` is in `tape[-3:]` so eids are always live,
(2) failing `click`/`type`/`select_option` in <1 s when an eid is no longer in
the DOM.

**Architecture:** `list_interactive` returns a short ack instead of the JSON
snapshot. Loop walks `tape[-3:]` before each `build_messages` call; if a
`list_interactive` step is present, it re-runs `session.snapshot(**args)` against
the live page and passes the fresh JSON to `build_messages` as a new
`interactive_elements` kwarg. `build_messages` appends a `## Interactive elements
(live)` section to the user message when that kwarg is set. Click and friends gain
an upfront `await loc.count() == 0` guard.

**Tech Stack:** Python 3.13, `uv`, Playwright (CDP for AX tree), pytest+pytest-asyncio,
DeepSeek `deepseek-chat` via OpenAI-compatible API.

**Reference:** see `docs/plans/2026-05-03-live-interactive-section-design.md`
for motivation, cost trade, and bench evidence.

**Branch:** continue on `task2-web-agent` (current HEAD `2871df7` is the design doc).

---

## Phase 1 — `build_messages` learns the new section

### Task 1: `build_messages` renders `## Interactive elements (live)` when given the kwarg

**Files:**
- Modify: `task2/src/agent/context.py` (function `build_messages`, currently lines 39–105)
- Test: `task2/tests/unit/test_live_interactive_section.py` (new)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_live_interactive_section.py`:

```python
"""build_messages appends a `## Interactive elements (live)` section to the
user message when given the `interactive_elements` kwarg."""
from agent.context import build_messages


def _user_text(msgs: list[dict]) -> str:
    return next(m["content"] for m in msgs if m["role"] == "user")


def test_no_section_when_kwarg_omitted():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
    )
    assert "## Interactive elements" not in _user_text(msgs)


def test_no_section_when_kwarg_none():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
        interactive_elements=None,
    )
    assert "## Interactive elements" not in _user_text(msgs)


def test_section_appended_when_kwarg_set():
    payload = '[{"id": 0, "role": "link", "name": "Home"}]'
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=[],
        page_header="HDR",
        replan_hint=None,
        interactive_elements=payload,
    )
    user = _user_text(msgs)
    assert "## Interactive elements (live)" in user
    assert payload in user


def test_section_after_recent_obs():
    """The live-elements section comes after the recent-obs block, so the
    most-recent context the LLM sees is the live element list."""
    tape = [
        {"action": "read", "args": {}, "obs": "old1", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "old2", "url": "", "reason": ""},
    ]
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="HDR",
        replan_hint=None,
        interactive_elements="[{\"id\":0}]",
    )
    user = _user_text(msgs)
    obs_idx = user.index("## Recent observations")
    elem_idx = user.index("## Interactive elements (live)")
    assert obs_idx < elem_idx
```

**Step 2: Run, see it fail**

Run: `cd task2 && uv run pytest tests/unit/test_live_interactive_section.py -v`
Expected: FAIL — `build_messages` doesn't accept `interactive_elements` kwarg yet.

**Step 3: Add the kwarg + render branch**

In `task2/src/agent/context.py`, modify `build_messages`:

1. Add new parameter at end of signature (after `reason_log`):
   ```python
   interactive_elements: str | None = None,
   ```
2. After the existing recent-obs block (after the closing `for obs in recent_obs: ... user_parts.append("---")`), append:
   ```python
   if interactive_elements:
       user_parts.append("")
       user_parts.append("## Interactive elements (live)")
       user_parts.append(interactive_elements)
   ```

**Step 4: Run, see it pass**

Run: `cd task2 && uv run pytest tests/unit/test_live_interactive_section.py -v`
Expected: 4 passed.

**Step 5: Run full suite + ruff**

```bash
cd task2 && uv run pytest -x && uv run ruff check .
```
Expected: 226+4 = 230 passed, ruff clean.

**Step 6: Don't commit yet** — Tasks 1-3 ship as one commit. Move on to Task 2.

---

### Task 2: `list_interactive` returns a short ack

**Files:**
- Modify: `task2/src/agent/tools/browser.py` (function `list_interactive`, lines 128–133)
- Test: `task2/tests/unit/test_list_interactive_ack.py` (new)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_list_interactive_ack.py`:

```python
"""list_interactive returns a short ack string, not the JSON snapshot.

The snapshot data flows to the LLM via build_messages's interactive_elements
section, re-rendered fresh each turn. The tool's return value is the obs that
lands in the tape — kept small so it doesn't bloat the recent-3-obs window."""
import pytest

from agent.tools.browser import build_browser_tools


class _FakeSession:
    def __init__(self, entries):
        self._entries = entries
        self.snapshot_calls: list[dict] = []

    async def snapshot(self, *, offset=0, limit=50):
        self.snapshot_calls.append({"offset": offset, "limit": limit})
        return self._entries

    @property
    def page(self):
        raise RuntimeError("not used in this test")


@pytest.mark.asyncio
async def test_list_interactive_returns_ack_not_json():
    fake = _FakeSession([{"id": 0, "role": "link", "name": "x"}] * 5)
    tools = build_browser_tools(fake, restrict_goto=False)
    obs = await tools["list_interactive"](offset=0, limit=50)
    # The obs must NOT be the JSON snapshot — that would defeat the purpose.
    assert not obs.startswith("[")
    # Must mention it took a snapshot and how many elements.
    assert "snapshot taken" in obs
    assert "5" in obs  # count
    # Must point the LLM at the live section.
    assert "Interactive elements" in obs


@pytest.mark.asyncio
async def test_list_interactive_still_calls_snapshot():
    """Eids must be tagged on the live DOM right now — same-turn click safety."""
    fake = _FakeSession([])
    tools = build_browser_tools(fake, restrict_goto=False)
    await tools["list_interactive"](offset=2, limit=10)
    assert fake.snapshot_calls == [{"offset": 2, "limit": 10}]
```

**Step 2: Run, see it fail**

Run: `cd task2 && uv run pytest tests/unit/test_list_interactive_ack.py -v`
Expected: FAIL on `test_list_interactive_returns_ack_not_json` — current
implementation returns `json.dumps(entries)` which starts with `[`.

**Step 3: Change `list_interactive` body**

In `task2/src/agent/tools/browser.py`, replace the `list_interactive` body
(lines 128–133):

```python
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
```

The `json` import in `browser.py` is still used by other paths (none currently —
but leave it; the loop wiring in Task 3 needs `json.dumps` to serialize the
fresh snapshot). If lint flags it as unused after Task 2 alone, leave it; Task 3
restores the use.

**Step 4: Run, see it pass**

Run: `cd task2 && uv run pytest tests/unit/test_list_interactive_ack.py -v`
Expected: 2 passed.

**Step 5: Don't commit yet.**

---

### Task 3: Loop re-snapshots and passes JSON to `build_messages`

**Files:**
- Modify: `task2/src/agent/loop.py` (around the `build_messages` call at lines 258–269)
- Test: extend `task2/tests/unit/test_live_interactive_section.py` with loop-level tests OR add `task2/tests/unit/test_loop_live_snapshot.py` (new). Pick the new file — keeps unit-of-test focused.

**Step 1: Write the failing tests**

Create `task2/tests/unit/test_loop_live_snapshot.py`:

```python
"""When list_interactive is in tape[-3:], the loop must call session.snapshot()
again at message-build time and pass the result to build_messages as
interactive_elements.

When list_interactive is older than 3 actions, no extra snapshot, no section."""
from __future__ import annotations

import json

import pytest

from agent.loop import _maybe_live_interactive_payload


class _RecordingSession:
    def __init__(self, entries):
        self._entries = entries
        self.snapshot_calls: list[dict] = []

    async def snapshot(self, *, offset=0, limit=50):
        self.snapshot_calls.append({"offset": offset, "limit": limit})
        return self._entries


@pytest.mark.asyncio
async def test_no_snapshot_when_list_interactive_absent_from_last_three():
    sess = _RecordingSession([])
    tape = [
        {"action": "list_interactive", "args": {"offset": 0, "limit": 50}, "obs": "ack", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "x", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "y", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "z", "url": "", "reason": ""},
    ]  # list_interactive is at index 0; last 3 are reads.
    payload = await _maybe_live_interactive_payload(sess, tape)
    assert payload is None
    assert sess.snapshot_calls == []


@pytest.mark.asyncio
async def test_snapshot_when_list_interactive_in_last_three():
    sess = _RecordingSession([{"id": 0, "role": "link", "name": "Home"}])
    tape = [
        {"action": "read", "args": {}, "obs": "x", "url": "", "reason": ""},
        {"action": "list_interactive", "args": {"offset": 0, "limit": 50}, "obs": "ack", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "y", "url": "", "reason": ""},
    ]
    payload = await _maybe_live_interactive_payload(sess, tape)
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed == [{"id": 0, "role": "link", "name": "Home"}]
    assert sess.snapshot_calls == [{"offset": 0, "limit": 50}]


@pytest.mark.asyncio
async def test_snapshot_uses_most_recent_list_interactive_args():
    sess = _RecordingSession([])
    tape = [
        {"action": "list_interactive", "args": {"offset": 0, "limit": 50}, "obs": "ack", "url": "", "reason": ""},
        {"action": "list_interactive", "args": {"offset": 50, "limit": 25}, "obs": "ack", "url": "", "reason": ""},
        {"action": "read", "args": {}, "obs": "z", "url": "", "reason": ""},
    ]
    await _maybe_live_interactive_payload(sess, tape)
    assert sess.snapshot_calls == [{"offset": 50, "limit": 25}]


@pytest.mark.asyncio
async def test_snapshot_failure_returns_none_not_raises():
    """If the live re-snapshot fails (e.g., page navigated mid-flight),
    return None and let the loop continue without the section, rather than
    crashing the whole turn."""
    class _Boom:
        async def snapshot(self, **_):
            raise RuntimeError("boom")
    tape = [
        {"action": "list_interactive", "args": {"offset": 0, "limit": 50}, "obs": "ack", "url": "", "reason": ""},
    ]
    payload = await _maybe_live_interactive_payload(_Boom(), tape)
    assert payload is None
```

**Step 2: Run, see them fail**

Run: `cd task2 && uv run pytest tests/unit/test_loop_live_snapshot.py -v`
Expected: FAIL — `_maybe_live_interactive_payload` doesn't exist.

**Step 3: Add the helper + wire it into loop**

In `task2/src/agent/loop.py`, near the top (after the imports, before
`_check_read_grep_grounding`), add:

```python
async def _maybe_live_interactive_payload(session, tape: list[dict]) -> str | None:
    """If `tape[-3:]` contains a `list_interactive` step, re-run the snapshot
    against the live page using the most-recent list_interactive's args, and
    return the JSON payload for the `## Interactive elements (live)` section.

    Returns None when list_interactive isn't recent, or when the live snapshot
    raises (the loop can still proceed without the section)."""
    import json
    last_li = None
    for step in tape[-3:]:
        if step.get("action") == "list_interactive":
            last_li = step
    if last_li is None:
        return None
    args = last_li.get("args") or {}
    offset = args.get("offset", 0)
    limit = args.get("limit", 50)
    try:
        entries = await session.snapshot(offset=offset, limit=limit)
    except Exception:
        return None
    return json.dumps(entries, ensure_ascii=False)
```

In the same file, find the `build_messages(...)` call at lines 258–269 and
modify it. Just before the call, compute the payload:

```python
            interactive_elements = await _maybe_live_interactive_payload(
                self.browser, self.tape
            )
            messages = build_messages(
                system=_SYSTEM,
                goal=goal,
                qa=list(self.qa),
                url_notes=url_notes,
                tape=self.tape,
                page_header=self._page_header(),
                replan_hint=None,
                page_diff=diff_block,
                wall_banner=wall_banner,
                reason_log=self.reason_log,
                interactive_elements=interactive_elements,
            )
```

The `self.browser` attribute is the `BrowserSession` (set in `__init__`). It has
the `snapshot()` method.

**Step 4: Run, see them pass**

Run: `cd task2 && uv run pytest tests/unit/test_loop_live_snapshot.py -v`
Expected: 4 passed.

**Step 5: Run full suite + ruff**

```bash
cd task2 && uv run pytest -x && uv run ruff check .
```
Expected: 226+4+2+4 = 236 passed, ruff clean. Move the `import json` from inside
the helper to the top of `loop.py` if ruff prefers it (likely already imported —
check; if so, drop the local import).

**Step 6: Commit Phase 1**

```bash
cd /home/pgi/v_coding_test2
git add task2/src/agent/context.py task2/src/agent/tools/browser.py task2/src/agent/loop.py task2/tests/unit/test_live_interactive_section.py task2/tests/unit/test_list_interactive_ack.py task2/tests/unit/test_loop_live_snapshot.py
git commit -m "feat(task2): list_interactive returns ack; live interactive section appended on demand"
```

DO NOT use `git add -A`. Pre-existing modifications (`.claude/scheduled_tasks.lock`,
`observations.md`, `prompts/task2.md`) must NOT be staged.

---

## Phase 2 — Click / type / select_option fast-fail

### Task 4: `click` fast-fails on stale eid

**Files:**
- Modify: `task2/src/agent/tools/browser.py` (function `click`, lines 135–154)
- Test: `task2/tests/unit/test_click_fast_fail.py` (new)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_click_fast_fail.py`:

```python
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
        # Simulate Playwright's 30s wait by sleeping.
        import asyncio
        await asyncio.sleep(31)
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
```

**Step 2: Run, see it fail**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`
Expected: `test_click_fast_fails_when_count_zero` either times out at 30s (the
fake's evaluate sleep) or passes if existing `except Exception` swallows. Either
way, the assertion `elapsed < 1.0` will fail because the current click goes
through three timeouts before reporting.

**Step 3: Modify `click`**

In `task2/src/agent/tools/browser.py`, replace the `click` body:

```python
    async def click(id: int) -> str:
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
            if tag == "SELECT":
                return (
                    f"ERROR: id={id} is a <select>; clicking it does not open a "
                    f"DOM-visible dropdown. Use select_option(id={id}, value=<one of "
                    "the entries from list_interactive's `options` field>) instead."
                )
            try:
                await loc.click(timeout=3000)
            except Exception:
                await loc.evaluate("el => el.click()", timeout=3000)
            return f"clicked id={id}"
        except Exception as e:
            return f"ERROR: {e}"
```

Both `evaluate(...)` calls now carry an explicit 3 s timeout — bounds the worst
case at ~10 s for actually-present-but-unclickable elements. Stale eid path
exits in milliseconds.

**Step 4: Run, see them pass**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`
Expected: 2 passed.

**Step 5: Don't commit yet** — Tasks 4 and 5 ship as one commit.

---

### Task 5: `type_` and `select_option` fast-fail similarly

**Files:**
- Modify: `task2/src/agent/tools/browser.py` (functions `type_` lines 156–164,
  `select_option` lines 166–172)
- Test: extend `task2/tests/unit/test_click_fast_fail.py`

`press_key` does NOT need a fast-fail — it calls `session.page.keyboard.press(key)`
without dereferencing an eid. Skip it.

**Step 1: Add failing tests**

Append to `task2/tests/unit/test_click_fast_fail.py`:

```python
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
```

**Step 2: Run, see them fail**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`
Expected: the new fast-fail tests fail (elapsed > 1s).

**Step 3: Modify `type_` and `select_option`**

In `task2/src/agent/tools/browser.py`:

```python
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

    async def select_option(id: int, value: str) -> str:
        try:
            loc = session.locator(id)
            if await loc.count() == 0:
                return (
                    f"ERROR: id={id} no longer in DOM. Call list_interactive "
                    "to refresh — eids are reassigned each snapshot."
                )
            await loc.select_option(value, timeout=5_000)
            return f"selected {value!r} on id={id}"
        except Exception as e:
            return f"ERROR: {e}"
```

**Step 4: Run, see them pass**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`
Expected: 6 passed.

**Step 5: Run full suite + ruff**

```bash
cd task2 && uv run pytest -x && uv run ruff check .
```
Expected: 236 + 6 = 242 passed, ruff clean.

**Step 6: Commit Phase 2**

```bash
cd /home/pgi/v_coding_test2
git add task2/src/agent/tools/browser.py task2/tests/unit/test_click_fast_fail.py
git commit -m "feat(task2): click/type/select_option fast-fail on stale eid"
```

---

## Phase 3 — Bench validation

### Task 6: Re-run the 12-case bench

**Step 1: Restart the agent server with the new code**

```bash
lsof -ti :8001 | xargs -r kill -9 2>/dev/null
sleep 1
cd /home/pgi/v_coding_test2/task2
set -a && . ./.env && set +a
AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory \
  --host 127.0.0.1 --port 8001 &
until ss -ltn | grep -q ':8001'; do sleep 2; done
echo "ready"
```

**Step 2: Run the full bench**

```bash
cd task2 && uv run python scripts/bench_webvoyager.py --limit 12
```

Expected wall: 25–40 min (down from 47 min on the prior run because cases
101/102/105 should no longer eat 30s timeouts).

**Step 3: Confirm bar met**

- ≥ 9/12 successes (no regression).
- Cases 101, 102, 105 wall-time drops noticeably (no 30s `Locator.evaluate`
  timeouts in their traces). Verify with:
  ```bash
  for sid in $(jq -r '.results[] | .sid // empty' data/bench/webvoyager_*Z.json | tail -12); do
    n=$(grep -c "Locator.evaluate" "data/traces/${sid}.jsonl" 2>/dev/null || echo 0)
    echo "$sid: $n stale-eid timeouts"
  done
  ```
  Expected: all sids show 0.

If a previously-passing case now fails, inspect the trace; if the cause is
genuine (not flakey network), file as a P0 in `observations.md` and decide
whether to fix here or punt. New failures of cases that were already failing
(107, 111, 112) are not regressions.

**Step 4: Update `observations.md`**

Replace contents (use the Write tool, not Edit). Pattern after prior:

```markdown
# Observations: Bench validation for live interactive section + click fast-fail PR

**Run:** webvoyager_<timestamp>.json
**Plan:** docs/plans/2026-05-03-live-interactive-section.md
**Result:** N/12 — list per-case status delta vs prior run

## Per-case wall-time delta (the targets)

| Case | Web | Prior | Now | Stale-eid timeouts (prior → now) |
|------|-----|-------|-----|----------------------------------|
| 101 | Wikipedia | 465s | <X>s | 3 → 0 |
| 102 | Wikipedia | 235s | <X>s | 6 → 0 |
| 105 | GitHub | 61s | <X>s | 2 → 0 |

## Findings

[List any P0/P1 findings — verbatim trace evidence]
```

**Step 5: No commit** — `observations.md` stays uncommitted per the
bench-failure-triage discipline.

---

## Done criteria

- Phase 1 and Phase 2 commits land on `task2-web-agent`.
- `uv run pytest -x` green (~242 tests).
- `uv run ruff check .` clean.
- 12-case bench: ≥ 9/12 successes, zero `Locator.evaluate` timeouts in any trace.
- `observations.md` documents the wall-time delta on cases 101, 102, 105.

## Rollback

If Phase 1 introduces flakiness or breaks integration tests that hit a real
browser:

```bash
cd /home/pgi/v_coding_test2
git revert <phase-1-sha>
```

Phase 2 is mechanically simple — unlikely to need rollback, but the same revert
mechanism applies.
