# Narrative History + Mandatory Reason Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the (thought, action, args, obs) tape-as-prompt-history with a compact unbounded
`(url, action, reason)` narrative + last 3 raw obs, drop all code-level stuck detection, and
make `reason` a mandatory field on every tool.

**Architecture:** System prompt carries the narrative for the entire session; user message carries
goal + last 3 raw obs only. Conversation is exactly 3 messages per turn (system + user + assistant
tool call) — no tool-call/tool-result pair interleaving. Tape stays in memory and in trace JSONL
for debugging; only the rendering of context to the LLM changes.

**Tech Stack:** Python 3.13, FastAPI/uvicorn, Playwright, OpenAI-compatible chat-completions
client, `uv` package manager, `ruff` linter, `pytest`.

---

## Conventions

- Working dir for all commands: `/home/pgi/v_coding_test2/task2`.
- Run tests with `uv run pytest <path> -v`.
- Run lint with `uv run ruff check .`.
- Each task ends with one commit. Commit message uses conventional style:
  `feat(task2)|fix(task2)|refactor(task2)|test(task2): <summary>`.
- TDD red-first: write failing test, see it fail for the expected reason,
  write minimal implementation, see it pass.

---

## Phase 1 — Revert plateau machinery

### Task 1: Revert the 6 plateau commits

**Goal:** Get the working tree back to the AX-tree-only state so the new mechanism
lands cleanly. The reverts are mechanical; no test changes needed because the plateau
tests get deleted in a later task anyway.

**Commits to revert (newest first):**

- `6d930b7` fix(task2): plateau pending clears on novel obs, not just reason
- `87d3fee` feat(task2): inject plateau interrupt hint into system prompt
- `1e9b91a` feat(task2): reason clears plateau pending; doesn't touch streak
- `7f1b511` docs(task2): explain hinted-redirect gate at plateau call site
- `1d7b61e` feat(task2): restrict tool set to reason/done/ask at plateau
- `eea03ec` feat(task2): introduce PLATEAU_INTERRUPT constant + pending flag

**Step 1: Revert in reverse-chronological order**

```bash
git revert --no-edit 6d930b7 87d3fee 1e9b91a 7f1b511 1d7b61e eea03ec
```

This produces 6 revert commits. If any revert hits a conflict, resolve it minimally
(prefer pre-plateau code), `git add`, `git revert --continue`.

**Step 2: Verify the test suite still passes after the reverts**

```bash
uv run pytest -x
```

Expected: 215 - 5 (deleted plateau tests) = 210 tests pass. The plateau tests come
back as "FAILED to run" because the constants they reference no longer exist; that's
expected — they get deleted in Phase 6. For now, mark them with pytest collect errors
and proceed.

If the plateau tests cause collection errors that block the rest of the suite, delete
`tests/unit/test_loop_plateau_interrupt.py` immediately (it is going to be deleted
anyway).

**Step 3: Lint**

```bash
uv run ruff check .
```

Expected: clean. If revert left orphan imports, fix them.

**Step 4: Squash the 6 revert commits into one**

For a clean PR history. Use a soft reset and recommit:

