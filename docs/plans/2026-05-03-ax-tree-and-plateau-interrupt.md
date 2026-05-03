# AX-Tree Snapshot Enrichment + Plateau Interrupt Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Surface link URLs, widget state, and autocomplete options in `list_interactive` snapshots, and force the agent to call `reason` after 4 consecutive non-novel observations — so the WebVoyager agent can recover from the failure shapes seen in cases 104 and 107 of bench `webvoyager_20260503T024201Z`.

**Architecture:** Two parallel changes. (a) Extend `BrowserSession.snapshot()` (`src/agent/browser_session.py`) to emit additional fields per entry from the AX tree and DOM, and widen the role filter to include `option` when its listbox is `expanded`. (b) Extend `ReactLoop` (`src/agent/loop.py`) with a plateau-interrupt that triggers at `no_progress_streak == 4`, restricts that turn's tool set to `{reason, done, ask_user_question}`, and prepends a one-shot interrupt instruction to the system prompt.

**Tech Stack:** Python 3.11, Playwright (Chromium via CDP for AX tree), pytest-asyncio, ruff. Package manager `uv` — every command runs from `task2/` via `uv run …`.

**Design doc:** `docs/plans/2026-05-03-ax-tree-and-plateau-interrupt-design.md` (commit `f792d50`).

---

## Conventions (read once)

- All shell commands assume cwd `task2/`. Prepend `cd /home/pgi/v_coding_test2/task2 && ` if you've drifted.
- Lint must stay clean: `uv run ruff check .` after each task.
- Commit messages: conventional, scoped `feat(task2):` / `test(task2):` / `refactor(task2):`. Co-author tag per `CLAUDE.md` repo convention.
- Tests live in `task2/tests/{unit,integration}/`. Snapshot tests are integration (drive a real headless Chromium); loop tests are unit (mock LLM via `httpx.MockTransport`).
- The `_INTERACTIVE_ROLES` set lives at `src/agent/browser_session.py:15-26`.
- The plateau threshold constant goes next to `NO_PROGRESS_GIVEUP` at `src/agent/loop.py:49`.
- TDD discipline: write the failing test first, run it, see the expected failure mode, then write the smallest impl, then commit. No batching.

---

## Task 1: Snapshot — `href` on `link` entries

**Files:**
- Modify: `src/agent/browser_session.py:60-148`
- Test: `tests/integration/test_browser_tools_interact.py` (add new test at end of file)

**Step 1 — Write the failing test**

Append to `tests/integration/test_browser_tools_interact.py`:

```python
HTML_LINKS = """<!doctype html><html><body>
<a href="/foo">Foo</a>
<a href="https://example.com/bar">Bar</a>
<a>NoHref</a>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_link_exposes_href():
    """Link entries must expose `href` resolved against the page URL.
    Without this, the goto-grounding guard cannot allow a goto to a URL
    that only ever appears as an anchor target (case 107: HF autocomplete
    rendered model name as text but URL was never in any obs)."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_LINKS.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        links = {e["name"]: e for e in snap if e["role"] == "link"}
        assert links["Foo"]["href"].endswith("/foo"), links["Foo"]
        assert links["Bar"]["href"] == "https://example.com/bar", links["Bar"]
        # Anchor with no href must not crash and must not invent a value.
        assert "href" not in links["NoHref"], links["NoHref"]
    finally:
        await s.close()
```

**Step 2 — Run test to verify it fails**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py::test_snapshot_link_exposes_href -v
```

Expected: FAIL with `KeyError: 'href'` or `AssertionError` (snapshot has no `href` field).

**Step 3 — Implement**

In `src/agent/browser_session.py::snapshot()`, after the existing `entry: dict[str, Any] = {"id": eid, "role": role, "name": name}` line (around line 118), and before `value_obj = node.get("value")`, add:

```python
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
```

**Why `el.href` not `getAttribute('href')`:** the DOM `href` property returns the resolved absolute URL; `getAttribute` returns the literal attribute (could be relative or empty). Test asserts absolute resolution.

**Why `with contextlib.suppress(Exception)`:** the object may have been released between `resolveNode` and the `Runtime.callFunctionOn` call (snapshot already does the same pattern for the data-agent-eid stamp at line 102-116). Don't let one bad node blow up the whole snapshot.

**Caution:** the existing code calls `Runtime.releaseObject` in a `finally:` block at line 114-116. Your new `callFunctionOn` must run *before* that release (i.e. inside the same try block, before the `finally`). Read lines 95-122 carefully and place the `if role == "link":` block between the stamp call (~line 111) and the `finally:` (~line 114), inside the same try.

**Step 4 — Run test to verify it passes**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py::test_snapshot_link_exposes_href -v
```

