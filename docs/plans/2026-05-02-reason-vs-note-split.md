# Reason vs. Note Split — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the conflated `note(text)` tool into `reason()` (agent-callable in-session scratchpad) and a system-authored post-`done()` distillation that writes goal-agnostic page-knowledge to `NotesStore`. Replaces the existing per-turn `Summarizer` writes.

**Architecture:** Three layers. (1) `reason` is a new meta tool whose payloads accumulate on a `ReactLoop.reason_log: list[str]` and render in the prompt under `Reasoning so far:`. (2) A new `agent/distill.py` runs one LLM call after `LoopDone`, takes `(goal, url, status, answer, tape, reason_log, prior_note)` and emits a unified replacement page-fact list — the LLM does the merge. (3) The agent-callable `note` tool is removed; the existing `Summarizer` (which writes mid-session on goto/error/failed) is removed in the same pass since the distiller subsumes it.

**Tech Stack:** Python 3.11, `pytest` + `pytest-asyncio`, `httpx.MockTransport`, `uv`. Existing modules: `agent/llm.py`, `agent/notes_store.py`, `agent/tools/meta.py`, `agent/loop.py`, `agent/context.py`. Linter: `ruff`.

**Companion design:** `docs/plans/2026-05-02-reason-vs-note-split-design.md`.

---

## Pre-flight (one-time)

Before Task 1, verify the baseline is green:

```bash
cd /home/pgi/v_coding_test2/task2
uv run pytest
uv run ruff check .
```

Expected: 149 passed, ruff clean. If anything is red, stop and fix first — do not start the plan on a red baseline.

This plan is best executed on the existing `task2-web-agent` branch. No new worktree needed; commits land directly.

---

## Task 1 — `NotesStore.set()` for full-row replacement

The distiller produces a unified replacement, not an append. `NotesStore.append()` joins to existing content; we need a `set()` that overwrites.

**Files:**
- Modify: `task2/src/agent/notes_store.py`
- Test: `task2/tests/unit/test_notes_store.py`

**Step 1 — Write the failing test**

Append this to `task2/tests/unit/test_notes_store.py`:

```python
def test_set_overwrites_existing(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.append("https://a.test/", "old line")
    s.set("https://a.test/", "fresh line")
    assert s.get("https://a.test/") == "fresh line"


def test_set_caps_at_2kb(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.set("https://a.test/", "x" * 5000)
    assert len(s.get("https://a.test/")) <= 2048


def test_set_strips_query_string_by_default(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.set("https://a.test/x?token=1", "fact")
    assert s.get("https://a.test/x?token=2") == "fact"
```

**Step 2 — Run, see RED**

```bash
cd /home/pgi/v_coding_test2/task2
uv run pytest tests/unit/test_notes_store.py -v
```

Expected: 3 new tests fail with `AttributeError: 'NotesStore' object has no attribute 'set'`.

**Step 3 — Implement `set()`**

In `task2/src/agent/notes_store.py`, add this method to `NotesStore` (just below `append`):

```python
def set(self, url: str, text: str) -> None:
    key = _normalise(url, self._query_strip)
    trimmed = text
    while len(trimmed.encode()) > _MAX_BYTES and "\n" in trimmed:
        trimmed = trimmed.split("\n", 1)[1]
    if len(trimmed.encode()) > _MAX_BYTES:
        trimmed = trimmed.encode()[:_MAX_BYTES].decode("utf-8", "ignore")
    self._conn.execute(
        "INSERT INTO url_notes(url, notes, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(url) DO UPDATE SET notes=excluded.notes, updated_at=CURRENT_TIMESTAMP",
        (key, trimmed),
    )
    self._conn.commit()
```

**Step 4 — Run, see GREEN**

```bash
uv run pytest tests/unit/test_notes_store.py -v
uv run ruff check task2/src/agent/notes_store.py
```

Expected: all 7 tests pass, ruff clean.

**Step 5 — Commit**

```bash
git add task2/src/agent/notes_store.py task2/tests/unit/test_notes_store.py
git commit -m "feat(task2): NotesStore.set() for full-row replacement"
```

