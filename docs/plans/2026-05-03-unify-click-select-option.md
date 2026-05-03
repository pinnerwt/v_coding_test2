# Unify `click` and `select_option` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collapse `click` and `select_option` into a single LLM-visible tool `click(id, value=None)` that dispatches by element tag at runtime, eliminating the eid-type-confusion failure mode that case 104 exposed on bench `webvoyager_20260503T084302Z.json`.

**Architecture:** Inside `task2/src/agent/tools/browser.py`, the existing `click` function gains an optional `value: str | None` parameter. After the existing `count() == 0` guard and the existing `tagName` probe, the function dispatches: `tag == "SELECT"` → `loc.select_option(value)`; otherwise → `loc.click()`. Two type-mismatch guards preserve the type-confusion detection that the separate-tool design got for free: SELECT-without-value → ask for a value; non-SELECT-with-value → flag stale eid (the case-104 prevention path). `select_option` is removed from both the tool-fn dict (`build_browser_tools`) and the LLM tool list (`build_browser_tool_list`). All tests that called `tools["select_option"](...)` are rewritten to `tools["click"](id=..., value=...)`. `type_` is untouched.

**Tech Stack:** Python 3.12, Playwright (Chromium), pytest + pytest-asyncio, `uv` for env management, `ruff` for lint/format.

**Design doc:** `docs/plans/2026-05-03-unify-click-select-option-design.md`

---

## Pre-flight

- Branch: `task2-unify-click-select` (already created off `dev`).
- Working directory for all `uv` / `pytest` commands: `task2/` (each task in this repo has its own `uv.lock`).
- Run `cd task2 && uv sync` once at the start if the env is stale.
- All commits use conventional style with the `task2` scope, e.g. `feat(task2): …`, `test(task2): …`, `refactor(task2): …`.

---

## Task 1: Update `test_click_fast_fail.py` unit tests for the new `click(id, value=...)` shape (red bar)

We start by rewriting the unit tests to express the new contract. They will fail against today's code, which is the expected red.

**Files:**
- Modify: `task2/tests/unit/test_click_fast_fail.py`

**Step 1: Update the `_LiveLocator.evaluate` to be tag-parameterizable**

Today it hardcodes `"DIV"` for any `tagName` query (line 59). For the new tests we need to assert behavior on both SELECT and non-SELECT tags. Replace the class with:

```python
class _LiveLocator:
    """Locator whose count() returns 1 and whose actions succeed.
    `tag` is what `el => el.tagName` will return."""

    def __init__(self, tag: str = "DIV"):
        self.tag = tag
        self.click_calls = 0
        self.fill_calls = 0
        self.select_calls = 0

    async def count(self) -> int:
        return 1

    async def evaluate(self, expr, *_a, **_k):
        if "tagName" in expr:
            return self.tag
        return None

    async def click(self, **_k):
        self.click_calls += 1

    async def fill(self, *_a, **_k):
        self.fill_calls += 1

    async def press(self, *_a, **_k):
        pass

    async def select_option(self, value, *_a, **_k):
        self.select_calls += 1
        self.last_value = value
```

**Step 2: Replace the two `select_option` tests with new `click(id, value=...)` tests**

Delete `test_select_option_fast_fails_when_count_zero` and `test_select_option_proceeds_when_count_positive` (lines 130–148) and append:

```python
@pytest.mark.asyncio
async def test_click_with_value_fast_fails_when_count_zero():
    """The count() == 0 fast-fail must run before the tag probe, so a stale
    eid surfaces the standard "no longer in DOM" error in well under 1s
    even when the LLM passed a value."""
    sess = _Sess({3: _DeadLocator()})
    tools = build_browser_tools(sess, restrict_goto=False)
    t0 = time.monotonic()
    obs = await tools["click"](id=3, value="x")
    elapsed = time.monotonic() - t0
    assert "no longer in DOM" in obs
    assert "list_interactive" in obs
    assert elapsed < 1.0, f"click took {elapsed:.2f}s — must fast-fail"


@pytest.mark.asyncio
async def test_click_on_select_with_value_dispatches_to_select_option():
    loc = _LiveLocator(tag="SELECT")
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["click"](id=3, value="Title")
    assert "selected 'Title' on id=3" in obs
    assert loc.select_calls == 1
    assert loc.last_value == "Title"
    assert loc.click_calls == 0


@pytest.mark.asyncio
async def test_click_on_select_without_value_returns_actionable_error():
    """LLM must be told to pass `value` from the live section's `options`."""
    loc = _LiveLocator(tag="SELECT")
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["click"](id=3)
    assert obs.startswith("ERROR:")
    assert "id=3" in obs
    assert "<select>" in obs
    assert "value" in obs
    assert "options" in obs
    assert loc.select_calls == 0
    assert loc.click_calls == 0


@pytest.mark.asyncio
async def test_click_on_non_select_with_value_flags_stale_eid():
    """Case-104 prevention: LLM still 'remembers' eid as a <select> after
    a snapshot rotation re-bound it to <a>. Tool must surface a stale-eid
    ERROR with `Call list_interactive`, not silently click."""
    loc = _LiveLocator(tag="A")
    sess = _Sess({3: loc})
    tools = build_browser_tools(sess, restrict_goto=False)
    obs = await tools["click"](id=3, value="Title")
    assert obs.startswith("ERROR:")
    assert "id=3" in obs
    assert "not a <select>" in obs
    assert "list_interactive" in obs
    assert loc.click_calls == 0
    assert loc.select_calls == 0
```

Also: the existing `test_click_proceeds_when_count_positive` will keep passing as-is (DIV, no value). Keep it.

**Step 3: Run the file and verify the new tests fail**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`

Expected:
- `test_click_with_value_fast_fails_when_count_zero` → **FAIL** (today's `click` doesn't accept `value`)
- `test_click_on_select_with_value_dispatches_to_select_option` → **FAIL** (same)
- `test_click_on_select_without_value_returns_actionable_error` → may pass-by-accident on today's "is a <select>" redirect message — verify wording. If it incidentally passes, that's fine; the contract is what matters.
- `test_click_on_non_select_with_value_flags_stale_eid` → **FAIL** (no such guard today)

**Step 4: Commit the red bar**

```bash
git add task2/tests/unit/test_click_fast_fail.py
git commit -m "test(task2): rewrite click fast-fail tests for unified click(id, value=...)"
```

---

## Task 2: Implement the unified `click(id, value=None)` in `browser.py` (make Task 1 green)

**Files:**
- Modify: `task2/src/agent/tools/browser.py:138-190` (replace `click` body and remove `select_option`)

**Step 1: Replace the `click` function body**

Open `task2/src/agent/tools/browser.py`. Replace the existing `click` (lines ~138–162) with:

```python
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
```

**Step 2: Delete the standalone `select_option` function (lines ~179–190)**

Delete the entire `async def select_option(...)` block. Its select-handling logic now lives inside `click`.

**Step 3: Drop `select_option` from the `build_browser_tools` return dict (lines ~199–209)**

Remove the line `"select_option": select_option,`.

**Step 4: Run the unit tests — should now pass**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py -v`

Expected: all tests in the file pass (5 originally + 3 new = 8 passing).

**Step 5: Commit**

```bash
git add task2/src/agent/tools/browser.py
git commit -m "feat(task2): unify click and select_option into click(id, value=None)"
```

---

## Task 3: Update the LLM tool list — drop `select_option`, extend `click` schema

**Files:**
- Modify: `task2/src/agent/tools/browser.py:286-298, 314-327` (the `Tool` entries inside `build_browser_tool_list`)

**Step 1: Add a unit test for the tool registry**

Append to `task2/tests/unit/test_click_fast_fail.py`:

```python
@pytest.mark.asyncio
async def test_select_option_not_in_tool_registry():
    """LLM tool surface should expose only `click`; no `select_option`."""
    from agent.tools.browser import build_browser_tool_list, build_browser_tools

    sess = _Sess({})
    fns = build_browser_tools(sess, restrict_goto=False)
    assert "select_option" not in fns

    tool_list = build_browser_tool_list(sess, restrict_goto=False)
    names = [t.name for t in tool_list]
    assert "select_option" not in names
    assert "click" in names

    click_tool = next(t for t in tool_list if t.name == "click")
    props = click_tool.parameters["properties"]
    assert "value" in props
    assert props["value"]["type"] == "string"
    # value must NOT be required — it's only used for <select>
    assert "value" not in click_tool.parameters.get("required", [])
```