Expected: PASS.

**Step 5 — Lint and commit**

```bash
uv run ruff check src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git add src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git commit -m "$(cat <<'EOF'
feat(task2): expose `href` on link entries in list_interactive snapshot

Lets the goto-grounding guard allow navigation to URLs the agent
observed as anchor targets — fixes the case-107 root cause where the
HF autocomplete row was readable as text but the URL never landed in
any obs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Snapshot — `placeholder` on textbox/searchbox

**Files:**
- Modify: `src/agent/browser_session.py` (same `snapshot()` method)
- Test: `tests/integration/test_browser_tools_interact.py` (append)

**Step 1 — Write the failing test**

```python
HTML_PLACEHOLDERS = """<!doctype html><html><body>
<input type=text placeholder="Search models, datasets, users…">
<input type=text>
<input type=search placeholder="ignored-by-search-role">
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_textbox_placeholder():
    """Textbox/searchbox entries must expose `placeholder` so the agent
    can pick the right input on the first try (HF homepage has 3+ text
    inputs; without placeholders the agent has to guess by id ordering)."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_PLACEHOLDERS.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        textboxes = [e for e in snap if e["role"] in ("textbox", "searchbox")]
        with_ph = [e for e in textboxes if "placeholder" in e]
        assert any(
            e["placeholder"] == "Search models, datasets, users…" for e in with_ph
        ), textboxes
        # An input with no placeholder attribute must not have the field.
        bare = [e for e in textboxes if "placeholder" not in e]
        assert bare, "expected at least one textbox without placeholder"
    finally:
        await s.close()
```

**Step 2 — Run, expect FAIL.**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py::test_snapshot_textbox_placeholder -v
```

**Step 3 — Implement**

In the same `snapshot()` block (after the `href` block from Task 1, still inside the same try, before the `finally:`), add:

```python
            if role in ("textbox", "searchbox"):
                with contextlib.suppress(Exception):
                    res = await cdp.send(
                        "Runtime.callFunctionOn",
                        {
                            "functionDeclaration": (
                                "function(){ return this.getAttribute('placeholder') || ''; }"
                            ),
                            "objectId": object_id,
                            "returnByValue": True,
                        },
                    )
                    raw = (res.get("result") or {}).get("value") or ""
                    if raw:
                        entry["placeholder"] = raw
```

**Step 4 — Run test, expect PASS.**

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git add src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git commit -m "feat(task2): expose textbox/searchbox placeholder in snapshot

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Snapshot — state booleans (`expanded`, `disabled`, `checked`, `selected`)

**Files:** same as above.

**Step 1 — Write the failing test**

```python
HTML_STATES = """<!doctype html><html><body>
<button aria-expanded="true">Menu</button>
<button disabled>Submit</button>
<input type=checkbox checked>
<input type=radio>
<select>
  <option selected>A</option>
  <option>B</option>
</select>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_state_booleans():
    """expanded/disabled/checked/selected must surface from the AX tree
    so the agent can tell that a menu is already open, a submit is
    greyed out, a box is already ticked, etc."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_STATES.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        by_name = {e["name"]: e for e in snap if e["name"]}
        assert by_name["Menu"].get("expanded") is True, by_name.get("Menu")
        assert by_name["Submit"].get("disabled") is True, by_name.get("Submit")
        cb = next(e for e in snap if e["role"] == "checkbox")
        assert cb.get("checked") is True, cb
        radio = next(e for e in snap if e["role"] == "radio")
        # Unchecked radio must NOT have checked: true. May omit the field
        # OR set it to false; pick one and stick with it for stability.
        assert not radio.get("checked"), radio
    finally:
        await s.close()
```