---

## Task 2 — `distill_page_knowledge` module (no integration yet)

Standalone module; no loop wiring. Validates the distiller in isolation before threading it through.

**Files:**
- Create: `task2/src/agent/distill.py`
- Create: `task2/tests/unit/test_distill.py`

**Step 1 — Write the failing tests**

Create `task2/tests/unit/test_distill.py`:

```python
import json

import httpx
import pytest

from agent.distill import distill_page_knowledge
from agent.llm import LLMClient
from agent.notes_store import NotesStore


def _llm_with_response(content: str) -> LLMClient:
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    return LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )


def _llm_that_fails() -> LLMClient:
    async def handler(request):
        return httpx.Response(500, text="boom")

    return LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )


class _RecordingTrace:
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_distill_replaces_url_row_after_success(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "stale page-fact from prior run")
    llm = _llm_with_response(
        "- GPU dropdown is at id=94\n- Selecting a GPU updates the score column"
    )
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="best LLM for an RTX 3090",
        status="success",
        answer="Llama 3.1 8B",
        tape=[{"action": "select_option", "args": {"id": 94}, "obs": "selected"}],
        reason_log=["GPU dropdown id=94"],
        trace=trace,
    )
    out = notes.get("https://a.test/")
    assert "GPU dropdown is at id=94" in out
    assert "stale page-fact from prior run" not in out


@pytest.mark.asyncio
async def test_distill_runs_on_failed_status(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    llm = _llm_with_response("- Cloudflare challenge wall on every navigation")
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="anything",
        status="failed",
        answer="blocked by Cloudflare",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    assert "Cloudflare" in notes.get("https://a.test/")


@pytest.mark.asyncio
async def test_distill_failure_emits_trace_event_and_leaves_row(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "prior-row-content")
    llm = _llm_that_fails()
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=trace,
    )
    assert notes.get("https://a.test/") == "prior-row-content"
    assert any(e["type"] == "distill_failed" for e in trace.events)


@pytest.mark.asyncio
async def test_distill_short_circuits_when_notes_is_none():
    llm = _llm_that_fails()  # would raise if invoked
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=None,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=trace,
    )
    assert trace.events == []


@pytest.mark.asyncio
async def test_distill_caps_oversize_output(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    huge = "\n".join(["- " + "x" * 200] * 100)
    llm = _llm_with_response(huge)
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    assert len(notes.get("https://a.test/")) <= 2048


@pytest.mark.asyncio
async def test_distill_passes_prior_note_into_prompt(tmp_path):
    """The merge happens LLM-side, so the prior row must reach the
    prompt verbatim."""
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "prior-fact-XYZ")
    captured = {}

    async def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "- new fact"}}]}
        )

    llm = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    body_text = json.dumps(captured["body"])
    assert "prior-fact-XYZ" in body_text
```

**Step 2 — Run, see RED**

```bash
uv run pytest tests/unit/test_distill.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent.distill'`.

**Step 3 — Implement the module**

Create `task2/src/agent/distill.py`:

```python
from __future__ import annotations

from typing import Any, Protocol

from agent.llm import LLMClient
from agent.notes_store import NotesStore


class _TraceLike(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


_PROMPT = (
    "You are extracting durable PAGE-FACTS from a single browsing session, "
    "to help any FUTURE agent reach ANY goal on this URL.\n\n"
    "Output ≤10 short bullets, one per line, prefixed by '- '. Each bullet "
    "must describe the PAGE: selectors and element ids, hidden requirements, "
    "dead-end actions, walls (Cloudflare/CAPTCHA/login), where data lives, "
    "what filters or dropdowns exist. Reject anything goal-specific (the "
    "value the user asked for in this run, intermediate analyses of that "
    "value). If a prior note is supplied, fold it in: deduplicate, prefer "
    "the more specific phrasing, drop facts contradicted by this session.\n\n"
    "No prose, no headers, no preamble. Just the bullets."
)


async def distill_page_knowledge(
    *,
    llm: LLMClient,
    notes: NotesStore | None,
    url: str,
    goal: str,
    status: str,
    answer: str,
    tape: list[dict[str, Any]],
    reason_log: list[str],
    trace: _TraceLike | None,
) -> None:
    """Fire-and-forget post-`done()` distillation. Writes goal-agnostic
    page-facts to `notes` for `url`, replacing the prior row. Any failure
    is swallowed and emitted as a `distill_failed` trace event so the
    user-visible result is unaffected."""
    if notes is None or not url:
        return
    try:
        prior = notes.get(url) or "(none)"
        user = (
            f"URL: {url}\n"
            f"Goal: {goal}\n"
            f"Status: {status}\n"
            f"Final answer: {answer}\n\n"
            f"Prior page-note for this URL:\n{prior}\n\n"
            f"In-session reasoning the agent kept:\n"
            + ("\n".join(f"- {r}" for r in reason_log) or "(none)")
            + "\n\nFull tape:\n"
            + "\n".join(
                f"- {s.get('action')}({s.get('args')}) → "
                f"{(s.get('obs') or '')[:200]!r}"
                for s in tape
            )
        )
        msg, _ = await llm.chat(
            [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": user},
            ],
            reasoning=False,
        )
        text = (msg.get("content") or "").strip()
        if not text:
            return
        notes.set(url, text)
    except Exception as e:
        if trace is not None:
            trace.write(
                {"type": "distill_failed", "payload": {"error": str(e)[:200]}}
            )
```

**Step 4 — Run, see GREEN**

```bash
uv run pytest tests/unit/test_distill.py -v
uv run ruff check task2/src/agent/distill.py
```

Expected: all 6 tests pass, ruff clean.

**Step 5 — Commit**

```bash
git add task2/src/agent/distill.py task2/tests/unit/test_distill.py
git commit -m "feat(task2): distill_page_knowledge for post-done page-fact extraction"
```

---

## Task 3 — `reason` tool added to meta tools (parallel to existing `note`)

We add `reason` first without removing `note`, so the codebase stays green between commits.

**Files:**
- Modify: `task2/src/agent/tools/meta.py`
- Modify: `task2/tests/unit/test_meta_tools.py`

**Step 1 — Write the failing test**

Append to `task2/tests/unit/test_meta_tools.py`:

```python
@pytest.mark.asyncio
async def test_reason_appends_to_session_log():
    log: list[str] = []
    tools = build_meta_tools(
        notes=None,
        current_url=lambda: "x",
        question_channel=QuestionChannel(),
        reason_log=log,
    )
    out = await tools["reason"](text="GPU dropdown id=94")
    assert out == "noted"
    assert log == ["GPU dropdown id=94"]


@pytest.mark.asyncio
async def test_reason_does_not_touch_notes_store(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    log: list[str] = []
    tools = build_meta_tools(
        notes=notes,
        current_url=lambda: "https://a.test/",
        question_channel=QuestionChannel(),
        reason_log=log,
    )
    await tools["reason"](text="ephemeral")
    assert notes.get("https://a.test/") == ""
```

**Step 2 — Run, see RED**

```bash
uv run pytest tests/unit/test_meta_tools.py -v
```

Expected: 2 new tests fail (`build_meta_tools` doesn't accept `reason_log` kwarg).

**Step 3 — Implement**

In `task2/src/agent/tools/meta.py`:

a) Add `reason_log` parameter to `build_meta_tools`:

```python
def build_meta_tools(
    *,
    notes: NotesStore | None,
    current_url: Callable[[], str],
    question_channel: QuestionChannel,
    reason_log: list[str] | None = None,
) -> dict:
```

b) Inside `build_meta_tools`, add `reason`:

```python
async def reason(text: str) -> str:
    if reason_log is not None:
        reason_log.append(text)
    return "noted"
```

c) Include `"reason": reason` in the returned dict.

d) Mirror the change in `build_meta_tool_list` — accept `reason_log: list[str] | None = None` kwarg, pass it through to `build_meta_tools`, and add a `Tool` entry for `reason`:

```python
Tool(
    "reason",
    "Record a short thought you want to remember past the rolling action "
    "window. In-session only — does not persist.",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "thought": {"type": "string"},
        },
        "required": ["text"],
    },
    fns["reason"],
),
```

Place the `Tool` entry *before* the existing `note` entry (so `reason` is reachable via the registry while we still have `note` around).

**Step 4 — Run, see GREEN**

```bash
uv run pytest tests/unit/test_meta_tools.py -v
uv run ruff check task2/src/agent/tools/meta.py
```

Expected: 2 new tests pass; existing `test_note_appends`, `test_done_raises_with_payload`, `test_ask_user_question_blocks_until_answered` still pass.

**Step 5 — Commit**

```bash
git add task2/src/agent/tools/meta.py task2/tests/unit/test_meta_tools.py
git commit -m "feat(task2): add reason() tool — in-session scratchpad"
```

---

## Task 4 — Wire `reason_log` into ReactLoop and prompt rendering

`reason` is registered as a tool, but nothing owns the log yet. This task threads it through.

**Files:**
- Modify: `task2/src/agent/loop.py`
- Modify: `task2/src/agent/context.py`
- Modify: `task2/src/agent/server.py` (factory site, if it constructs tools directly)
- Test: `task2/tests/unit/test_context.py` (extend or create)

**Step 1 — Write the failing test for context rendering**

Find or create `task2/tests/unit/test_context.py`. Add:

```python
from agent.context import build_messages


def test_reason_log_renders_under_dedicated_block():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
        reason_log=["a", "b"],
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Reasoning so far:" in user["content"]
    assert "- a" in user["content"]
    assert "- b" in user["content"]


def test_reason_log_block_says_none_when_empty():
    msgs = build_messages(
        system="sys",
        goal="g",
        qa=[],
        url_notes="(none)",
        tape=[],
        page_header="URL=https://x",
        replan_hint=None,
        reason_log=[],
    )
    user = next(m for m in msgs if m["role"] == "user")
    assert "Reasoning so far:\n(none)" in user["content"]
```

**Step 2 — Run, see RED**

```bash
uv run pytest tests/unit/test_context.py -v
```

Expected: 2 new tests fail (`build_messages` rejects `reason_log` kwarg).

**Step 3 — Implement context rendering**

In `task2/src/agent/context.py`, change the signature of `build_messages` to accept an optional `reason_log`:

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
    wall_banner: str | None = None,
    reason_log: list[str] | None = None,
) -> list[dict]:
```

Right after the existing `URL notes:` block (lines 71-73), before `page_header`, insert:

```python
user_parts.append("")
user_parts.append("Reasoning so far:")
if reason_log:
    rendered = "\n".join(f"- {r}" for r in reason_log)
    # FIFO trim: keep newest, drop oldest, until ≤4 KB.
    while len(rendered.encode()) > 4096 and "\n" in rendered:
        rendered = rendered.split("\n", 1)[1]
    user_parts.append(rendered)
else:
    user_parts.append("(none)")
```

**Step 4 — Run context tests, see GREEN**

```bash
uv run pytest tests/unit/test_context.py -v
```

Expected: both new tests pass; pre-existing tests in this file (if any) still pass.

**Step 5 — Wire reason_log on ReactLoop**

In `task2/src/agent/loop.py`:

a) In `__init__`, after `self._wall_kind = None` (around line 132), add:

```python
self.reason_log: list[str] = []
```

b) Find every `build_messages(...)` call site (there are several — at minimum lines ~180, 250, 290, 335, 470, 565). For each call, add the kwarg:

```python
reason_log=self.reason_log,
```

c) Find the site that builds the meta tool list — it's wherever `build_meta_tool_list` is called. This is most likely in `agent/server.py` (the app factory) or in the `ReactLoop`'s tool wiring. Search:

```bash
grep -rn "build_meta_tool_list\|build_meta_tools" task2/src
```

For each site, add `reason_log=<the loop's reason_log>` to the kwargs. If the call site does not have access to the `ReactLoop`, route it via constructor injection — the tool list and `ReactLoop` need to share the same `list` instance.

