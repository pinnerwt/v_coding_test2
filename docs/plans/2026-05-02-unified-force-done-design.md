# Unified Force-Done Pathway — Design

**Date:** 2026-05-02
**Branch:** `task2-web-agent`
**Scope:** `task2/src/agent/loop.py`, new `task2/src/agent/force_done.py`, tests under `task2/tests/unit/`

## Background

Triage of bench cases 107 (HuggingFace), 111 (Cambridge Dictionary), and 113 (CanIRun.ai) on commit `92beea7` surfaced three concrete failure modes:

1. **Final-step LLM bypass.** The loop restricts `tools=[done]` at `step_idx == max_steps - 1`, but DeepSeek pattern-matches the K=8 recent assistant `tool_calls` (which reference `read` / `read_grep` / `list_interactive`) and emits a `read` call anyway. The loop's executor accepts any registered tool name, runs it, and the run terminates with the placeholder `"max steps"` instead of any real commit. Verified by reproducing the messages: `tools` was `["done"]` and the LLM still called `read`.
2. **Empty `list_interactive` walking.** `OffsetCache.was_served` is per-offset-key. When `list_interactive` returns `"[]"` at offset 350, then 550, then 750…, none of those offsets had been served before, so `was_served` returns False and the auto-advance hop counter never increments. Case 113 burned 13 consecutive steps walking offsets up to 2750, all returning `"[]"`, until the novelty detector tripped.
3. **Premature stall with answer in obs.** Trace `c20b9a834288421fb308cd7101e530c2.jsonl` (case 113, an earlier run) shows the agent successfully selecting RTX 3090, navigating to `/device/rtx-3090`, and reading content that included `"39 RUNS GREAT"` and `"Qwen 3 32B 16.9 GB · 70% · 128K ctx"` — then fiddling with sort options for 30+ steps and hitting `max steps`. Same family as #1: a force-done site exists, but it bails with placeholder text rather than committing the answer the agent already has.

All three force-done sites in `loop.py` today (`max_steps` fallback, `NO_PROGRESS_GIVEUP`, asked-state-giveup) bail with hard-coded strings; none of them give the LLM a chance to commit a real answer.

## Goal

One unified force-done pathway that, when triggered, makes one final LLM call constrained to emit `done(...)` with a useful answer drawn from the agent's read history. Plus close the empty-list-dedup hole that fed case 113.

## Non-Goals

- The hinted state machine (`_last_three_match` → `state="hinted"` → `REPLAN_HINT`) stays. It runs ahead of force-done as a soft pressure.
- `OffsetCache` keeps its per-offset-key semantics for `read` (it works correctly there).
- `detect_wall` is unchanged; the wall banner remains the upstream early-warn signal, and the unified force-done picks up downstream when a wall persists.
- No bench-grader / placeholder-string changes on the wire; existing trace consumers continue to see `"max steps"` / `"stuck: …"` strings if the LLM transport fails.

## Design Decisions (from brainstorming)

- **Q1 → (a)** One unified force-done pathway, three call sites collapse into one helper.
- **Q2 → (a)** Use named `tool_choice={"type":"function","function":{"name":"done"}}` for the forced call. The LLM physically cannot emit a non-`done` call. No loop-side rewrite fallback. Defense against malformed transport responses (empty `tool_calls`, wrong tool name) returns a trigger-specific placeholder string — that path is for transport bugs, not LLM bypass.
- **Q3 → (b) + extension** Trigger-specific synthetic user messages (one per site), plus append every prior `read()` obs to the forced-done turn's user message so the LLM has a concrete content reference.

## Architecture

Three changes ship together as one coherent commit set.

### 1. Empty-list dedup (tactical, ~10 lines in `loop.py`)

In the `list_interactive` auto-advance branch (`loop.py:296-342`), short-circuit when the served string is the empty array literal:

```python
if served_str.strip() == "[]":
    self._hidden_tools.add("list_interactive")
    obs_override = (
        f"(end of interactive list; tried offsets up to {served_offset} "
        f"with no new entries). Try done() or click an existing id."
    )
    args = {**args, "offset": served_offset}
    # skip the cache-walking loop entirely
```

This mirrors the existing `exhausted=True` arm. Empty array is treated as exhausted-on-arrival.

### 2. Force-done helper — new module `agent/force_done.py`

```python
async def coerce_done_via_llm(
    *,
    llm: LLMClient,
    tape: list[dict],
    goal: str,
    qa: list[tuple[str, str]],
    url: str,
    url_notes: str,
    page_header: str,
    trigger: Literal["max_steps", "no_progress", "asked_after_clarification"],
    n_no_progress: int | None = None,
    done_tool_schema: dict,
) -> dict:
    """Make one LLM call constrained to emit done(...) and return its result.

    On transport-level failure (empty tool_calls or wrong tool name) returns
    a trigger-specific placeholder string. Does not raise on those paths;
    network errors from llm.chat propagate as today.
    """
```