**Step 2 — Run, expect FAIL.**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py::test_snapshot_state_booleans -v
```

**Step 3 — Implement**

Reading the AX tree: each `Accessibility.getFullAXTree` node has an optional `properties` array with entries like `{"name": "expanded", "value": {"type": "boolean", "value": true}}`. Extract a small helper inside `snapshot()` (above the `for node in res.get("nodes", []):` loop, around line 87):

```python
        def _ax_prop(n: dict[str, Any], key: str) -> Any:
            for prop in n.get("properties", []) or []:
                if prop.get("name") == key:
                    return (prop.get("value") or {}).get("value")
            return None
```

Then, after building `entry` (after the placeholder block, still inside the try, before the `finally:`), add:

```python
            for prop_name in ("expanded", "disabled", "checked", "selected"):
                pv = _ax_prop(node, prop_name)
                if pv is True:
                    entry[prop_name] = True
                # Note: only emit `True`. Omit-when-false keeps the JSON tight
                # and matches the test's "not radio.get('checked')" expectation.
```

**Why omit-when-false:** every entry would gain 4 fields if we emitted `False` too, blowing up snapshot tokens for no signal. The agent can read `not entry.get("disabled")` for the same effect.

**Step 4 — Run test, expect PASS.**

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git add src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git commit -m "feat(task2): expose AX state booleans (expanded/disabled/checked/selected) in snapshot

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Snapshot — surface `option` entries when a listbox is `expanded`

**Files:** same as above.

**Step 1 — Write the failing test**

```python
HTML_AUTOCOMPLETE = """<!doctype html><html><body>
<div role="combobox" aria-expanded="true" aria-controls="lb">
  <input type="text" value="bert">
</div>
<ul id="lb" role="listbox">
  <li role="option" tabindex="0">google-bert/bert-base-uncased</li>
  <li role="option" tabindex="0">nlpaueb/legal-bert-base-uncased</li>
</ul>
<div role="listbox" aria-expanded="false">
  <div role="option">should-not-appear</div>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_listbox_options_appear_when_expanded():
    """Case-107 fix: when a listbox is expanded (autocomplete dropdown
    visible), its `option` children must be enumerated as clickable
    interactive entries. Collapsed listboxes' options stay hidden."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_AUTOCOMPLETE.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        options = [e for e in snap if e["role"] == "option"]
        names = {e["name"] for e in options}
        assert "google-bert/bert-base-uncased" in names, options
        assert "nlpaueb/legal-bert-base-uncased" in names, options
        assert "should-not-appear" not in names, options
    finally:
        await s.close()
```

**Step 2 — Run, expect FAIL.**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py::test_snapshot_listbox_options_appear_when_expanded -v
```

**Step 3 — Implement**

This one is subtler. The AX-tree traversal currently filters by `_INTERACTIVE_ROLES` early; `option` isn't in that set. Two changes:

(a) In `src/agent/browser_session.py`, change the early-skip from a hard filter to a conditional: keep `option` candidates *only* if their nearest listbox ancestor (by AX-tree parent chain) has `expanded=true`.

(b) The simplest correct approach: do **two passes** over `res["nodes"]`. First pass builds a map `node_id → node` and identifies expanded-listbox node ids. Second pass enumerates as today, but `option` is admitted iff one of its ancestors (walking via `parentId` if present, else by `childIds` reverse-index built in pass 1) is in the expanded-listbox set.

Concrete patch — replace the existing single-pass loop body. Before the existing loop, build:

```python
        nodes = res.get("nodes", []) or []
        by_id = {n.get("nodeId"): n for n in nodes if n.get("nodeId")}
        # Reverse map child→parent (CDP's AX nodes only carry childIds).
        parent_of: dict[str, str] = {}
        for n in nodes:
            pid = n.get("nodeId")
            for cid in n.get("childIds", []) or []:
                parent_of[cid] = pid
        expanded_listbox_ids: set[str] = set()
        for n in nodes:
            role_n = (n.get("role") or {}).get("value", "") or ""
            if role_n == "listbox" and _ax_prop(n, "expanded") is True:
                expanded_listbox_ids.add(n.get("nodeId"))
```