```bash
git reset --soft HEAD~6
git commit -m "$(cat <<'EOF'
revert(task2): unship plateau-interrupt machinery

Reverts six commits that introduced PLATEAU_INTERRUPT, the pending
flag, the tool-list restriction, the hinted-redirect comment, the
reason-clears-pending change, and the lockout fix. The mechanism's
byte-equality trigger was too literal to catch case-107-shape thrash
(see observations.md from the bench validation). The replacement is a
narrative-history + mandatory-reason architecture; see
docs/plans/2026-05-03-narrative-history-design.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

**Step 5: Confirm we're at the right baseline**

```bash
grep -n "PLATEAU_INTERRUPT\|_plateau_interrupt_pending\|_PLATEAU_INTERRUPT_HINT" src/agent/loop.py src/agent/context.py
```

Expected: no matches (or only matches inside comments referencing the removed mechanism — should
be zero matches in source). If any remain, the revert didn't fully clean up; fix and re-commit.

---

## Phase 2 — Mandatory `reason` field on every tool

### Task 2: Replace `thought` with `reason` in all tool schemas

**Goal:** Every tool's JSON schema has `reason` in `properties` (string) and in `required`.
The `thought` field is removed everywhere.

**Files:**
- Modify: `src/agent/tools/browser.py:171-288` (9 browser tool schemas)
- Modify: `src/agent/tools/meta.py:71-115` (3 meta tool schemas)
- Test: `tests/unit/test_registry.py` (existing file, append test)

**Step 1: Write the failing test**

Append to `tests/unit/test_registry.py`:

```python
def test_every_tool_schema_requires_reason():
    """Every tool — browser + meta — must have `reason` in both `properties`
    and `required`. This is the structural contract that the loop relies on
    to extract the agent's verbalised intent for each action."""
    from agent.browser_session import BrowserSession  # noqa: F401
    from agent.tools.browser import build_browser_tool_list
    from agent.tools.meta import QuestionChannel, build_meta_tool_list

    qc = QuestionChannel()
    # Use None for session - we're only inspecting schemas, not invoking handlers.
    browser_tools = build_browser_tool_list(session=None, restrict_goto=False)
    meta_tools = build_meta_tool_list(question_channel=qc, reason_log=[])

    for tool in browser_tools + meta_tools:
        params = tool.parameters
        assert "reason" in params.get("properties", {}), (
            f"{tool.name}: missing reason in properties"
        )
        assert params["properties"]["reason"]["type"] == "string", (
            f"{tool.name}: reason field must be type=string"
        )
        assert "reason" in params.get("required", []), (
            f"{tool.name}: reason must be in required list"
        )
        assert "thought" not in params.get("properties", {}), (
            f"{tool.name}: legacy `thought` field should be removed"
        )
```

**Step 2: Run the test, see it fail**

```bash
uv run pytest tests/unit/test_registry.py::test_every_tool_schema_requires_reason -v
```

Expected: FAIL on the first browser tool — `goto` has `thought` not `reason`.

**Step 3: Implement — rewrite all 12 schemas**

In `src/agent/tools/browser.py`, replace every occurrence of `"thought": {"type": "string"}`
with `"reason": {"type": "string"}`, and update each tool's `required` list to include
`"reason"`. Each tool's `required` becomes its existing required args plus `"reason"`.
Specifically:

- `goto`: `"required": ["url", "reason"]`
- `back`: `"required": ["reason"]`
- `read`: `"required": ["reason"]`
- `read_grep`: `"required": ["pattern", "reason"]`
- `list_interactive`: `"required": ["reason"]`
- `click`: `"required": ["id", "reason"]`
- `type`: `"required": ["id", "text", "reason"]`
- `select_option`: `"required": ["id", "value", "reason"]`
- `press_key`: `"required": ["key", "reason"]`

In `src/agent/tools/meta.py`, same pattern:

- `reason` (the tool): `"required": ["text", "reason"]`. Yes, the `reason` tool now has
  both `text` (its primary arg, the thought to log) AND a `reason` field (the universal
  why-this-action explanation). Awkward but correct — the tool's name shouldn't conflate
  with the universal field. Both fields stay.
- `ask_user_question`: `"required": ["question", "reason"]`
- `done`: `"required": ["status", "answer", "reason"]`

**Step 4: Run the test, see it pass**

```bash
uv run pytest tests/unit/test_registry.py::test_every_tool_schema_requires_reason -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/agent/tools/browser.py src/agent/tools/meta.py tests/unit/test_registry.py
git commit -m "feat(task2): require reason field on every tool schema"
```

### Task 3: Loop extracts `reason` from args

**Goal:** Loop pops `reason` from action args (replacing `thought`), stores it on the tape,
and emits an error obs if missing.

**Files:**
- Modify: `src/agent/loop.py:366` (extraction site) + sites that propagate `thought`
- Test: `tests/unit/test_loop_happy.py` (existing) — extend OR add a new test file

**Step 1: Write the failing test**

Create `tests/unit/test_loop_reason_field.py`:

```python
"""Reason field is mandatory; loop extracts it from args, stores on tape,
errors if missing."""
from __future__ import annotations

import pytest
import httpx
from agent.llm import LLMClient
# ... (use the same fixture pattern as test_loop_happy.py)
# (paste enough scaffolding here that the test runs in isolation)

