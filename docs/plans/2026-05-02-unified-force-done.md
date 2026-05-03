# Unified Force-Done Pathway Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace three placeholder-string force-done sites in `ReactLoop` with one helper that makes a constrained final LLM call to commit a real answer, and treat empty `list_interactive` results as exhausted-on-arrival.

**Architecture:** New module `agent/force_done.py` exporting `coerce_done_via_llm(...)`. The helper appends a trigger-specific instruction line and a chronological dump of every prior `read()` obs to the existing message stack, then calls the LLM with `tools=[done_only]` and named `tool_choice` so the model is mechanically constrained to emit `done(...)`. `ReactLoop` calls this helper from three sites (max_steps boundary, NO_PROGRESS_GIVEUP, asked-state-giveup) instead of returning hard-coded placeholder strings. Independent of all that, the `list_interactive` auto-advance branch short-circuits when the served slice is `"[]"`.

**Tech Stack:** Python 3.11+, `uv` for env, `ruff` for lint+format, `pytest` + `pytest-asyncio` + `httpx.MockTransport` for tests. OpenAI-compatible LLM client (DeepSeek backend in production).

---

## Conventions for every task

- Run all `pytest`/`ruff` commands from `task2/` (the Python project root): `cd task2 && uv run pytest …`.
- TDD ordering is non-negotiable: write the failing test, see it fail for the *expected* reason, write the minimal code to make it pass, see it pass, commit.
- Commit messages use conventional style with `task2` scope: `feat(task2): …`, `test(task2): …`, `refactor(task2): …`. Each commit ends with the `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` trailer per repo convention.
- Reference: design doc at `docs/plans/2026-05-02-unified-force-done-design.md`.
- Existing test patterns (stub `_StubBrowser`, `_mock_llm_calls`, `_StubPage`) live in `task2/tests/unit/test_loop_anti_repeat.py:13-80`. Reuse them — do NOT reimplement.
- After every commit, run `cd task2 && uv run ruff check . && uv run ruff format --check .` to keep the bar green. If `ruff format` reports diffs, run `uv run ruff format .` and fold the result into the same commit.

---

## Task 1: Empty `list_interactive` exhausted-on-arrival

**Files:**
- Modify: `task2/src/agent/loop.py:296-342` (the `list_interactive` auto-advance branch)
- Test: `task2/tests/unit/test_loop_anti_repeat.py` (append at end)

**Step 1: Write the failing test**

Append to `test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_list_interactive_empty_treated_as_exhausted_on_arrival(tmp_path):
    """When list_interactive returns "[]", treat as exhausted-on-arrival:
    inject the synthetic 'end of interactive list' obs and hide the tool
    from the next turn — same as the hop-cap-hide arm."""
    browser = _StubBrowser("body text")

    captured_tools: list[list[str]] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        # Step 0: list_interactive(offset=0) (will return "[]"); step 1: done.
        idx = len(captured_tools) - 1
        if idx == 0:
            tc_name, tc_args = "list_interactive", {"offset": 0, "limit": 50}
        else:
            tc_name, tc_args = "done", {"status": "success", "answer": "ok"}
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)

    async def list_interactive(offset: int = 0, limit: int = 50, thought: str = ""):
        return "[]"

    reg.register(
        Tool(
            "list_interactive",
            "list_interactive",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "thought": {"type": "string"},
                },
            },
            list_interactive,
        )
    )
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Step 0 obs is the exhausted-on-arrival synthetic message.
    assert loop.tape[0]["obs"].startswith("(end of interactive list")
    # Turn 1 (the next LLM call) MUST NOT have list_interactive in tools.
    assert "list_interactive" not in captured_tools[1]
```