Then in the existing per-node loop, replace:

```python
                if role not in _INTERACTIVE_ROLES:
                    continue
```

with:

```python
                if role == "option":
                    cur = parent_of.get(node.get("nodeId"))
                    while cur is not None and cur not in expanded_listbox_ids:
                        cur = parent_of.get(cur)
                    if cur is None:
                        continue
                elif role not in _INTERACTIVE_ROLES:
                    continue
```

**Why ancestor-walk not direct-parent:** option is sometimes wrapped in a generic group inside the listbox (e.g. a `role=group` divider).

**Edge case to confirm with the test:** the second listbox in the HTML has `aria-expanded="false"` — its `option` child must NOT appear. The test asserts this.

**Step 4 — Run test, expect PASS. Also re-run all interact tests to confirm no regression on the existing 7 tests:**

```bash
uv run pytest tests/integration/test_browser_tools_interact.py -v
```

All 11 tests (7 existing + 4 new) must pass.

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git add src/agent/browser_session.py tests/integration/test_browser_tools_interact.py
git commit -m "feat(task2): admit option entries when their listbox is expanded

Closes the case-107 gap where HF's autocomplete results were rendered
as <li role=option> children of an expanded listbox but never made it
to list_interactive — the agent had no clickable id and looped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Loop — `PLATEAU_INTERRUPT` constant + pending flag plumbing

**Files:**
- Modify: `src/agent/loop.py:49` (constants block) and `src/agent/loop.py:121-138` (`__init__`)
- Test: `tests/unit/test_loop_plateau_interrupt.py` (new file)

**Step 1 — Write the failing test**

Create `tests/unit/test_loop_plateau_interrupt.py`:

```python
"""Plateau interrupt: at PLATEAU_INTERRUPT consecutive non-novel obs,
force the agent to call `reason` (or done/ask) before the
NO_PROGRESS_GIVEUP backstop fires at 9. Splits one stuck-spiral into
1 reflection step + at-most-5 recovery attempts."""

from agent.loop import NO_PROGRESS_GIVEUP, PLATEAU_INTERRUPT


def test_plateau_interrupt_threshold_below_giveup():
    """PLATEAU_INTERRUPT must fire strictly before NO_PROGRESS_GIVEUP,
    leaving room for the agent to recover after the forced reason."""
    assert PLATEAU_INTERRUPT < NO_PROGRESS_GIVEUP
    assert PLATEAU_INTERRUPT == 4
```

**Step 2 — Run, expect FAIL** (`ImportError: cannot import name 'PLATEAU_INTERRUPT'`):

```bash
uv run pytest tests/unit/test_loop_plateau_interrupt.py::test_plateau_interrupt_threshold_below_giveup -v
```

**Step 3 — Implement**

In `src/agent/loop.py`, after the existing `NO_PROGRESS_GIVEUP = 9` line (around line 49), add:

```python
# Force a `reason` step (or done/ask_user) when the agent's no-progress
# streak hits this threshold — strictly less than NO_PROGRESS_GIVEUP so
# the agent gets ~5 post-interrupt steps to recover before the hard
# backstop fires.
PLATEAU_INTERRUPT = 4
```

In `ReactLoop.__init__` (after `self._last_visit: str | None = None` at line 137), add:

```python
        self._plateau_interrupt_pending: bool = False
```

**Step 4 — Run test, expect PASS.**

**Step 5 — Commit**

```bash
uv run ruff check src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git add src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git commit -m "feat(task2): introduce PLATEAU_INTERRUPT constant + pending flag

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Loop — set `_plateau_interrupt_pending` when streak hits 4

**Files:** `src/agent/loop.py`, `tests/unit/test_loop_plateau_interrupt.py`

**Step 1 — Write the failing test**

Append to `tests/unit/test_loop_plateau_interrupt.py`:

```python
import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _Browser:
    class _Page:
        url = "https://a.test/"

        async def evaluate(self, *_a, **_k):  # noqa: D401 - test stub
            return ""

    page = _Page()