**Step 2: Run it — expect FAIL**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py::test_select_option_not_in_tool_registry -v`

Expected: FAIL — `select_option` is still in the tool list, and `click`'s schema lacks `value`.

**Step 3: Update the `click` `Tool(...)` entry (around line 286–298)**

Replace with:

```python
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
```

**Step 4: Delete the `select_option` `Tool(...)` entry (around line 314–327)**

Remove the entire `Tool("select_option", ...)` block, including the trailing comma.

**Step 5: Run the test — expect PASS**

Run: `cd task2 && uv run pytest tests/unit/test_click_fast_fail.py::test_select_option_not_in_tool_registry -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add task2/src/agent/tools/browser.py task2/tests/unit/test_click_fast_fail.py
git commit -m "feat(task2): drop select_option from LLM tool list; extend click schema with value"
```

---

## Task 4: Port integration tests in `test_browser_tools_interact.py`

These tests exercise the *real* Playwright surface (not mocks), so they're the strongest guarantee that the dispatch works end-to-end.

**Files:**
- Modify: `task2/tests/integration/test_browser_tools_interact.py`

**Step 1: Run the integration tests to see what fails today**

Run: `cd task2 && uv run pytest tests/integration/test_browser_tools_interact.py -v`

Expected: a handful of tests reference `tools["select_option"]` — those will now KeyError because the key was removed in Task 2. That's the red bar.

**Step 2: Update `test_click_type_select_press` (line 60)**

Replace:
```python
        await tools["select_option"](id=s_id, value="b")
```
With:
```python
        await tools["click"](id=s_id, value="b")
```

**Step 3: Update `test_click_on_select_short_circuits` (lines 87–106)**

The test asserts that `click(id_of_select)` (no value) returns an ERROR mentioning `select_option`. With the new design, the message no longer references `select_option` — it tells the LLM to pass `value`. Rewrite the assertions:

```python
@pytest.mark.asyncio
async def test_click_on_select_without_value_short_circuits():
    """Click on a real <select> with no value must produce an actionable
    error telling the LLM to pass `value` instead of looping. Mirrors the
    previous select_option-redirect behavior under the unified tool."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        out = await tools["click"](id=sid)
        assert out.startswith("ERROR:")
        assert "<select>" in out
        assert "value" in out
        assert "options" in out
        assert f"id={sid}" in out
    finally:
        await s.close()
```

(Rename the test function for clarity.)

**Step 4: Update `test_each_id_resolves_to_distinct_dom_node` (line 190)**

Replace:
```python
            out = await tools["select_option"](id=c["id"], value=f"opt-{i}-B")
```
With:
```python
            out = await tools["click"](id=c["id"], value=f"opt-{i}-B")
```

**Step 5: Update `test_select_option_failure_fast` (lines 201–220)**

Rename the test and its docstring to reflect the unified tool. Replace:

```python
@pytest.mark.asyncio
async def test_click_on_select_with_bad_value_fails_fast():
    """Default Playwright timeout is 30s — three failures = 90s of dead
    time in a 50-step budget. Cap to ~5s so the loop recovers fast."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        t0 = time.monotonic()
        out = await tools["click"](id=sid, value="NotAnOption")
        elapsed = time.monotonic() - t0
        assert out.startswith("ERROR:")
        assert elapsed < 10.0, f"click took {elapsed:.1f}s; expected <10s"
    finally:
        await s.close()
```

**Step 6: Add a new integration test for the case-104 prevention path**

Append:

```python
HTML_SELECT_AND_LINK = """<!doctype html><html><body>
<select id=field><option>Title</option><option>Author</option></select>
<a id=ln href="/next">Next</a>
</body></html>"""