@pytest.mark.asyncio
async def test_reason_extracted_from_args_into_tape(make_loop, mock_llm):
    """When LLM emits a tool call with `reason: "X"` in args, the tape entry
    has tape[i]['reason'] == "X" and reason is removed from args."""
    mock_llm.queue_tool_call("noopA", {"reason": "I want to test something."})
    mock_llm.queue_tool_call("done", {"status": "success", "answer": "ok",
                                       "reason": "Got what I needed."})
    loop = make_loop()
    result = await loop.run(goal="test", max_steps=5)

    assert loop.tape[0]["reason"] == "I want to test something."
    assert "reason" not in loop.tape[0]["args"]  # popped, not duplicated
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_missing_reason_produces_error_obs(make_loop, mock_llm):
    """If the LLM somehow emits a tool call without `reason`, the loop
    appends an error obs to the tape and continues. The next turn must
    succeed."""
    mock_llm.queue_tool_call("noopA", {})  # no reason
    mock_llm.queue_tool_call("done", {"status": "failed", "answer": "n/a",
                                       "reason": "test exit"})
    loop = make_loop()
    await loop.run(goal="test", max_steps=5)

    assert "reason" in loop.tape[0]["obs"].lower()  # error mentions reason
    # And the loop did not crash — second action ran.
    assert loop.tape[-1]["action"] == "done"
```

(Reuse the `make_loop` / `mock_llm` fixtures from existing test files. If they're not
shared via `conftest.py`, copy the minimum scaffolding inline.)

**Step 2: Run, see it fail**

```bash
uv run pytest tests/unit/test_loop_reason_field.py -v
```

Expected: FAIL (loop currently extracts `thought`, not `reason`).

**Step 3: Implement**

In `src/agent/loop.py`:

- Replace `loop.py:366`:
  ```python
  thought = args.pop("thought", "") if isinstance(args, dict) else ""
  ```
  with:
  ```python
  reason = args.pop("reason", "") if isinstance(args, dict) else ""
  ```

- Wherever `thought` is used in tape append / trace write (`loop.py:323, 329, 509, 524, 585, 606, 624`),
  replace with `reason`.

- Tape append at `loop.py:606` becomes:
  ```python
  self.tape.append({
      "url": <captured at issue time, see Task 4>,  # placeholder for now
      "action": name,
      "args": args,
      "reason": reason,
      "obs": obs_str,
  })
  ```
  For Task 3 specifically, just replace `"thought": thought` → `"reason": reason`.
  URL field comes in Task 4.

- Add an error-emission path right after extraction, when `reason` is empty:
  ```python
  if not reason:
      obs = (
          f"ERROR: tool {name!r} called without `reason` field. The reason "
          "field is mandatory: 1–3 sentences capturing what you just observed "
          "and why you chose this action. Retry with reason."
      )
      self.tape.append({
          "url": "",  # placeholder; Task 4 fills this
          "action": name,
          "args": args,
          "reason": "",
          "obs": obs,
      })
      self.trace.write({
          "type": "step",
          "payload": {"n": step_idx, "action": name, "args": args,
                      "reason": "", "obs": obs},
      })
      continue
  ```
  Place this just after `reason = args.pop(...)` extraction at `loop.py:366`, before
  any subsequent action-dispatch logic.

**Step 4: Run, see it pass**

```bash
uv run pytest tests/unit/test_loop_reason_field.py -v
```

Expected: PASS.

**Step 5: Run full suite**

```bash
uv run pytest -x
```

Some existing tests may break because they emit tool calls without `reason`. Update them
to include `reason` in their args. This is expected and good — those tests were testing
old contract.

If a test is testing a removed mechanism (plateau, no_progress) and crashes here,
just leave it for Phase 6's deletion task — comment it out with a TODO so the suite
runs.

**Step 6: Commit**

```bash
git add -A
git commit -m "feat(task2): loop extracts mandatory reason field from tool args"
```

---

## Phase 3 — URL per tape entry

### Task 4: Capture URL on each tape entry

**Goal:** Each tape entry records the URL the agent was on when the action was issued.
This is what the narrative renders.

**Files:**
- Modify: `src/agent/loop.py` (tape-append sites + error paths)
- Test: `tests/unit/test_loop_reason_field.py` (extend) OR new test file

**Step 1: Write the failing test**

Append to `tests/unit/test_loop_reason_field.py`:

```python
@pytest.mark.asyncio
async def test_tape_entry_records_url_at_action_time(make_loop, mock_llm,
                                                     mock_browser_session):
    """tape[i]['url'] is the URL the browser was on when step i's action
    was issued (not after the action ran)."""
    mock_browser_session.set_url("https://example.com/start")
    mock_llm.queue_tool_call("goto", {"url": "https://example.com/dest",
                                       "reason": "navigating"})
    # After goto, mock browser session's URL becomes /dest.
    mock_browser_session.queue_url_after_action("https://example.com/dest")
    mock_llm.queue_tool_call("done", {"status": "success", "answer": "ok",
                                       "reason": "done"})

    loop = make_loop()
    await loop.run(goal="test", max_steps=5)

    assert loop.tape[0]["url"] == "https://example.com/start"  # url BEFORE goto
    assert loop.tape[1]["url"] == "https://example.com/dest"   # url AFTER goto