def _ok_response(name: str) -> httpx.Response:
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
                                "function": {"name": name, "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_plateau_interrupt_restricts_tools_at_threshold(tmp_path):
    """At streak == PLATEAU_INTERRUPT, the next LLM call's `tools`
    array must contain only {reason, done, ask_user_question}."""
    seen_tool_sets: list[set[str]] = []

    async def handler(request):
        body = json.loads(request.content)
        names = {t["function"]["name"] for t in body.get("tools", [])}
        seen_tool_sets.append(names)
        # Always call noopA — same obs each time, drives the streak up.
        return _ok_response("noopA")

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=20,
    )
    await loop.run("g")

    # The first 4 turns: full tool set offered. The 5th turn (after
    # streak reaches 4 from turns 1-4 of repeated "same" obs) must be
    # restricted to {reason, done, ask_user_question}.
    assert "noopA" in seen_tool_sets[0]
    restricted_turn = next(
        (i for i, s in enumerate(seen_tool_sets) if "noopA" not in s),
        None,
    )
    assert restricted_turn is not None, f"no restricted turn seen: {seen_tool_sets}"
    assert restricted_turn == 4, f"expected restriction at turn 4, got {restricted_turn}"
    assert seen_tool_sets[restricted_turn] == {"reason", "done", "ask_user_question"}
```

**Step 2 — Run, expect FAIL.**

```bash
uv run pytest tests/unit/test_loop_plateau_interrupt.py::test_plateau_interrupt_restricts_tools_at_threshold -v
```

Expected: assertion failure — restricted_turn is None.

**Step 3 — Implement**

In `src/agent/loop.py` `run()`, modify two spots:

(a) Where tools are computed for each turn (around line 274: `tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)`), wrap:

```python
            if self._plateau_interrupt_pending:
                allowed = {"reason", "done", "ask_user_question"}
                exclude = {n for n in self.registry.names() if n not in allowed}
                tools = self.registry.to_openai_tools_filtered(exclude=exclude)
            else:
                tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)
```

(b) After the streak-update block at the end of the per-step loop (around line 575, just after `self.no_progress_streak = 0` / `+=1` and the trace write at lines 576-587, before the `if self.no_progress_streak >= NO_PROGRESS_GIVEUP:` block), add:

```python
            if (
                name != "reason"
                and self.no_progress_streak == PLATEAU_INTERRUPT
                and not self._plateau_interrupt_pending
            ):
                self._plateau_interrupt_pending = True
```

**Note:** this only sets the flag. Clearing it on `reason` is Task 7. The test passes today because once the flag is set, the next turn's tools are restricted, the LLM is forced to pick from the allowed set; even though the mocked LLM keeps returning `noopA`, the flag mechanism still restricts the *tool list sent* — that's what the test asserts.

**Caveat for the test author:** the mock LLM will hit `ToolNameNotAllowed` because it picks `noopA` and `noopA` is not in `tools`. That triggers the existing `except ToolNameNotAllowed` handler at line 296, which appends a synthetic obs and continues. The test's assertion is on the *tools array sent*, not what the LLM returned, so this is fine. But ensure the test does not assert on result["status"] — that's path-dependent.

**Step 4 — Run test, expect PASS.**

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git add src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git commit -m "feat(task2): restrict tool set to reason/done/ask at plateau

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Loop — `reason` clears the pending flag and skips streak update

**Files:** `src/agent/loop.py`, `tests/unit/test_loop_plateau_interrupt.py`

**Step 1 — Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_reason_clears_pending_and_does_not_touch_streak(tmp_path):
    """After 4 stale obs the next turn is restricted; the LLM picks
    `reason`; the pending flag clears and the streak does not change.
    A subsequent stale obs should push streak to 5 (not 1, not reset)."""
    streak_at_each_check: list[int] = []
    pending_at_each_check: list[bool] = []
    actions_to_play = ["noopA", "noopA", "noopA", "noopA", "reason", "noopA", "noopA"]
    idx = {"i": 0}

    async def handler(request):
        # Snapshot loop state BEFORE the LLM picks an action.
        # Read counters from the loop instance via a side channel below.
        i = idx["i"]
        idx["i"] += 1
        return _ok_response(actions_to_play[i])

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=8,
    )
    await loop.run("g")

    # Tape after run: noopA, noopA, noopA, noopA, reason, noopA, noopA
    # Streaks expected: 0(novel), 1, 2, 3, 4 → triggers pending; reason
    # runs (does not touch streak); next noopA is non-novel → streak 5;
    # next noopA → streak 6.
    actions = [s["action"] for s in loop.tape]
    assert actions[:7] == ["noopA"] * 4 + ["reason", "noopA", "noopA"], actions
    # After reason at index 4, pending flag must be False again.
    # We can't read it post-hoc reliably without instrumentation, but
    # the streak after the final noopA tells us reason did NOT reset it
    # (would be 1 if it had).
    assert loop.no_progress_streak >= 5, loop.no_progress_streak
```