**Step 2: Run the test to verify it fails**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_list_interactive_empty_treated_as_exhausted_on_arrival -v`
Expected: FAIL — current code records `"[]"` at offset=0, walks one offset, gets `"[]"` again, doesn't match `was_served`, never trips the exhausted arm. Step 0 obs likely contains `[auto-advanced 0→50: 0 unchanged …] []` rather than `(end of interactive list…`.

**Step 3: Write minimal implementation**

In `task2/src/agent/loop.py`, locate the block starting at line 307 (`served_offset = requested_offset`) and modify so that if the very first served result is `"[]"`, we go straight to the exhausted arm:

```python
served_offset = requested_offset
served_str = await _safe_call_li(served_offset)
hops = 0
exhausted = False
if served_str.strip() == "[]":
    exhausted = True
elif not served_str.startswith("ERROR:"):
    while self.list_interactive_cache.was_served(
        offset=served_offset, candidate=served_str
    ):
        hops += 1
        if hops >= self._max_auto_advance_hops:
            exhausted = True
            break
        served_offset += limit
        served_str = await _safe_call_li(served_offset)
        if served_str.startswith("ERROR:"):
            break
        if served_str.strip() == "[]":
            exhausted = True
            break
```

The two `served_str.strip() == "[]"` checks (initial result + post-hop result) ensure we never serve an empty list to the agent and never walk past one.

**Step 4: Run the test to verify it passes**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_list_interactive_empty_treated_as_exhausted_on_arrival -v`
Expected: PASS

**Step 5: Run the full unit suite to check nothing regresses**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: 131 passed (130 prior + 1 new). If any prior list_interactive test breaks, double-check the conditional ordering — `exhausted=True` must short-circuit the while-loop, and the cache must NOT record `"[]"`.

**Step 6: Lint + commit**

```bash
cd /home/pgi/v_coding_test2 && cd task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py && \
git commit -m "$(cat <<'EOF'
feat(task2): treat empty list_interactive result as exhausted-on-arrival

When list_interactive returns "[]" at the requested offset, hide the
tool and emit the existing "(end of interactive list ...)" synthetic
obs immediately, instead of walking unbounded offsets through more
empty results. Closes the case-113 failure mode where the agent burned
13 consecutive steps on offsets 350..2750, all returning "[]".

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `force_done.py` skeleton + return value shape

**Files:**
- Create: `task2/src/agent/force_done.py`
- Test: `task2/tests/unit/test_force_done.py` (new)

**Step 1: Write the failing test**

Create `task2/tests/unit/test_force_done.py`:

```python
import json

import httpx
import pytest

from agent.force_done import coerce_done_via_llm
from agent.llm import LLMClient


_DONE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "done",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["status", "answer"],
        },
    },
}


def _llm_returning(name: str, args: dict) -> LLMClient:
    async def handler(request):
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_coerce_done_returns_llm_done_tool_call():
    llm = _llm_returning("done", {"status": "success", "answer": "the answer"})
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="any goal",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "success", "answer": "the answer"}
```

**Step 2: Run the test to verify it fails**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py::test_coerce_done_returns_llm_done_tool_call -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.force_done'`.

**Step 3: Write minimal implementation**

Create `task2/src/agent/force_done.py`:

```python
from __future__ import annotations

import json
from typing import Any, Literal

from agent.context import build_messages
from agent.llm import LLMClient

Trigger = Literal["max_steps", "no_progress", "asked_after_clarification"]

_PLACEHOLDERS: dict[Trigger, str] = {
    "max_steps": "max steps",
    "no_progress": "stuck: no novel observation for {n} consecutive steps",
    "asked_after_clarification": "stuck after user clarification",
}


def _placeholder(trigger: Trigger, n_no_progress: int | None) -> str:
    template = _PLACEHOLDERS[trigger]
    if trigger == "no_progress":
        return template.format(n=n_no_progress if n_no_progress is not None else "?")
    return template


async def coerce_done_via_llm(
    *,
    llm: LLMClient,
    tape: list[dict[str, Any]],
    goal: str,
    qa: list[tuple[str, str]],
    url: str,
    url_notes: str,
    page_header: str,
    trigger: Trigger,
    n_no_progress: int | None,
    done_tool_schema: dict[str, Any],
) -> dict[str, Any]:
    """One-shot LLM call constrained to emit done(...). Returns
    {"status": ..., "answer": ...}. On transport-level deviation
    (empty tool_calls, wrong tool name, malformed args) returns a
    trigger-specific placeholder. Network errors propagate."""
    messages = build_messages(
        system="",
        goal=goal,
        qa=qa,
        url_notes=url_notes,
        tape=tape,
        page_header=page_header,
        replan_hint=None,
    )
    msg, _usage = await llm.chat(
        messages,
        tools=[done_tool_schema],
        tool_choice={"type": "function", "function": {"name": "done"}},
        reasoning=False,
    )
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    tc = tool_calls[0]
    if tc.get("function", {}).get("name") != "done":
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    try:
        args = json.loads(tc["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {"status": "failed", "answer": _placeholder(trigger, n_no_progress)}
    return {
        "status": args.get("status", "failed"),
        "answer": args.get("answer", _placeholder(trigger, n_no_progress)),
    }
```