**Step 6 — Write a loop-level test that the agent's `reason` calls land on the log**

Append to `task2/tests/unit/test_loop_anti_repeat.py` (or create `test_loop_reason.py` if you prefer file-per-feature):

```python
@pytest.mark.asyncio
async def test_loop_reason_call_lands_on_reason_log(tmp_path):
    """Agent calls reason("X"); next-turn build_messages should include
    'X' in the Reasoning so far block."""
    # Build a loop with a fake LLM that on call 1 emits reason("hello"),
    # on call 2 emits done(success). Use the same harness pattern as
    # test_loop_recovers_from_hallucinated_tool_name. After run completes,
    # assert loop.reason_log == ["hello"].
    # (See test_loop_recovers_from_hallucinated_tool_name for the exact
    # mock-LLM scaffolding to copy.)
```

(Note: this test reuses the existing `test_loop_anti_repeat.py` harness — copy the mock-LLM transport pattern from `test_loop_recovers_from_hallucinated_tool_name`. The new test only needs to assert post-run `loop.reason_log` content.)

**Step 7 — Run full suite, see GREEN**

```bash
uv run pytest
uv run ruff check .
```

Expected: all tests pass, ruff clean.

**Step 8 — Commit**

```bash
git add task2/src/agent/loop.py task2/src/agent/context.py task2/src/agent/server.py task2/tests/unit/test_context.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): wire reason_log through ReactLoop and prompt context"
```

---

## Task 5 — Replace `Summarizer` with the distiller at `LoopDone`

This is the consequential surgery. The existing `Summarizer` fires mid-session on `goto`/`error`/`failed`-done. The design says page-knowledge writes happen **only after `done()`**, so we remove the per-turn triggers and call the distiller from the `LoopDone` handler.

**Files:**
- Modify: `task2/src/agent/loop.py`
- Delete: `task2/src/agent/summarizer.py`
- Delete: `task2/tests/unit/test_summarizer.py`
- Delete: `task2/tests/unit/test_loop_summarizer_resilient.py`
- Delete: `task2/tests/unit/test_loop_summarizer_trigger.py`
- Test: `task2/tests/unit/test_loop_distill.py` (new)

**Step 1 — Write the failing test for the distiller call at LoopDone**

Create `task2/tests/unit/test_loop_distill.py`:

```python
"""ReactLoop must call distill_page_knowledge after every done(...),
success or failed, before returning the result."""

import pytest

# Use the same fake-LLM + fake-browser harness pattern as
# test_loop_recovers_from_hallucinated_tool_name. The new asserts:
#   1. After a run that ends in done(success), distill ran once with
#      status="success" and the URL row was overwritten.
#   2. After a run that ends in done(failed), distill ran once with
#      status="failed" and the URL row was overwritten.
#   3. If the distiller LLM raises, the user-visible result still
#      reflects the agent's done() call exactly, and a distill_failed
#      trace event was written.
#
# Implement these three tests by copying the harness from
# test_loop_anti_repeat.py and customizing the mock LLM.

# Skeleton — fill in using the existing harness pattern:

@pytest.mark.asyncio
async def test_distill_runs_after_done_success(tmp_path):
    ...

@pytest.mark.asyncio
async def test_distill_runs_after_done_failed(tmp_path):
    ...

@pytest.mark.asyncio
async def test_distill_failure_does_not_corrupt_result(tmp_path):
    ...
```

(Implementation note: the harness in `test_loop_anti_repeat.py::test_loop_recovers_from_hallucinated_tool_name` shows how to wire a mock `httpx.MockTransport` LLM, a fake browser, a `NotesStore` on `tmp_path`, and a `ReactLoop`. Reuse it. The done-call mock is a queue of responses — first the agent emits `done(...)`, second the distiller emits the bullet content.)

**Step 2 — Run, see RED**

```bash
uv run pytest tests/unit/test_loop_distill.py -v
```

Expected: 3 tests fail or skip (depending on how you fill the skeletons).