```

**Step 2: Run, see it fail**

```bash
uv run pytest tests/unit/test_loop_reason_field.py::test_tape_entry_records_url_at_action_time -v
```

Expected: FAIL (`tape[0]["url"]` doesn't exist yet).

**Step 3: Implement**

In `src/agent/loop.py`, capture URL at the top of each iteration (before action dispatch):

```python
issue_url = self._current_url()
```

…and include it in every tape append (`loop.py:323, 606`, plus the new error path from
Task 3):

```python
self.tape.append({
    "url": issue_url,
    "action": name,
    "args": args,
    "reason": reason,
    "obs": obs_str,
})
```

Same change for the error-path append.

**Step 4: Run, see it pass**

```bash
uv run pytest tests/unit/test_loop_reason_field.py -v
```

Expected: all 3 tests pass.

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(task2): tape entries record URL at action-issue time"
```

---

## Phase 4 — Narrative history rendering

### Task 5: `build_messages` renders unbounded narrative in system prompt

**Goal:** New section `## Action history` in the system message, unbounded, formatted
as `step <N> | <url> | <action_call> | <reason>`.

**Files:**
- Modify: `src/agent/context.py:49-150` (`build_messages`)
- Test: `tests/unit/test_context.py` (new file or extend if exists)

**Step 1: Write the failing test**

Create `tests/unit/test_narrative_history.py`:

```python
"""Narrative history rendering — full session, unbounded, in system prompt."""
from agent.context import build_messages


def _step(url: str, action: str, args: dict, reason: str, obs: str) -> dict:
    return {"url": url, "action": action, "args": args,
            "reason": reason, "obs": obs}


def test_narrative_renders_in_system_for_each_step():
    tape = [
        _step("https://a.com", "goto", {"url": "https://b.com"},
              "navigating to b", "navigated to https://b.com"),
        _step("https://b.com", "read", {}, "checking content", "<text>"),
    ]
    msgs = build_messages(
        system="<sys>", goal="g", qa=[], url_notes="", tape=tape,
        page_header="URL=https://b.com", replan_hint=None,
    )
    sys_content = msgs[0]["content"]
    assert "## Action history" in sys_content
    # Narrative line format: step N | url | action_call | reason
    assert "step 0 | https://a.com | goto" in sys_content
    assert "navigating to b" in sys_content
    assert "step 1 | https://b.com | read" in sys_content
    assert "checking content" in sys_content


def test_narrative_unbounded():
    tape = [_step(f"https://x{i}.com", "read", {},
                  f"reason {i}", f"obs {i}") for i in range(25)]
    msgs = build_messages(
        system="<sys>", goal="g", qa=[], url_notes="", tape=tape,
        page_header="URL=", replan_hint=None,
    )
    sys_content = msgs[0]["content"]
    for i in range(25):
        assert f"step {i} |" in sys_content
        assert f"reason {i}" in sys_content
```

**Step 2: Run, see it fail**

```bash
uv run pytest tests/unit/test_narrative_history.py -v
```

Expected: FAIL — current `build_messages` doesn't render a `## Action history` section.

**Step 3: Implement**

In `src/agent/context.py`, add a helper:

```python
def _action_call_str(action: str, args: dict) -> str:
    """Render `action(arg1=v1, arg2=v2)` for the most-discriminating args.
    Skip None / empty / very-long values. ~60-char target."""
    if not args:
        return f"{action}()"
    parts = []
    for k, v in args.items():
        if v in (None, "", []):
            continue
        sval = json.dumps(v, ensure_ascii=False)
        if len(sval) > 40:
            sval = sval[:37] + "..."
        parts.append(f"{k}={sval}")
    return f"{action}({', '.join(parts)})"


def _render_narrative(tape: list[dict[str, Any]]) -> str:
    if not tape:
        return ""
    lines = ["## Action history"]
    for i, step in enumerate(tape):
        url = step.get("url", "")
        call = _action_call_str(step.get("action", ""), step.get("args", {}))
        reason = step.get("reason", "")
        lines.append(f"step {i} | {url} | {call} | {reason}")
    return "\n".join(lines)
```