@pytest.mark.asyncio
async def test_click_with_value_on_link_flags_stale_eid():
    """Case-104 prevention: if the LLM thinks an eid is a <select> but it
    has been re-bound to <a> across snapshots, pass `value` and the tool
    must surface a stale-eid ERROR rather than silently clicking the link."""
    s = BrowserSession()
    await s.start()
    try:
        url = (
            "data:text/html;base64,"
            + base64.b64encode(HTML_SELECT_AND_LINK.encode()).decode()
        )
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        link_id = next(e["id"] for e in snap if e["role"] == "link")
        out = await tools["click"](id=link_id, value="Title")
        assert out.startswith("ERROR:")
        assert "not a <select>" in out
        assert "list_interactive" in out
        # Page must NOT have navigated.
        assert s.page.url.startswith("data:text/html"), s.page.url
    finally:
        await s.close()
```

**Step 7: Run the integration suite — expect PASS**

Run: `cd task2 && uv run pytest tests/integration/test_browser_tools_interact.py -v`

Expected: all tests pass (renamed and new ones included).

**Step 8: Commit**

```bash
git add task2/tests/integration/test_browser_tools_interact.py
git commit -m "test(task2): port browser-interact integration tests to unified click(value=...)"
```

---

## Task 5: Update incidental `select_option` references

**Files:**
- Modify: `task2/src/agent/browser_session.py:212` (comment)
- Modify: `task2/tests/unit/test_distill.py:59` (fixture)

**Step 1: Fix the stale comment in `browser_session.py`**

Open `task2/src/agent/browser_session.py`. Replace lines around 210–213:
```python
        # For each combobox that resolves to a real <select>, surface its
        # <option> text values inline. Without this the model has to guess
        # what `value` to pass to select_option and Playwright blocks for
        # 30s on a non-match (canirun.ai bench case 113).
```
With:
```python
        # For each combobox that resolves to a real <select>, surface its
        # <option> text values inline. Without this the model has to guess
        # what `value` to pass to click() — Playwright blocks for ~5s on
        # a non-match (canirun.ai bench case 113).
```

**Step 2: Fix the test fixture in `test_distill.py`**

Open `task2/tests/unit/test_distill.py`, line ~59. Replace:
```python
        tape=[{"action": "select_option", "args": {"id": 94}, "obs": "selected"}],
```
With:
```python
        tape=[{"action": "click", "args": {"id": 94, "value": "RTX 3090"}, "obs": "selected"}],