**Step 3 — Wire the distiller into `LoopDone`**

In `task2/src/agent/loop.py`:

a) Add to imports:

```python
from agent.distill import distill_page_knowledge
```

b) Locate the `except LoopDone as d:` block (around line 509). Immediately before the `return result` at the end of that block (after the trace write of the final `done` event), insert:

```python
url = self._current_url()
await distill_page_knowledge(
    llm=self.llm,
    notes=self.notes,
    url=url,
    goal=goal,
    status=d.status,
    answer=d.answer,
    tape=self.tape,
    reason_log=self.reason_log,
    trace=self.trace,
)
```

c) **Remove** the existing summarizer call inside the `LoopDone` handler (the `if d.status == "failed" and self.summarizer is not None and url:` block — currently lines 522-529).

d) **Remove** the per-turn summarizer block (currently lines 590-610) entirely.

e) **Remove** `summarizer` from the `ReactLoop.__init__` signature and `self.summarizer` attribute.

f) Update every call site that constructs `ReactLoop` to drop the `summarizer=` kwarg. Search:

```bash
grep -rn "ReactLoop\b" task2
```

g) **Delete** the source files:

```bash
rm task2/src/agent/summarizer.py
rm task2/tests/unit/test_summarizer.py
rm task2/tests/unit/test_loop_summarizer_resilient.py
rm task2/tests/unit/test_loop_summarizer_trigger.py
```

**Step 4 — Run full suite, see GREEN**

```bash
uv run pytest
uv run ruff check .
```

Expected: all tests pass (the deleted summarizer tests are gone; the new distill tests pass), ruff clean.

If there are import errors from `summarizer` somewhere else in the tree, grep them and update — most likely one or two imports in `agent/server.py`.

**Step 5 — Commit**

```bash
git add -A task2/src task2/tests
git commit -m "feat(task2): replace Summarizer with post-done distill_page_knowledge"
```

---

## Task 6 — Remove the agent-callable `note` tool

`reason` is in place and used; the distiller is the only writer to `NotesStore`. Now we remove the agent-callable `note` tool entirely.

**Files:**
- Modify: `task2/src/agent/tools/meta.py`
- Modify: `task2/tests/unit/test_meta_tools.py`

**Step 1 — Write the failing test**

In `task2/tests/unit/test_meta_tools.py`:

a) **Remove** the existing `test_note_appends` test.

b) Add a regression test that `note` is no longer in the registry:

```python
@pytest.mark.asyncio
async def test_note_tool_is_not_in_registry():
    tools = build_meta_tools(
        notes=None,
        current_url=lambda: "x",
        question_channel=QuestionChannel(),
        reason_log=[],
    )
    assert "note" not in tools
    assert "reason" in tools
```

**Step 2 — Run, see RED**

```bash
uv run pytest tests/unit/test_meta_tools.py -v
```

Expected: `test_note_tool_is_not_in_registry` fails because `note` is still in `tools`.

**Step 3 — Remove `note` from `build_meta_tools` and `build_meta_tool_list`**

In `task2/src/agent/tools/meta.py`:

- Delete the `async def note(...)` function and the `"note": note` entry in `build_meta_tools`'s return.
- Delete the `Tool("note", ...)` entry in `build_meta_tool_list`.

**Step 4 — Run, see GREEN**

```bash
uv run pytest
uv run ruff check .
```

Expected: all pass. If anything else in the codebase still references `agent.tools.meta.note`, update it. Most likely none, since the tool was only used through the registry.

**Step 5 — Commit**

```bash
git add task2/src/agent/tools/meta.py task2/tests/unit/test_meta_tools.py
git commit -m "feat(task2): remove agent-callable note() tool"
```

---

## Task 7 — Migration: wipe `notes.sqlite` and update `prompts/task2.md`

Final cleanup. The existing SQLite rows are polluted with task-state from the agent-as-author era; carrying them forward via the distiller's `prior_note` input would propagate the pollution. Wipe and start fresh.

**Files:**
- Delete: `task2/data/notes.sqlite`
- Modify: `prompts/task2.md`