Internals:

1. Build messages via existing `build_messages(...)` over the input tape (preserves the system prompt, goal, qa, url notes, page header, K_RECENT replay).
2. Append a synthetic `{"role": "user"}` message containing:
   - The trigger-specific instruction line (table below).
   - A blank line.
   - `"Read content captured so far (chronological):"` followed by one bullet per `step` where `action == "read"`, in tape order. Each bullet:
     - `- read(offset={N}): "{obs[:800]}"` (truncated to 800 chars per entry).
     - If no reads in tape: `"Read content captured so far: (none — no read() calls in tape)"`.
3. Call `await llm.chat(messages, tools=[done_tool_schema], tool_choice={"type":"function","function":{"name":"done"}}, reasoning=False)`.
4. Parse the returned tool call:
   - If `tool_calls` non-empty AND `tool_calls[0].function.name == "done"` AND args parse: return `{"status": args["status"], "answer": args["answer"]}`.
   - Otherwise return trigger-specific placeholder: `{"status": "failed", "answer": <_PLACEHOLDERS[trigger]>}`.

### 3. Three call sites in `loop.py` → one helper

**(3a) Max-step boundary.** Replace lines 220-224 (`is_final_step` tools-filter) AND line 528 (`"max steps"` fallback). New flow at the *top* of the `step_idx == max_steps - 1` iteration:

```python
if step_idx == self.max_steps - 1:
    result = await coerce_done_via_llm(
        llm=self.llm,
        tape=self.tape,
        goal=goal,
        qa=list(self.qa),
        url=self._current_url(),
        url_notes=url_notes,
        page_header=self._page_header(),
        trigger="max_steps",
        done_tool_schema=self._done_tool_schema(),
    )
    self.trace.write({"type": "done", "payload": result})
    return result
```

The normal LLM call inside the loop body is skipped on the final step — no possibility of bypass because we never offer the unrestricted tools list.

**(3b) `NO_PROGRESS_GIVEUP` thresholds.** Two sites in `loop.py` (the read-exhausted branch around line 390, and the main-path branch around line 483). Replace `return {"status":"failed", "answer": f"stuck: no novel observation for {n} consecutive steps"}` with:

```python
result = await coerce_done_via_llm(
    ..., trigger="no_progress", n_no_progress=self.no_progress_streak,
)
self.trace.write({"type": "done", "payload": result})
return result
```

**(3c) Asked-state giveup.** Replace lines 271-277 (`name = "done"; args = {"status":"failed","answer":"stuck after user clarification"}`) with a call to `coerce_done_via_llm(..., trigger="asked_after_clarification")` and an immediate `return`. The synthetic done-call no longer flows through the normal tool-execution path; it's emitted directly via the helper.

### Trigger-specific message templates

```python
_FORCE_DONE_PROMPTS = {
    "max_steps": (
        "This is your final allowed step. Commit done() now with the best "
        "answer your prior reads support. If your reads contained the answer, "
        "use done(success, \"<answer>\"); otherwise done(failed, \"<one-line "
        "reason>\")."
    ),
    "no_progress": (
        "You have produced no novel observation for {n} consecutive steps. "
        "Either the goal is unreachable from this browser (commit "
        "done(failed, \"blocked by <wall>\") if you saw a Cloudflare/CAPTCHA/"
        "login wall, or done(failed, \"<reason>\") otherwise) or you already "
        "have the answer (commit done(success, \"<answer>\") with the "
        "rendered value from your reads — even if the page does not name the "
        "asked phrase verbatim)."
    ),
    "asked_after_clarification": (
        "User clarification did not unblock you. Commit done(failed, "
        "\"<closest answer you have>\") rather than retrying the same action."
    ),
}

_PLACEHOLDERS = {
    "max_steps": "max steps",
    "no_progress": "stuck: no novel observation for {n} consecutive steps",
    "asked_after_clarification": "stuck after user clarification",
}
```

`_PLACEHOLDERS["no_progress"]` interpolates `n_no_progress` at call time.

### Read-content dump format

```
Read content captured so far (chronological):
- read(offset=0): "Hugging Face\nModels\nDatasets\n…"
- read(offset=1600): "…ar release  16.9 GB · 128K ctx …"
- read(offset=3200): "…"
```

Per-entry truncation: 800 chars. One entry per `step.action == "read"` in chronological order. If empty: `"Read content captured so far: (none — no read() calls in tape)"`.

## Components & Data Flow

```
ReactLoop.run() iteration
  ├─ step_idx == max_steps-1?      ── yes ──▶ coerce_done_via_llm("max_steps") ──▶ return
  ├─ no_progress_streak >= 12?     ── yes ──▶ coerce_done_via_llm("no_progress") ──▶ return
  ├─ asked-state same-action?      ── yes ──▶ coerce_done_via_llm("asked_after_clarification") ──▶ return
  └─ otherwise: normal ReAct turn (unchanged)
```