In `build_messages`, after `sys_parts = [system]` and the existing `replan_hint`
block, add:

```python
narrative = _render_narrative(tape)
if narrative:
    sys_parts.append("")
    sys_parts.append(narrative)
```

**Step 4: Run, see it pass**

```bash
uv run pytest tests/unit/test_narrative_history.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(task2): render unbounded narrative in system prompt"
```

---

## Phase 5 — Last 3 raw obs + drop assistant/tool message pairs

### Task 6: Replace recent-obs interleaved messages with raw last-3 in user message

**Goal:** Drop the existing K_RECENT=8 assistant+tool message pair rendering. Add a new
`## Recent observations (last 3)` section in the user message containing the last 3 obs
strings, separated by `---`, no wrapper.

**Files:**
- Modify: `src/agent/context.py` (`build_messages` — drop the recent loop, add new section)
- Test: `tests/unit/test_narrative_history.py` (extend)

**Step 1: Write the failing tests**

Append to `tests/unit/test_narrative_history.py`:

```python
def test_user_message_includes_last_3_obs_raw():
    tape = [_step("https://a.com", "read", {}, f"r{i}", f"obs-content-{i}")
            for i in range(5)]
    msgs = build_messages(
        system="<sys>", goal="g", qa=[], url_notes="", tape=tape,
        page_header="URL=", replan_hint=None,
    )
    user_content = msgs[1]["content"]
    assert "## Recent observations (last 3)" in user_content
    # Exactly the last 3, in order
    assert "obs-content-2" in user_content
    assert "obs-content-3" in user_content
    assert "obs-content-4" in user_content
    # NOT the older ones
    assert "obs-content-0" not in user_content
    assert "obs-content-1" not in user_content
    # Separator between obs (3 obs → 2 separators OR delimiter pattern)
    assert "---" in user_content


def test_user_message_obs_when_tape_short():
    tape = [_step("https://a.com", "read", {}, "r0", "obs0"),
            _step("https://a.com", "read", {}, "r1", "obs1")]
    msgs = build_messages(
        system="<sys>", goal="g", qa=[], url_notes="", tape=tape,
        page_header="URL=", replan_hint=None,
    )
    user_content = msgs[1]["content"]
    assert "obs0" in user_content
    assert "obs1" in user_content


def test_no_assistant_tool_pair_messages():
    """Conversation is exactly system + user. No interleaved tool_calls."""
    tape = [_step("https://a.com", "read", {}, f"r{i}", f"obs-{i}")
            for i in range(10)]
    msgs = build_messages(
        system="<sys>", goal="g", qa=[], url_notes="", tape=tape,
        page_header="URL=", replan_hint=None,
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
```

**Step 2: Run, see it fail**

```bash
uv run pytest tests/unit/test_narrative_history.py -v
```

Expected: 3 new tests fail.

**Step 3: Implement**

In `src/agent/context.py`, in `build_messages`:

- Delete the entire existing recent-tape interleaving block (`loop.py:109-149` —
  the `older` / `recent` split, the "Earlier steps:" rendering, and the
  `for off, step in enumerate(recent)` loop that builds assistant+tool pairs).
- Delete `_short`, `_call_str`, `_histogram_line`, `_novelty_line`,
  `_obs_fingerprint`, `K_RECENT`, `NOVELTY_WINDOW`, `OBS_FINGERPRINT_LEN`. They're
  unused after this change.
- Replace with:
  ```python
  recent_obs = [step.get("obs", "") for step in tape[-3:]]
  if recent_obs:
      user_parts.append("")
      user_parts.append("## Recent observations (last 3)")
      user_parts.append("---")
      for obs in recent_obs:
          user_parts.append(obs)
          user_parts.append("---")
  ```
- Final `msgs` is just `[system, user]`. Delete the loop building assistant+tool pairs.

**Step 4: Run, see it pass**

```bash
uv run pytest tests/unit/test_narrative_history.py -v
```

Expected: all narrative tests pass.

**Step 5: Update loop.py call site**