**Step 2 — Run, expect FAIL** (likely the streak resets on reason because "noted" is novel the first time, and then again on the next noopA's "same" because "same" is also already in earlier_fps — the assertion `>= 5` will catch the wrong arithmetic).

```bash
uv run pytest tests/unit/test_loop_plateau_interrupt.py::test_reason_clears_pending_and_does_not_touch_streak -v
```

**Step 3 — Implement**

Two changes in `src/agent/loop.py`:

(a) Where the streak is updated after the regular step (around lines 569-575):

```python
            obs_str = obs if isinstance(obs, str) else json.dumps(obs)
            new_fp = _obs_fingerprint(obs_str)
            earlier_fps = {_obs_fingerprint(s.get("obs", "")) for s in self.tape}
            self.tape.append({"thought": thought, "action": name, "args": args, "obs": obs_str})
            if name == "reason":
                self._plateau_interrupt_pending = False
            else:
                if new_fp in earlier_fps:
                    self.no_progress_streak += 1
                else:
                    self.no_progress_streak = 0
```

(b) Apply the same exemption in the `read`-exhausted branch (around lines 481-483) where streak is also updated. Wrap with `if name == "reason":` (which can never be true on the read path, but mirroring keeps the semantic uniform — actually skip this; that branch only runs for `read`, so it's fine to leave alone).

**Watch out for:** the `ToolNameNotAllowed` handler at line 314 also increments `self.no_progress_streak` — but that fires when the LLM picks an unknown tool, never when it picks `reason`, so it's fine to leave alone.

**Step 4 — Run test, expect PASS.**

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git add src/agent/loop.py tests/unit/test_loop_plateau_interrupt.py
git commit -m "feat(task2): reason clears plateau pending; doesn't touch streak

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Loop — inject one-shot interrupt instruction into system prompt

**Files:**
- Modify: `src/agent/context.py:49-118` (`build_messages`) — add an optional `plateau_interrupt: str | None` parameter
- Modify: `src/agent/loop.py` — pass the interrupt text when pending
- Test: `tests/unit/test_loop_plateau_interrupt.py` (append)

**Step 1 — Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_plateau_interrupt_message_appears_in_system_prompt(tmp_path):
    """When the plateau triggers, the system prompt sent to the LLM on
    that turn must include the one-shot interrupt instruction."""
    seen_systems: list[str] = []

    async def handler(request):
        body = json.loads(request.content)
        seen_systems.append(body["messages"][0]["content"])
        return _ok_response("noopA")

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=8,
    )
    await loop.run("g")

    # Turn at index 4 (after 4 stale obs) must carry the interrupt text.
    assert "no new information" in seen_systems[4].lower(), seen_systems[4]
    assert "reason" in seen_systems[4].lower()
    # Earlier turns must NOT carry it.
    for i in range(4):
        assert "no new information" not in seen_systems[i].lower(), (i, seen_systems[i])
```

**Step 2 — Run, expect FAIL.**

```bash
uv run pytest tests/unit/test_loop_plateau_interrupt.py::test_plateau_interrupt_message_appears_in_system_prompt -v
```

**Step 3 — Implement**

(a) In `src/agent/context.py::build_messages`, add a new keyword arg `plateau_interrupt: str | None = None`. Inside the function, after the `replan_hint` handling (around line 65), add:

```python
    if plateau_interrupt:
        sys_parts.append("")
        sys_parts.append(plateau_interrupt)
```

(b) In `src/agent/loop.py`, define the constant near `_REPLAN_HINT` (around line 38):

```python
_PLATEAU_INTERRUPT_HINT = (
    "INTERRUPT: You've taken several actions with no new information. "
    "Your next action MUST be `reason` (write what you've tried, what's "
    "blocking, and what to try next), or `done(failed, ...)` with the "
    "best partial answer, or `ask_user_question` if a human can break the tie."
)
```

(c) In `loop.run()`, where `build_messages` is called (around line 278), pass the new arg:

```python
            messages = build_messages(
                system=_SYSTEM,
                goal=goal,
                qa=list(self.qa),
                url_notes=url_notes,
                tape=self.tape,
                page_header=self._page_header(),
                replan_hint=replan_hint,
                page_diff=diff_block,
                wall_banner=wall_banner,
                reason_log=self.reason_log,
                plateau_interrupt=(
                    _PLATEAU_INTERRUPT_HINT if self._plateau_interrupt_pending else None
                ),
            )
```

**Step 4 — Run test, expect PASS.**

**Step 5 — Lint + commit**

```bash
uv run ruff check src/agent/loop.py src/agent/context.py tests/unit/test_loop_plateau_interrupt.py
git add src/agent/loop.py src/agent/context.py tests/unit/test_loop_plateau_interrupt.py
git commit -m "feat(task2): inject plateau interrupt hint into system prompt

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Regression — confirm existing stuck-detector + happy-path tests still pass

**Files:** none modified. Verification only.

**Step 1 — Run the full suite**

```bash
uv run pytest -x
```

Expected: ALL green. The most important pre-existing tests that could regress:

- `tests/unit/test_loop_no_progress.py::test_no_progress_streak_forces_done_failed` — stuck-detector at 9 must still terminate. The plateau interrupt at 4 should NOT short-circuit this; the test uses `noopA`/`noopB` (not `reason`), so the new `if name == "reason":` branch never runs and streak still hits 9.
- `tests/unit/test_loop_no_progress.py::test_replan_hint_fires_on_alternating_actions_with_stale_obs` — REPLAN should still fire at the existing trigger (no_progress at NOVELTY_WINDOW=8).
- `tests/unit/test_loop_no_progress.py::test_no_progress_constants_align_with_k_recent` — sanity check on constants. Plateau is independent (no constant changed).
- `tests/integration/test_browser_tools_interact.py` — all 7 existing snapshot tests must still pass (you re-ran these in Task 4 already, but confirm).

**Step 2 — If anything fails:** STOP. Read the failure carefully. Most likely causes:
- A new dict field on snapshot entries breaks `by_role = {(e["role"], e["name"]): e["id"] for e in snap}` if duplicates appear (unlikely but possible after admitting `option`s on the canirun.ai HTML — verify). Fix the test or guard the snapshot.
- The plateau-pending flag was set on the same turn it was checked, causing an off-by-one. Re-read Task 6 step 3 carefully.

**Step 3 — Lint pass over the changed surface**

```bash
uv run ruff check .
```

Expected: clean.

**Step 4 — No commit needed** (verification only). If you had to fix a regression, commit that as `fix(task2): …` separately with a clear "regression caught by Task 9" note.

---

## Task 10: Bench validation — re-run cases 104 and 107

**Files:** none modified.

**Pre-flight:** ensure the agent server is running. From the `bench-failure-triage` skill's prerequisites:

```bash
lsof -ti :8001 | xargs -r kill -9 2>/dev/null; sleep 1; true
```

Then start fresh in background (this is what the skill does — replicate it):

```bash
cd /home/pgi/v_coding_test2/task2 && set -a && . ./.env && set +a && AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001
```

(Run with `run_in_background: true`. Wait until `ss -ltn | grep :8001` returns something.)

**Step 1 — Run the two cases**

```bash
uv run python scripts/bench_webvoyager.py --ids 104,107
```

Expected wall-time: ~3-5 min total. The script writes `data/bench/webvoyager_<timestamp>.json`.

**Step 2 — Compare against baseline**

The baseline failures from `data/bench/webvoyager_20260503T024201Z.json`:
- 104: "The search results sorted by submission date (oldest first) only show papers from 1999–2009 …" — `failed`. Expected year is **2014**.
- 107: "stuck: no novel observation for 9 consecutive steps" — `failed`. Expected: a downloads number, even if "last month" rather than "total" (the system prompt explicitly authorizes the substitution + caveat).

Read the new bench JSON and report:
- Per case: did `status` flip to `success`?
- For case 107: did the trace touch the model card URL `huggingface.co/google-bert/bert-base-uncased`? (Read the matching trace via mtime as the bench-triage skill does.) If yes, the snapshot fix worked even if the answer is wrong.
- For case 104: did the agent call `reason` at any point? Did it switch to a Title-restricted search or add "Goodfellow" to the query? (Plateau interrupt should have given it a chance to.)

**Step 3 — Bar for shipping**

- **Bar:** at least one of {104, 107} flips to `success`.
- **Stretch:** both flip.
- **Floor:** if neither flips, examine the new traces and decide whether the design needs revision (a follow-up design doc) before claiming done. Do not ship a "tests pass but bench unchanged" outcome silently — the design's whole motivation is the bench.

**Step 4 — Document the result**

Replace `observations.md` at the repo root with a summary of what changed:

```markdown
# Bench validation — AX tree + plateau interrupt

**Baseline:** webvoyager_20260503T024201Z.json — 9/12 (104, 107, 111 failed)
**New run:** webvoyager_<new>.json — <new score>

## Case 104
- Old: failed, "…only show papers from 1999–2009…"
- New: <status>, "<answer excerpt>"
- Trace evidence: <key step or "no qualitative change">

## Case 107
- Old: failed, "stuck: no novel observation for 9 consecutive steps"
- New: <status>, "<answer excerpt>"
- Trace evidence: <e.g. "agent reached huggingface.co/google-bert/bert-base-uncased at step N">

## Verdict
<ship / revise / partial>
```

(`observations.md` is the bench-triage skill's working file — overwriting it is expected at completion of any agent change.)

**Step 5 — Final commit**

If the bar is met:

```bash
git add observations.md
git commit -m "docs(task2): bench validation results for AX tree + plateau interrupt

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

Then update `prompts/task2.md` only if the changes warrant a prompt-side note (probably not — the AX-tree fields are self-documenting via their presence in `list_interactive` JSON, and the plateau interrupt hint is injected at runtime). If you do edit the prompt, commit separately with `docs(task2): …`.

---

## Out of scope (for this plan)

These were explicitly deferred in the design:

- Per-URL action budget (the "mechanism #1" from the brainstorm).
- Stricter stuck-detector signal — `(url, action_type, target_role)` triple tracking.
- Cambridge Dictionary case 111 (Cloudflare; site-side).
- Refine-budget for arXiv-style query refinement loops (case 104) — re-evaluate after seeing whether AX-tree alone moves it.

If the bench validation in Task 10 shows that 104 still fails because the agent never picked the right field-restrict dropdown despite the new state booleans, open a follow-up design for the refine-budget. Do NOT bolt it on inside this plan.

---

## Acceptance checklist

- [ ] All 4 new snapshot tests pass.
- [ ] All 4 new loop tests pass.
- [ ] All pre-existing tests still pass (`uv run pytest -x`).
- [ ] `uv run ruff check .` is clean.
- [ ] Bench cases 104 and 107 re-run; at least one flips to `success`; result documented in `observations.md`.
- [ ] Each task committed separately (no batched commits, no `--amend`).