**Step 4: Run the test to verify it passes**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: PASS (1 test)

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/force_done.py task2/tests/unit/test_force_done.py && \
git commit -m "$(cat <<'EOF'
feat(task2): add coerce_done_via_llm helper skeleton

One-shot LLM call constrained to emit done(...) via named tool_choice;
returns parsed {status, answer} or a trigger-specific placeholder on
transport-level deviation. Not yet wired into the loop.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Named `tool_choice` and trigger-specific instruction in user message

**Files:**
- Modify: `task2/src/agent/force_done.py`
- Modify: `task2/tests/unit/test_force_done.py`

**Step 1: Write the failing tests**

Append to `test_force_done.py`:

```python
def _capturing_llm():
    captured: dict[str, Any] = {}

    async def handler(request):
        body = json.loads(request.content.decode())
        captured["payload"] = body
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "done",
                                        "arguments": json.dumps(
                                            {"status": "failed", "answer": "x"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler)), captured


@pytest.mark.asyncio
async def test_coerce_done_passes_named_tool_choice():
    llm, captured = _capturing_llm()
    await coerce_done_via_llm(
        llm=llm, tape=[], goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="max_steps",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert captured["payload"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "done"},
    }
    assert [t["function"]["name"] for t in captured["payload"]["tools"]] == ["done"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger,needle",
    [
        ("max_steps", "final allowed step"),
        ("no_progress", "no novel observation"),
        ("asked_after_clarification", "User clarification"),
    ],
)
async def test_coerce_done_includes_trigger_specific_instruction(trigger, needle):
    llm, captured = _capturing_llm()
    await coerce_done_via_llm(
        llm=llm, tape=[], goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger=trigger,
        n_no_progress=12 if trigger == "no_progress" else None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert needle in combined
```

**Step 2: Run the tests to verify they fail**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: `test_coerce_done_passes_named_tool_choice` PASSES (already implemented in Task 2). The three parametrized `test_coerce_done_includes_trigger_specific_instruction` cases FAIL — the helper does not currently append a trigger-specific instruction.

**Step 3: Write minimal implementation**

Modify `task2/src/agent/force_done.py`:

```python
_FORCE_DONE_PROMPTS: dict[Trigger, str] = {
    "max_steps": (
        "This is your final allowed step. Commit done() now with the best "
        "answer your prior reads support. If your reads contained the answer, "
        'use done(success, "<answer>"); otherwise done(failed, "<one-line '
        'reason>").'
    ),
    "no_progress": (
        "You have produced no novel observation for {n} consecutive steps. "
        "Either the goal is unreachable from this browser (commit "
        'done(failed, "blocked by <wall>") if you saw a Cloudflare/CAPTCHA/'
        'login wall, or done(failed, "<reason>") otherwise) or you already '
        'have the answer (commit done(success, "<answer>") with the rendered '
        "value from your reads — even if the page does not name the asked "
        "phrase verbatim)."
    ),
    "asked_after_clarification": (
        "User clarification did not unblock you. Commit "
        'done(failed, "<closest answer you have>") rather than retrying the '
        "same action."
    ),
}


def _instruction(trigger: Trigger, n_no_progress: int | None) -> str:
    template = _FORCE_DONE_PROMPTS[trigger]
    if trigger == "no_progress":
        return template.format(n=n_no_progress if n_no_progress is not None else "?")
    return template
```

In `coerce_done_via_llm`, between `messages = build_messages(...)` and `msg, _usage = await llm.chat(...)`, append a synthetic user message:

```python
messages.append({"role": "user", "content": _instruction(trigger, n_no_progress)})
```

**Step 4: Run the tests to verify they pass**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: 4 passed (1 from Task 2 + 1 named-tool-choice + 3 parametrized trigger cases).

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/force_done.py task2/tests/unit/test_force_done.py && \
git commit -m "$(cat <<'EOF'
feat(task2): trigger-specific instruction in coerce_done_via_llm

Three templates (max_steps / no_progress / asked_after_clarification)
appended as a synthetic user message before the constrained LLM call.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Read-content dump

**Files:**
- Modify: `task2/src/agent/force_done.py`
- Modify: `task2/tests/unit/test_force_done.py`

**Step 1: Write the failing tests**

Append to `test_force_done.py`:

```python
@pytest.mark.asyncio
async def test_coerce_done_appends_read_content_dump():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "goto", "args": {"url": "https://x.test/"}, "obs": "navigated"},
        {"action": "read", "args": {"offset": 0}, "obs": "first read content"},
        {"action": "read", "args": {"offset": 1600}, "obs": "second read content"},
        {"action": "read", "args": {"offset": 3200}, "obs": "third read content"},
    ]
    await coerce_done_via_llm(
        llm=llm, tape=tape, goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="max_steps",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert "Read content captured so far (chronological):" in combined
    assert "read(offset=0)" in combined
    assert "first read content" in combined
    assert "read(offset=1600)" in combined
    assert "second read content" in combined
    assert "read(offset=3200)" in combined
    assert "third read content" in combined
    # Order preserved
    assert combined.index("first read content") < combined.index("second read content")
    assert combined.index("second read content") < combined.index("third read content")


@pytest.mark.asyncio
async def test_coerce_done_handles_empty_reads():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "goto", "args": {"url": "https://x.test/"}, "obs": "navigated"},
        {"action": "list_interactive", "args": {}, "obs": "[]"},
    ]
    await coerce_done_via_llm(
        llm=llm, tape=tape, goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="max_steps",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert "(none — no read() calls in tape)" in combined


@pytest.mark.asyncio
async def test_coerce_done_truncates_long_read_obs():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "read", "args": {"offset": 0}, "obs": "X" * 5000},
    ]
    await coerce_done_via_llm(
        llm=llm, tape=tape, goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="max_steps",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    # 800-char window, then 5000 - 800 = 4200 X's must NOT be present.
    assert "X" * 800 in combined
    assert "X" * 801 not in combined
```

**Step 2: Run the tests to verify they fail**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: 3 new tests FAIL — the helper currently does not include a "Read content captured so far" block.

**Step 3: Write minimal implementation**

In `task2/src/agent/force_done.py`, add:

```python
_READ_TRUNCATE = 800


def _read_content_dump(tape: list[dict[str, Any]]) -> str:
    reads = [s for s in tape if s.get("action") == "read"]
    if not reads:
        return "Read content captured so far: (none — no read() calls in tape)"
    lines = ["Read content captured so far (chronological):"]
    for s in reads:
        offset = s.get("args", {}).get("offset", 0)
        obs = s.get("obs", "") or ""
        if len(obs) > _READ_TRUNCATE:
            obs = obs[:_READ_TRUNCATE]
        lines.append(f'- read(offset={offset}): "{obs}"')
    return "\n".join(lines)
```

Modify `coerce_done_via_llm` to append two synthetic user messages: the instruction (already there from Task 3) and the read-content dump:

```python
messages.append({"role": "user", "content": _instruction(trigger, n_no_progress)})
messages.append({"role": "user", "content": _read_content_dump(tape)})
```

**Step 4: Run the tests to verify they pass**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: 7 passed (4 prior + 3 new).

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/force_done.py task2/tests/unit/test_force_done.py && \
git commit -m "$(cat <<'EOF'
feat(task2): append chronological read-obs dump to force-done turn

Every prior step.action == "read" obs is included in tape order,
truncated to 800 chars per entry, so the LLM has a concrete content
reference at the moment of the forced commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Transport-level fallbacks (empty tool_calls, wrong tool name, malformed args)