**Step 1 — Wipe the SQLite**

```bash
rm -f task2/data/notes.sqlite
```

(Confirm there's no test fixture pointing at this file — `grep -r "data/notes.sqlite" task2/tests` should return nothing. Tests use `tmp_path`.)

**Step 2 — Update `prompts/task2.md`**

Open `prompts/task2.md`. Find any agent-facing instruction about `note` and replace with the new framing. Typical edits:

- Replace "Use `note` to record what you learned for future visits to this URL" → "Use `reason` to record short thoughts you want to keep past the rolling action window. In-session only. Page-knowledge for future visits is captured automatically when you commit `done()`; you do not need to write notes by hand."
- Add a single line near the tool descriptions: "After `done()` fires, the system extracts page-facts from this session into the URL's note row automatically. Don't try to do that yourself."

**Step 3 — Run full suite + ruff**

```bash
uv run pytest
uv run ruff check .
```

Expected: still all green.

**Step 4 — Smoke-test on a real bench case**

Restart the agent server (per `bench-failure-triage` skill prereqs):

```bash
lsof -ti :8001 | xargs -r kill -9 2>/dev/null; sleep 1
cd /home/pgi/v_coding_test2/task2 && set -a && . ./.env && set +a && \
  AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory \
  --factory --host 127.0.0.1 --port 8001 &
until ss -ltn 2>/dev/null | grep -q ':8001'; do sleep 2; done
```

Run case 113 once:

```bash
uv run python scripts/bench_webvoyager.py --ids 113
```

Expected: the run completes (success or failed). After it finishes, inspect `task2/data/notes.sqlite`:

```bash
uv run python -c "
from agent.notes_store import NotesStore
n = NotesStore('task2/data/notes.sqlite')
print(n.get('https://www.canirun.ai/'))
"
```

Expected: bullet lines describing page-facts (selectors, dropdown ids, walls). NOT task-state ("models that fit within 24 GB", "RTX 3090 specifically").

If the row contains task-state, the distillation prompt needs tightening — go back to Task 2 and revise `_PROMPT` in `agent/distill.py`.

**Step 5 — Commit**

```bash
git add prompts/task2.md
# notes.sqlite is gitignored; the rm doesn't show in git status
git commit -m "docs(task2): update prompt for reason/note split + wipe stale notes.sqlite"
```

---

## Done-criteria checklist

Before declaring the plan complete, verify all of these:

- [ ] `uv run pytest` is green from the repo root with ≥149 passing tests.
- [ ] `uv run ruff check .` clean.
- [ ] `agent.tools.meta` exposes `reason` but NOT `note`.
- [ ] `agent.distill.distill_page_knowledge` exists and is the only writer to `NotesStore` outside of tests.
- [ ] `agent.summarizer` no longer exists; no test references it.
- [ ] One real bench case (any) writes a goal-agnostic page-fact list to `notes.sqlite` after `done()`.
- [ ] `prompts/task2.md` mentions `reason` and the auto-distill behavior, not `note`.

---

## Risks and known sharp edges

- **Distiller LLM cost**: one extra `chat` call per session, ~5% of session token cost. Acceptable; if it grows we can sample (e.g., distill on 1-of-3 sessions).
- **Distiller hallucination**: the prompt forbids goal-specific content but the model can still leak the goal's value into the bullets. Mitigation: post-distillation, optionally strip lines containing the literal final-answer string. Not implemented in this plan; revisit if smoke-test (Task 7 step 4) shows the leak.
- **`reason_log` instance-sharing**: the meta tool builder and the loop must hold the *same* `list` object. If a constructor seam copies the list, `reason()` will mutate a copy and the loop will inject an empty block. Task 4 step 5 calls this out — verify by running the loop-level test in Task 4 step 6 before moving on.
- **Per-turn token cost of `Reasoning so far:`**: every turn injects up to 4 KB of reason lines. If a session writes many `reason()` calls, this competes with the tape budget. The 4 KB cap with FIFO eviction is the only knob; if it bites we can lower it.