`coerce_done_via_llm` is pure-ish: takes immutable inputs, returns a dict, performs no `ReactLoop` state mutation. The loop is responsible for writing the `trace.write({"type":"done", ...})` event after.

## Error Handling

| Failure                                          | Behavior                                                                              |
|--------------------------------------------------|---------------------------------------------------------------------------------------|
| `llm.chat` raises (network / HTTP error)         | Propagate as today; the loop's outer exception handler records and exits.             |
| `tool_calls` empty despite named `tool_choice`   | Return `{"status":"failed", "answer": _PLACEHOLDERS[trigger]}` (formatted).           |
| `tool_calls[0].function.name != "done"`          | Same as above. Defense-in-depth against transport-level deviation.                    |
| `done` args fail to parse                        | Same as above.                                                                         |

We never retry the forced-done call. One shot — if it fails, the placeholder ships and the run ends.

## Testing

TDD-ordered; tests written and red before each green change.

### Unit tests on `force_done.py` (mocked `LLMClient`)

1. `test_coerce_done_returns_llm_done_tool_call` — LLM stub returns `done(success, "the answer")`; helper returns `{"status":"success","answer":"the answer"}`.
2. `test_coerce_done_passes_named_tool_choice` — verifies `tool_choice={"type":"function","function":{"name":"done"}}` is what `llm.chat` was called with.
3. `test_coerce_done_includes_trigger_specific_instruction` — three sub-cases (one per trigger); each asserts the right one-liner appears in the synthetic user message.
4. `test_coerce_done_appends_read_content_dump` — tape with reads at offsets 0, 1600, 3200; assert all three appear in chronological order in the synthetic user message.
5. `test_coerce_done_handles_empty_reads` — tape with no reads → message says `"(none — no read() calls in tape)"`.
6. `test_coerce_done_truncates_long_read_obs` — read obs of 5000 chars → entry truncated to 800.
7. `test_coerce_done_falls_back_on_empty_tool_calls` — LLM returns no `tool_calls` → helper returns `{"status":"failed", "answer":"max steps"}` (or trigger-formatted equivalent).
8. `test_coerce_done_falls_back_on_wrong_tool_name` — LLM returns `read` tool_call → same fallback.
9. `test_coerce_done_no_progress_placeholder_interpolates_n` — trigger=`"no_progress"`, n=14 → fallback answer is `"stuck: no novel observation for 14 consecutive steps"`.

### Integration tests on `ReactLoop` (extending `test_loop_anti_repeat.py`)

10. `test_force_done_at_max_steps_replaces_max_steps_fallback` — `max_steps=3`, stub LLM scripts `read, read, <forced-done>`; the third call (forced-done) returns `done(success, "extracted")`. Assert final return is `{"status":"success","answer":"extracted"}` and the trace's last `done` event matches.
11. `test_force_done_on_no_progress_replaces_stuck_message` — stub browser returns identical text every turn; tape grows to 12 stale fingerprints; assert the helper was invoked with `trigger="no_progress"` and the captured fallback (or LLM-supplied) answer is the helper's output, not the legacy placeholder string.
12. `test_force_done_on_asked_after_clarification_replaces_stuck_after_user` — script `ask_user_question` then same action; assert helper invoked with `trigger="asked_after_clarification"`.

### Empty-list-dedup test (in `test_loop_anti_repeat.py`)

13. `test_list_interactive_empty_treated_as_exhausted_on_arrival` — stub registry returns `"[]"` for `list_interactive`; assert step1's obs starts with `"(end of interactive list"`, `_hidden_tools` contains `"list_interactive"` after the call, and the next step's offered tools exclude it.

## Out of Scope (NOT changing)

- `agent/wall_detect.py`, `agent/context.py` (build_messages), `agent/page_diff.py` (OffsetCache, GlobalTextCache, format_small_diff, plan_read).
- The hinted state machine (lines ~259-277 in `loop.py`).
- The bench script (`scripts/bench_webvoyager.py`).
- The system prompt `_SYSTEM` (it already contains the partial-answer + blocked-banner + final-step rules from commit `92beea7`).

## File-Level Plan

```
task2/src/agent/force_done.py            (new, ~80 lines)
task2/src/agent/loop.py                  (modified, ~50 lines net change)
task2/tests/unit/test_force_done.py      (new, ~250 lines)
task2/tests/unit/test_loop_anti_repeat.py (extended with 4 new tests)
```

No changes to `pyproject.toml` (no new deps).

## Open Questions

None outstanding. Brainstorming-locked decisions: Q1=(a), Q2=(a), Q3=(b)+extension.