**Files:**
- Modify: `task2/tests/unit/test_force_done.py` (3 new tests)

**Step 1: Write the failing tests**

Append to `test_force_done.py`:

```python
@pytest.mark.asyncio
async def test_coerce_done_falls_back_on_empty_tool_calls():
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "no tool"}}]},
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    result = await coerce_done_via_llm(
        llm=llm, tape=[], goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="max_steps",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "failed", "answer": "max steps"}


@pytest.mark.asyncio
async def test_coerce_done_falls_back_on_wrong_tool_name():
    llm = _llm_returning("read", {"offset": 0})
    result = await coerce_done_via_llm(
        llm=llm, tape=[], goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="asked_after_clarification",
        n_no_progress=None, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "failed", "answer": "stuck after user clarification"}


@pytest.mark.asyncio
async def test_coerce_done_no_progress_placeholder_interpolates_n():
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "x"}}]},
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    result = await coerce_done_via_llm(
        llm=llm, tape=[], goal="g", qa=[], url="https://x.test/", url_notes="",
        page_header="URL=https://x.test/", trigger="no_progress",
        n_no_progress=14, done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {
        "status": "failed",
        "answer": "stuck: no novel observation for 14 consecutive steps",
    }
```

**Step 2: Run the tests to verify they fail or pass**

Run: `cd task2 && uv run pytest tests/unit/test_force_done.py -v`
Expected: 10 passed. Per the design, the helper from Task 2 already returns trigger-specific placeholders for empty `tool_calls` and wrong tool names — these tests verify that contract and ought to pass on first run. If any FAIL, the bug is in Task 2's implementation; fix it there and re-run.

**Step 3: If any failed, write the minimal fix**

If `test_coerce_done_no_progress_placeholder_interpolates_n` fails, the `_placeholder` function isn't formatting the `{n}` template. Verify the implementation matches Task 2 spec.

If `test_coerce_done_falls_back_on_wrong_tool_name` fails, the early `return` after the name-check isn't firing. Re-check `tc.get("function", {}).get("name") != "done"` branch.