`src/agent/loop.py` previously imported `NOVELTY_WINDOW`, `_obs_fingerprint`,
`build_messages`. Drop the now-unused imports. Drop the `plateau_interrupt=` kwarg
on the `build_messages` call (already gone after Phase 1's revert; double-check).

**Step 6: Run full suite**

```bash
uv run pytest -x
```

Many existing tests will break here — those that asserted on assistant/tool message
pairs in `build_messages` output, or that imported the deleted constants. Fix them
as you encounter:

- Tests asserting `len(msgs) == 2 + 2*N` for tape length N: update to `len(msgs) == 2`.
- Tests asserting on tool_call_id formatting: delete those assertions (mechanism gone).
- Tests using `NOVELTY_WINDOW` / `K_RECENT` constants: delete the tests (deletion task in Phase 6 covers these).

**Step 7: Commit**

```bash
git add -A
git commit -m "feat(task2): swap recent-tape pair rendering for last-3 raw obs"
```

---

## Phase 6 — Remove all code-level stuck detection

### Task 7: Strip remaining stuck-detection machinery from loop.py

**Goal:** Delete all leftover stuck-detection code: hinted-redirect FSM, `no_progress_streak`,
`_last_three_match`, `_no_progress`, `_step_key`, `_obs_fingerprint`, `NO_PROGRESS_GIVEUP`,
the coerce-done-on-no-progress branches. Plus delete obsolete tests.

**Files:**
- Modify: `src/agent/loop.py` — delete listed mechanisms
- Delete: `tests/unit/test_loop_plateau_interrupt.py`, `tests/unit/test_loop_no_progress.py`,
  `tests/unit/test_loop_stuck.py`
- Modify: `tests/unit/test_loop_anti_repeat.py` — delete sections testing hinted-redirect/streak,
  keep behavioural assertions about ask_user_question and done

**Step 1: Identify what to delete**

Grep first to inventory what depends on what:

```bash
grep -n "no_progress_streak\|_last_three_match\|_no_progress\|_step_key\|NO_PROGRESS_GIVEUP\|state == \"hinted\"\|_obs_fingerprint" src/agent/loop.py
```

**Step 2: Delete the imports and definitions**

In `src/agent/loop.py`:
- Drop `NOVELTY_WINDOW`, `_obs_fingerprint` from the import line at top.
- Delete `NO_PROGRESS_GIVEUP = 9` constant.
- Delete `self.no_progress_streak = 0` initializer.
- Delete `_last_three_match`, `_no_progress`, `_step_key` methods.
- Delete the streak update in the obs-handling block (lines around 607-613).
- Delete the `if self.no_progress_streak >= NO_PROGRESS_GIVEUP:` blocks (two of them — one
  in the `ToolNameNotAllowed` handler at ~337 and one after action dispatch at ~635).
- Delete the `state == "hinted"` redirect block at ~379-395.
- Delete the `state` FSM if it's not used after this — verify with grep.

**Step 3: Delete obsolete test files**

```bash
rm tests/unit/test_loop_plateau_interrupt.py
rm tests/unit/test_loop_no_progress.py
rm tests/unit/test_loop_stuck.py
```

**Step 4: Trim test_loop_anti_repeat.py**

Open `tests/unit/test_loop_anti_repeat.py`. For each test, decide:
- Keep: tests that assert behaviour of `ask_user_question` answer flow, `done` short-circuit,
  basic LoopDone propagation.
- Delete: tests that exercise streak behaviour, hinted-redirect, NO_PROGRESS_GIVEUP coercion,
  novelty-based decisions.

When in doubt, run the test in isolation; if it imports a deleted constant or method, it goes.

**Step 5: Add a minimal regression for max_steps**

Append to `tests/unit/test_loop_happy.py` (or wherever similar tests live):

```python
@pytest.mark.asyncio
async def test_max_steps_is_only_backstop(make_loop, mock_llm):
    """No `done`, no stuck-detection — max_steps is the only stop."""
    for _ in range(10):
        mock_llm.queue_tool_call("noopA", {"reason": "looping"})
    loop = make_loop()
    result = await loop.run(goal="g", max_steps=5)
    # We expect 5 LLM calls, then a coerce-done-on-max-steps OR an explicit
    # max-steps result.
    assert len(loop.tape) == 5
    # The result type depends on existing max-steps behavior — assert whatever
    # the loop currently does (e.g. status="failed" with answer="max steps").
```

**Step 6: Run full suite + ruff**

```bash
uv run pytest -x
uv run ruff check .
```

Expected: green. Test count is significantly lower than the original 215.

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor(task2): strip code-level stuck detection — narrative is the judge"
```

---

## Phase 7 — Goto-grounding via narrative

### Task 8: Widen goto-grounding to scan history URLs + actions + reasons

**Goal:** The goto guard's allowlist is no longer derived from prior obs (no obs in narrative).
It now scans: every history entry's `url`, every prior `goto` action's `url` arg, and every
prior reason text. Any URL appearing in any of those three sources is allowed.

**Files:**
- Modify: `src/agent/loop.py::_allowlist_sources` (or wherever the allowlist closure is built)
  + `src/agent/tools/browser.py::goto` (uses the allowlist)
- Test: `tests/integration/test_browser_tools_interact.py` or a unit test

**Step 1: Find the allowlist source**

```bash
grep -n "allowlist_sources\|_extract_urls\|_is_goto_allowed" src/agent/loop.py src/agent/tools/browser.py
```

**Step 2: Write the failing test**

Create `tests/unit/test_goto_grounding.py`:

```python
"""Goto-grounding now scans narrative URLs/actions/reasons, not obs."""
import pytest
from agent.tools.browser import _extract_urls, _is_goto_allowed


def test_url_extracted_from_reason():
    urls = _extract_urls("found https://example.com/foo earlier")
    assert "https://example.com/foo" in urls


def test_goto_allowed_when_url_in_reason_text():
    sources = ["I noticed https://example.com/foo on the page"]
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert _is_goto_allowed("https://example.com/foo", allowlist)


def test_goto_blocked_when_url_nowhere():
    sources = ["nothing relevant"]
    allowlist = []
    for s in sources:
        allowlist.extend(_extract_urls(s))
    assert not _is_goto_allowed("https://malicious.example/x", allowlist)
```

(These test the helpers; the allowlist-construction integration goes in a follow-up
test if needed.)

**Step 3: Run, see it pass**

The helper-level tests likely already pass — `_extract_urls` and `_is_goto_allowed` don't
care about the source. The substantive change is in the LOOP — what it passes as sources.

**Step 4: Update the loop's allowlist source**

In `src/agent/loop.py`, find where the goto-tool's `allowlist_sources` callback is defined.
It currently returns prior obs strings. Change to return:

```python
def _goto_allowlist_sources() -> list[str]:
    sources: list[str] = []
    sources.append(self._goal or "")
    for step in self.tape:
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
    return sources
```

**Step 5: Add an integration-style test**

Append to `tests/unit/test_goto_grounding.py`:

```python
@pytest.mark.asyncio
async def test_goto_to_url_from_reason_is_allowed(make_loop, mock_llm,
                                                   mock_browser_session):
    mock_llm.queue_tool_call("read", {"reason": "saw https://example.com/foo on page"})
    mock_browser_session.queue_obs_for_action("read", "<no urls in obs>")
    mock_llm.queue_tool_call("goto", {"url": "https://example.com/foo",
                                       "reason": "navigating to discovered URL"})
    mock_browser_session.queue_obs_for_action("goto", "navigated to https://example.com/foo")
    mock_llm.queue_tool_call("done", {"status": "success", "answer": "ok",
                                       "reason": "done"})
    loop = make_loop()
    result = await loop.run(goal="g", max_steps=5)
    # The goto should NOT have been blocked.
    assert "blocked goto" not in loop.tape[1]["obs"]
    assert result["status"] == "success"
```

**Step 6: Run, see it pass**

```bash
uv run pytest tests/unit/test_goto_grounding.py -v
```

**Step 7: Commit**

```bash
git add -A
git commit -m "feat(task2): goto-grounding scans narrative urls/actions/reasons"
```

---

## Phase 8 — System prompt update

### Task 9: Update SYSTEM_PROMPT to require structured `reason`

**Goal:** The system prompt explains the mandatory `reason` field, the narrative log,
and the recent-obs window. The agent reads this at session start.

**Files:**
- Modify: `src/agent/loop.py` (the `SYSTEM_PROMPT` constant near the top of the file)
- No new test — system-prompt content is implicitly tested by bench validation.

**Step 1: Find the existing prompt**

```bash
grep -n "SYSTEM_PROMPT\|^_SYSTEM" src/agent/loop.py
```

**Step 2: Rewrite**

Locate the constant and rewrite to (something like):

```python
SYSTEM_PROMPT = """\
You are a browser-automation agent. Each turn, you call exactly one tool to make
progress toward the user's goal.

## The reason field

EVERY tool call must include a `reason` field. Format: 1–3 short sentences capturing:
1. What the most recent observation showed, relative to the goal.
2. What you expect this action to accomplish.
3. Why this action over alternatives.

Be concrete. Quote evidence from the obs. The reason field is your ONLY persistent
memory beyond the last 3 observations — make every word count.

## Action history (below)

The system prompt's `## Action history` section lists every step you have taken this
session: `step N | url | action_call | reason`. Re-read it before deciding. If a prior
reason said "found 59,513,990," that fact is still true now; don't re-fetch it. If it
shows you've made the same call 3 times, change strategy.

## Recent observations (in user message)

The user message's `## Recent observations (last 3)` section gives you the raw text of
the 3 most recent observations. Older obs are NOT preserved verbatim — only your reasons
are. This is intentional: write reasons that capture what you saw.

## Your tools

Browser navigation: goto, back, click, type, select_option, press_key.
Reading: read, read_grep, list_interactive.
Meta: reason (record a thought without acting), ask_user_question, done.

## Stopping

Call `done(success, "<answer>", reason="...")` as soon as you have the answer. Do not
keep exploring after you have it. Call `done(failed, "<best partial>", reason="...")`
when you have exhausted reasonable approaches.
"""
```

(Adapt to match the codebase's existing prompt style; this is a starting point.)

**Step 3: Run full suite**

```bash
uv run pytest -x
```

Expected: green. Some tests may have asserted on specific strings in the old prompt;
update those.

**Step 4: Commit**

```bash
git add src/agent/loop.py
git commit -m "feat(task2): rewrite system prompt for narrative + mandatory reason"
```

---

## Phase 9 — Verification

### Task 10: Full-suite + ruff regression

**Step 1: Run the full pytest suite**

```bash
uv run pytest -v
```

Expected: green. Test count significantly lower than the prior 215 because of the
deletions (plateau, no_progress, stuck, parts of anti_repeat). New tests added:
narrative rendering, reason-field extraction, URL on tape, goto-grounding.

**Step 2: Run ruff**

```bash
uv run ruff check .
```

Expected: clean.

**Step 3: No commit** — verification only. If anything fails, fix in a focused commit
labeled `fix(task2): <scope>`.

### Task 11: Bench validation

**Step 1: Restart the agent server with the new code**

```bash
lsof -ti :8001 | xargs -r kill -9 2>/dev/null
sleep 1
cd /home/pgi/v_coding_test2/task2
set -a && . ./.env && set +a
AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory \
  --host 127.0.0.1 --port 8001 &
sleep 3
ss -ltn | grep ':8001' || echo "server failed to start"
```

**Step 2: Run the full bench (12 cases)**

```bash
uv run python scripts/bench_webvoyager.py --limit 12
```

This will take ~30 minutes. Expected baseline: 9/12. Bar: ≥ 9/12, with case 107
flipping to success (the narrative should preserve "saw 59,513,990" from step 19's
reason into step 20+ where the agent calls `done`).

**Step 3: Triage any new regressions**

If any case that was previously passing now fails, that's a regression. Inspect the
trace; if the cause is genuine (not flakey network), file as a P0 in observations.md
and decide whether to fix here or punt. New failures of cases that were already failing
are not regressions.

**Step 4: Write observations.md with the bench results**

Pattern after the prior `observations.md`. Include:
- Bench JSON filename
- Per-case status delta vs prior baseline
- For 107 specifically: was the answer captured in a step's reason field? Did the
  agent call `done(success, "59,513,990")`? Cite step numbers.

**No commit** — `observations.md` is not committed per the bench-failure-triage skill
discipline.

---

## Done criteria

- All Phase 1–10 tasks committed.
- `uv run pytest -x` green; `uv run ruff check .` clean.
- Bench shows ≥ 9/12 with case 107 success documented in observations.md.
- No remaining references in code to: `K_RECENT`, `NOVELTY_WINDOW`, `NO_PROGRESS_GIVEUP`,
  `PLATEAU_INTERRUPT`, `_obs_fingerprint`, `_last_three_match`, `_no_progress`,
  `no_progress_streak`, `_plateau_interrupt_pending`, `state == "hinted"`.