```

**Step 3: Run the test to confirm it still passes**

Run: `cd task2 && uv run pytest tests/unit/test_distill.py -v`

Expected: PASS. The distill logic asserts narrative output ("GPU dropdown is at id=94"), which depends on `id=94` and the `obs` text — both unchanged. The action-name swap is for accuracy, not assertion-driven.

**Step 4: Verify `test_server_visit_trail.py` does not need changes**

Run: `cd task2 && uv run pytest tests/unit/test_server_visit_trail.py -v`

Expected: PASS. The `select_option` mock at line 208 is on a fake Playwright Locator (the *Playwright* surface), which the merged `click` still calls internally for SELECT elements. No change needed.

**Step 5: Commit**

```bash
git add task2/src/agent/browser_session.py task2/tests/unit/test_distill.py
git commit -m "refactor(task2): retire select_option references in comments and fixtures"
```

---

## Task 6: Confirm no `select_option` mentions remain outside legitimate places

**Step 1: Grep**

Run: `cd /home/pgi/v_coding_test2 && grep -rn "select_option" task2/ prompts/ docs/ --include="*.py" --include="*.md" 2>/dev/null | grep -v ".venv\|__pycache__\|uv.lock\|docs/plans/2026-05-03-unify-click-select-option"`

Expected: only legitimate references remain:
- `task2/src/agent/tools/browser.py` — call to `loc.select_option(...)` inside the unified `click` (Playwright API, not the LLM tool name).
- `task2/tests/unit/test_click_fast_fail.py` — `_DeadLocator.select_option` and `_LiveLocator.select_option` are Playwright-locator mocks; they stay.
- `task2/tests/unit/test_server_visit_trail.py:208` — same kind of mock; stays.

If anything else turns up (system prompts, docstrings, README sections), update it now in this same task.

**Step 2: Commit if anything was updated**

```bash
git add -p
git commit -m "docs(task2): purge stale select_option references"
```

(Skip the commit if nothing needed updating.)

---

## Task 7: Full test suite + lint pass

**Step 1: Run the full test suite**

Run: `cd task2 && uv run pytest -v`

Expected: all tests pass. If a test fails, fix the immediate cause (do not weaken assertions to make it green).

**Step 2: Run ruff**

Run: `cd task2 && uv run ruff check . && uv run ruff format --check .`

Expected: clean. If format complaints, run `uv run ruff format .` and re-stage.

**Step 3: If ruff modified any files, commit the formatting**

```bash
git add -u
git commit -m "style(task2): ruff format after click/select_option unification"
```

(Skip if nothing changed.)

---

## Task 8: Bench validation

The bar is **no regression on the count** (≥ 9/12), with case 104's failure shape (`select_option` mispick on stale eid) absent from any new trace.

**Step 1: Start the agent server**

Run (in a separate terminal or background): `cd task2 && uv run python -m server`

Wait until it logs that it's listening on `127.0.0.1:8001`.

**Step 2: Run the WebVoyager bench**

Run: `cd task2 && uv run python -m bench.webvoyager`

This writes a fresh `task2/data/bench/webvoyager_<timestamp>.json` file and per-case JSONL traces.

**Step 3: Compare against `webvoyager_20260503T084302Z.json`**

Compute pass count and per-case status delta. The bar:
- Pass count: ≥ 9/12.
- Case 104: ideally `success` (LLM no longer mispicks because `select_option` doesn't exist as a tool); a different failure shape is acceptable but case 104 must not fail with the same shape (LLM emitting `select_option`).

Verify shape absence: `grep -c "select_option" task2/data/traces/*.jsonl` against the new traces — expect 0 matches in any tool-call action name (you may see `loc.select_option` or similar Playwright-side strings, but never `"action":"select_option"` or `"name":"select_option"`).

**Step 4: Write a short observations entry**

Open `observations.md` and append a new section (do not overwrite the existing one — it documents the prior PR):

```markdown
# Observations: Bench validation for unify-click-select PR

**Run:** `task2/data/bench/webvoyager_<NEW_TIMESTAMP>.json`
**Plan:** `docs/plans/2026-05-03-unify-click-select-option.md`
**Result:** [N]/12

[Per-case delta table]

[Findings — especially for case 104 and any new shapes]
```

**Step 5: Commit observations**

```bash
git add observations.md
git commit -m "docs(task2): bench observations for unify-click-select PR"
```

---

## Task 9: Final review and merge prep

**Step 1: Diff against `dev`**

Run: `git log dev..HEAD --oneline; git diff dev...HEAD --stat`

Expected: ~5–7 commits (test red, impl, tool list, integration tests, incidental refs, optional ruff, observations). Stat: small — `browser.py` is the main file; tests are the bulk of the diff.

**Step 2: Run the full test suite + ruff once more**

Run: `cd task2 && uv run pytest && uv run ruff check . && uv run ruff format --check .`

Expected: green.

**Step 3: Hand off**

The branch is ready for review/merge. Do **not** auto-merge to `dev` — surface the diff, the bench result, and the observations.md entry to the user, and let them decide on the merge.

---

## Acceptance checklist (final)

- [ ] All unit tests in `task2/tests/unit/test_click_fast_fail.py` pass, including 4 new `click(id, value=...)` tests.
- [ ] Tool-registry test asserts `select_option` is gone from both `build_browser_tools` dict and `build_browser_tool_list` names.
- [ ] All integration tests in `task2/tests/integration/test_browser_tools_interact.py` pass, including the new case-104-prevention test.
- [ ] `task2/src/agent/tools/browser.py` no longer defines a top-level `select_option` function.
- [ ] `cd task2 && uv run pytest` is green end-to-end.
- [ ] `cd task2 && uv run ruff check .` is clean.
- [ ] Bench pass rate ≥ 9/12. No new trace contains `"action":"select_option"` or `"name":"select_option"`.
- [ ] `observations.md` has a fresh section for this PR.

## Rollback path

If bench regresses (< 9/12) or surfaces a new failure mode worse than the trade, revert the merged-`click` commit:

```bash
git revert <hash-of-feat-task2-unify-commit>
```

…and re-run bench to confirm restoration. The plan is intentionally additive on the test side and surgically scoped on the implementation side, so a single-commit revert restores the prior contract.