**Step 4: Run the full unit suite**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: 141 passed (131 prior including Task 1's new test + 10 in test_force_done.py).

**Step 5: Commit (only if any code changed)**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/tests/unit/test_force_done.py task2/src/agent/force_done.py && \
git commit -m "$(cat <<'EOF'
test(task2): cover transport-level fallbacks in coerce_done_via_llm

Empty tool_calls, wrong tool name, no_progress placeholder
interpolation. Locks in the defense-in-depth behavior against
non-conformant LLM endpoints.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

If only test files changed (helper passed without modification), just commit `task2/tests/unit/test_force_done.py`.

---

## Task 6: Wire `coerce_done_via_llm` into the max_steps boundary

**Files:**
- Modify: `task2/src/agent/loop.py` (replace `is_final_step` block at 220-226 and the trailing `"max steps"` fallback at 528)
- Modify: `task2/tests/unit/test_loop_anti_repeat.py` (add integration test)

**Step 1: Write the failing test**

Append to `test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_force_done_at_max_steps_replaces_max_steps_fallback(tmp_path):
    """At step_idx == max_steps - 1, the loop must call coerce_done_via_llm
    and use its return value, not fall through to the "max steps" placeholder."""
    text = "A" * 1600
    browser = _StubBrowser(text)

    captured_tool_choice: list[Any] = []
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tool_choice.append(body.get("tool_choice"))
        call_count["n"] += 1
        # max_steps=3 → step_idx 0, 1; final step is the forced-done call (3rd).
        if call_count["n"] in (1, 2):
            tc_name, tc_args = "read", {"offset": 0}
        else:
            tc_name, tc_args = "done", {"status": "success", "answer": "extracted"}
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=3,
    )
    result = await loop.run("anything")

    # The final result must come from the forced-done LLM call, not the placeholder.
    assert result == {"status": "success", "answer": "extracted"}
    # The forced call (3rd) must use named tool_choice.
    assert captured_tool_choice[2] == {"type": "function", "function": {"name": "done"}}
```

**Step 2: Run the test to verify it fails**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_at_max_steps_replaces_max_steps_fallback -v`
Expected: FAIL — current behavior is either (a) result is `{"status":"failed","answer":"max steps"}` because the LLM emits `read` on the final step bypass, or (b) `tool_choice` on the third call is `"auto"` not the named-function dict.

**Step 3: Write minimal implementation**

In `task2/src/agent/loop.py`:

1. Add `from agent.force_done import coerce_done_via_llm` at the top with the other agent imports.
2. Add a small helper inside `ReactLoop` for the `done` tool schema:

```python
def _done_tool_schema(self) -> dict[str, Any]:
    for t in self.registry.to_openai_tools():
        if t["function"]["name"] == "done":
            return t
    raise RuntimeError("done tool not registered")
```

3. At the **top** of the for-loop body in `run()` (right after `for step_idx in range(self.max_steps):` and before `current_text = ...`), add the max-step short-circuit:

```python
if step_idx == self.max_steps - 1:
    url = self._current_url()
    url_notes = self.notes.get(url) if self.notes else ""
    result = await coerce_done_via_llm(
        llm=self.llm,
        tape=self.tape,
        goal=goal,
        qa=list(self.qa),
        url=url,
        url_notes=url_notes,
        page_header=self._page_header(),
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=self._done_tool_schema(),
    )
    self.trace.write({"type": "done", "payload": result})
    return result
```

4. Remove the now-dead `is_final_step` branch at lines 220-224. Keep the `tools = self.registry.to_openai_tools_filtered(exclude=self._hidden_tools)` else-branch as the only path.
5. The trailing `"max steps"` fallback at line 528 is now unreachable (the for-loop can't exit normally because step_idx == max_steps - 1 always returns). Remove the two lines:

```python
self.trace.write({"type": "done", "payload": {"status": "failed", "answer": "max steps"}})
return {"status": "failed", "answer": "max steps"}
```

**Step 4: Run the test to verify it passes**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_at_max_steps_replaces_max_steps_fallback -v`
Expected: PASS

**Step 5: Run the full unit suite**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: All passing. If `test_only_done_exposed_on_final_step` from the prior commit (`92beea7`) now fails because the loop no longer calls the LLM with `tools=[done]` on the final step (it now bypasses to `coerce_done_via_llm`), update that test to assert the new behavior: the captured tools at the final-step call should be only `["done"]` (because `coerce_done_via_llm` passes `tools=[done_tool_schema]`). The test still verifies the spec; only the call site shifted. Rewrite as needed.

**Step 6: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py && \
git commit -m "$(cat <<'EOF'
feat(task2): force-done at max_steps via coerce_done_via_llm

At step_idx == max_steps - 1 the loop now bypasses the normal ReAct
turn and makes one constrained LLM call with tools=[done] and named
tool_choice. The "max steps" fallback string is gone except as the
transport-failure placeholder inside coerce_done_via_llm.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire `coerce_done_via_llm` into both `NO_PROGRESS_GIVEUP` sites

**Files:**
- Modify: `task2/src/agent/loop.py` (replace `NO_PROGRESS_GIVEUP` blocks at ~390-401 and ~483-490)
- Modify: `task2/tests/unit/test_loop_anti_repeat.py` (add integration test)

**Step 1: Write the failing test**

Append to `test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_force_done_on_no_progress_replaces_stuck_message(tmp_path):
    """When no_progress_streak hits NO_PROGRESS_GIVEUP, the loop must call
    coerce_done_via_llm with trigger=no_progress and use its return value,
    not return the legacy "stuck: no novel observation..." string."""
    # Browser returns the same text every turn; reads always produce the same obs.
    browser = _StubBrowser("static body")

    captured_user_messages: list[str] = []
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        for m in body["messages"]:
            if m["role"] == "user":
                captured_user_messages.append(m["content"])
        call_count["n"] += 1
        # All calls except the forced-done one return read; the forced-done
        # call (last) returns done.
        body_user_combined = "\n".join(
            m["content"] for m in body["messages"] if m["role"] == "user"
        )
        if "no novel observation" in body_user_combined:
            tc_name, tc_args = "done", {"status": "failed", "answer": "no_progress committed"}
        else:
            tc_name, tc_args = "read", {"offset": 0}
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=50,
    )
    result = await loop.run("anything")
    assert result["answer"] == "no_progress committed"
    # And one of the user messages must be the no_progress instruction.
    assert any("no novel observation" in m for m in captured_user_messages)
```

**Step 2: Run the test to verify it fails**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_on_no_progress_replaces_stuck_message -v`
Expected: FAIL — currently the loop returns `{"status":"failed","answer":"stuck: no novel observation for 12 consecutive steps"}` from one of the two NO_PROGRESS_GIVEUP sites.

**Step 3: Write minimal implementation**

In `task2/src/agent/loop.py`, find both NO_PROGRESS_GIVEUP blocks. Replace each `return {"status":"failed", "answer":"stuck: no novel observation for ... consecutive steps"}` with:

```python
url = self._current_url()
url_notes = self.notes.get(url) if self.notes else ""
result = await coerce_done_via_llm(
    llm=self.llm,
    tape=self.tape,
    goal=goal,
    qa=list(self.qa),
    url=url,
    url_notes=url_notes,
    page_header=self._page_header(),
    trigger="no_progress",
    n_no_progress=self.no_progress_streak,
    done_tool_schema=self._done_tool_schema(),
)
self.trace.write({"type": "done", "payload": result})
return result
```

Remove the now-dead `f"stuck: no novel observation..."` strings and the redundant `self.trace.write({"type": "done", "payload": {...}})` calls preceding them.

**Step 4: Run the test to verify it passes**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_on_no_progress_replaces_stuck_message -v`
Expected: PASS

**Step 5: Run the full unit suite**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: All passing. If a prior `no_progress` test asserts on the literal `"stuck: no novel observation..."` string, update it to assert the new contract (helper invoked, helper's return is the loop's return).

**Step 6: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py && \
git commit -m "$(cat <<'EOF'
feat(task2): force-done on NO_PROGRESS_GIVEUP via coerce_done_via_llm

Both no_progress giveup sites (read-exhausted branch and main path)
now route through the helper. The legacy 'stuck: no novel observation
for N consecutive steps' string survives only as the transport-failure
placeholder.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire `coerce_done_via_llm` into the asked-state giveup

**Files:**
- Modify: `task2/src/agent/loop.py` (replace lines 271-277)
- Modify: `task2/tests/unit/test_loop_anti_repeat.py` (add integration test)

**Step 1: Write the failing test**

Append to `test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_force_done_on_asked_after_clarification_replaces_stuck_after_user(tmp_path):
    """The asked-state giveup branch must route through coerce_done_via_llm
    rather than synthesizing a hard-coded done(failed, 'stuck after user clarification')."""
    browser = _StubBrowser("static body")
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        body_user_combined = "\n".join(
            m["content"] for m in body["messages"] if m["role"] == "user"
        )
        call_count["n"] += 1
        # Force the loop into asked state by repeating the same read 3 times,
        # then provide a non-empty user reply, then repeat again to trigger giveup.
        if "User clarification" in body_user_combined:
            tc_name, tc_args = "done", {"status": "failed", "answer": "asked-giveup committed"}
        else:
            tc_name, tc_args = "read", {"offset": 0}
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    # Pre-stub the question channel to immediately return a reply so the asked
    # state advances cleanly.
    qc.set_reply("any reply")  # see meta.QuestionChannel API; if absent, use the equivalent
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "ask_user_question",
            "ask",
            {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            meta["ask_user_question"],
        )
    )
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=50,
    )
    result = await loop.run("anything")
    assert result["answer"] == "asked-giveup committed"
```

NOTE: if `QuestionChannel.set_reply(...)` doesn't exist, look in `task2/src/agent/tools/meta.py` for the actual API to pre-seed a reply. Adjust the test to use the real API. The test's *intent* is what matters: drive the state machine into the giveup branch and verify the helper is invoked.

**Step 2: Run the test to verify it fails**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_on_asked_after_clarification_replaces_stuck_after_user -v`
Expected: FAIL — current behavior synthesizes `done(failed, "stuck after user clarification")` directly.

**Step 3: Write minimal implementation**

In `task2/src/agent/loop.py`, replace lines 271-277:

```python
elif state == "asked" and last_aa is not None and new_aa == last_aa:
    name = "done"
    args = {
        "status": "failed",
        "answer": "stuck after user clarification",
    }
    state = "giveup"
```

with:

```python
elif state == "asked" and last_aa is not None and new_aa == last_aa:
    url = self._current_url()
    url_notes = self.notes.get(url) if self.notes else ""
    result = await coerce_done_via_llm(
        llm=self.llm,
        tape=self.tape,
        goal=goal,
        qa=list(self.qa),
        url=url,
        url_notes=url_notes,
        page_header=self._page_header(),
        trigger="asked_after_clarification",
        n_no_progress=None,
        done_tool_schema=self._done_tool_schema(),
    )
    self.trace.write({"type": "done", "payload": result})
    return result
```

**Step 4: Run the test to verify it passes**

Run: `cd task2 && uv run pytest tests/unit/test_loop_anti_repeat.py::test_force_done_on_asked_after_clarification_replaces_stuck_after_user -v`
Expected: PASS

**Step 5: Run the full unit suite**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: All passing. If a prior test asserted `"stuck after user clarification"` literally, update it to assert the helper was invoked.

**Step 6: Commit**

```bash
cd /home/pgi/v_coding_test2/task2 && uv run ruff format . && uv run ruff check . && cd /home/pgi/v_coding_test2 && \
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py && \
git commit -m "$(cat <<'EOF'
feat(task2): force-done on asked-state giveup via coerce_done_via_llm

The state="asked" + same-action-repeat branch now routes through the
unified helper. 'stuck after user clarification' is now only the
transport-failure placeholder.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final sweep — full bench, lint, push

**Files:** none modified, this is a verification-only task.

**Step 1: Full unit suite**

Run: `cd task2 && uv run pytest tests/unit -q`
Expected: All tests passing (count = prior_total + 4 new force-done tests in test_force_done.py + 1 empty-list test + 3 integration tests in test_loop_anti_repeat.py).

**Step 2: Lint**

Run: `cd task2 && uv run ruff check . && uv run ruff format --check .`
Expected: All checks passed; format-check shows no diffs.

**Step 3: Replay-bench (quick)**

Run: `cd task2 && uv run pytest tests/bench -q` (or whatever the replay-bench command is — see `task2/pyproject.toml` `[project.scripts]` and `task2/tests/bench/`).
Expected: No replay-bench regressions. If any regress on the placeholder string match, update the replay assertion to check the new contract (helper invoked).

**Step 4: Push**

```bash
git push origin task2-web-agent
```

**Step 5: Optional live bench on cases 107, 111, 113 (manual)**

If desired, follow `bench-failure-triage` instructions to start the agent server fresh and run:

```bash
cd task2 && uv run python scripts/bench_webvoyager.py --ids 107,111,113
```

Inspect results. The expected change vs the prior bench:
- 107: should commit something other than `"max steps"` if any read content is in the tape (success unlikely on first try; we're chasing the *commit* behavior, not the answer).
- 111: still wall-blocked, still ~6 steps; `done(failed, "blocked by cloudflare ...")` survives unchanged because the wall-banner shortcut fires before any force-done site.
- 113: should not show 13 consecutive empty `list_interactive` results; final outcome still likely `failed` but via a real `coerce_done_via_llm` call rather than the legacy stuck-string.

This step is for confirmation only and is OK to skip if the bench server is unavailable.

---

## Summary of files changed

```
task2/src/agent/force_done.py            (new, ~110 lines)
task2/src/agent/loop.py                  (modified, ~50 lines net change)
task2/tests/unit/test_force_done.py      (new, ~270 lines)
task2/tests/unit/test_loop_anti_repeat.py (extended with 4 new tests)
```

No `pyproject.toml` changes; no new deps.

## Out of scope (per design doc)

- `agent/wall_detect.py`, `agent/context.py`, `agent/page_diff.py` are NOT touched.
- The hinted state machine (`_last_three_match → state="hinted"` → `REPLAN_HINT`) stays as-is.
- The bench script and the `_SYSTEM` prompt are NOT touched.
