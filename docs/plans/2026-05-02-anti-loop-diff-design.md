# Design: Anti-Loop Diff & Tool-Hiding for Task 2 Browser Agent

**Date:** 2026-05-02
**Status:** approved (brainstorm complete; ready for implementation plan)
**Motivating evidence:** `observations.md` — Case 113 (CanIRun.ai), trace `data/traces/c20b9a834288421fb308cd7101e530c2.jsonl`

## Problem

In the canirun.ai trace, the agent burned all 50 steps without committing to a `done(success, ...)`. Of those 50 steps, ~22 were spent on tool calls whose outcome the harness could have predicted as identical to a prior call:

- `read({offset: 0})` × 10 (byte-identical)
- `read({offset: 5600})` × 6 (byte-identical, even though the page mutated elsewhere via sort)
- `press_key(Home/End)` × 6 (no observable effect — Playwright `innerText` is scroll-independent)

The histogram line in `context.py` already surfaces these repeats as text ("Calls so far (≥2): read({...})×10"), but the agent ignored it. Soft hints aren't enough.

## Goal

Replace context-side hints with **server-side mechanical guarantees**. When the harness can predict a call's outcome is identical to a prior outcome, it must either (a) advance the agent forward automatically, or (b) remove the offending tool from the next turn's tool list.

## Scope

**In scope (this design):**
- Findings #2, #3 — `read` looping at fixed offsets.
- Finding #5 — `press_key` no-ops.
- Finding #7 (P1) — `list_interactive` redundant snapshots, since the code path is the same as `read`.
- Light coverage for `read_grep` (dedup) since the same shape is plausible.

**Out of scope (separate brainstorms):**
- Finding #1 — no commit-to-answer despite answer on screen.
- Finding #4 — sort-cycling without a stopping rule.
- Finding #6 — `[tier list]` link never clicked (planner weighting).

## Design

### Core mechanisms

**(a) Per-offset auto-advance for `read` and `list_interactive`.**

Maintain a per-tool cache `last_returned[offset] -> str` (keyed by offset only — content stored is the served slice). On `read({offset: N})`:

1. Compute `current = text[N : N + READ_LIMIT]`.
2. While `last_returned.get(N) == current` and `N < len(text)`:
   - `N += READ_LIMIT`
   - recompute `current`
   - cap at `MAX_AUTO_ADVANCE_HOPS` (32) to bound latency on pathological pages.
3. If `N >= len(text)` (or hops cap hit): return soft-nudge obs and **drop `read` from this turn's tool list**:
   ```
   (end of page; tried offsets up to M, page length L). Try read_grep or done().
   ```
4. Otherwise: serve `current`, update `last_returned[N] = current`, prepend annotation when N differs from the requested offset:
   ```
   [auto-advanced 5600→7200: 5600 unchanged since step K] <content>
   ```

`list_interactive` follows the same shape with its own cache and limit.

**(b) Global-diff tool hiding for `press_key`.**

After every action, the harness computes `global_diff = current_innerText vs previous_innerText`. If the previous step was `press_key` and `global_diff == ""`, drop `press_key` from this turn's tool list. Re-expose on the next turn where `global_diff != ""`.

**(c) Cache invalidation.**

Any non-`read` / non-`list_interactive` action that produces non-empty `global_diff` clears `last_returned` for both caches (page mutated; old offset hashes are stale). `read` / `list_interactive` calls update only the entry they serve; they never invalidate other entries.

### Small-diff context injection

When `global_diff` is non-empty and `len(diff) <= SMALL_DIFF_THRESHOLD` (default **500 chars**, total of added + removed), inject a one-block summary into the next turn's `page_header` section in `context.py`:

```
Page changes since last turn (+12 / -3 lines):
  + "Sort: Params ↓"
  - "Sort: Score"
```

- Format: `difflib.unified_diff` lines, capped at 10 displayed lines.
- Above threshold: no injection (would be noisier than helpful; agent can `read` if it wants).
- Below threshold but non-empty: always inject — this is what saves a `read` step when the change is small.

### `read_grep` dedup

Repeat call with the same pattern against an unchanged page:
```
(pattern "RUNS GREAT" already searched at step K, no new matches.)
```
No tool hiding. Pattern is agent-supplied; pre-emptive hiding would be over-reach.

### Re-exposure & invariants

- `read` is re-exposed on the next turn where `global_diff != ""` (i.e., any successful click/type/select/goto/back).
- `press_key` is re-exposed under the same condition.
- Auto-advance annotations are always shown to the agent so its mental model stays correct.
- **Liveness invariant:** the agent always has at least one progress-making tool. Even with `read` and `press_key` both hidden, the agent retains `goto`, `back`, `click`, `type`, `select_option`, `read_grep`, `list_interactive`, `note`, `done`. Hiding can never make the tool list empty of state-changing options.

### Tool coverage matrix

| Tool | Rule | Why |
|---|---|---|
| `read` | per-offset auto-advance + end-of-page hide | findings #2, #3 |
| `list_interactive` | per-offset auto-advance | finding #7; same code path as `read` |
| `press_key` | global-diff hide | finding #5 |
| `read_grep` | dedup-only (synthetic obs, no hide) | pattern is agent-supplied |
| `goto`, `back`, `click`, `type`, `select_option` | no rule | these are the tools that *cause* state change — they're how the agent escapes any hide |

## Configuration knobs

Added to `agent.config` (or equivalent):

| Knob | Default | Purpose |
|---|---|---|
| `READ_LIMIT` | 1600 (existing) | size of `read` slice |
| `SMALL_DIFF_THRESHOLD` | 500 | char cutoff for context injection |
| `MAX_AUTO_ADVANCE_HOPS` | 32 | bound on a single `read` call's auto-advance loop |
| `DIFF_INJECT_MAX_LINES` | 10 | cap on lines shown in injected diff |

## Testing plan (TDD)

Failing tests must be written and red before implementation. Order:

**Unit tests (new file: `tests/unit/test_loop_anti_repeat.py` or split):**
1. `test_read_auto_advance_on_repeat` — same offset, identical content → returns offset+READ_LIMIT.
2. `test_read_auto_advance_walks_to_changed_window` — multi-hop until content differs.
3. `test_read_end_of_page_hides_tool` — past `len(text)`, returns nudge AND tool registry omits `read`.
4. `test_read_reexposed_after_state_change` — click that mutates page → `read` back in tool list.
5. `test_press_key_hidden_when_no_global_diff` — after a press_key with empty diff, next turn excludes press_key.
6. `test_press_key_reexposed_after_page_mutation` — successful click/select restores press_key.
7. `test_small_diff_injected_into_page_header` — diff ≤500 chars → formatted diff appears in context user message.
8. `test_large_diff_not_injected` — diff >500 chars → no injection.
9. `test_list_interactive_auto_advance` — same auto-advance behavior as `read`.
10. `test_read_grep_dedup_on_repeat` — same pattern + same page → synthetic "already searched" obs.
11. `test_cache_invalidated_after_page_mutation` — after a successful click that changes innerText, the offset cache is cleared so the next `read` at a previously-seen offset returns content normally (not auto-advanced).

**Eval / integration tests:**
12. `tests/evals/test_canirun_no_loop.py` — replay or simulate the canirun.ai goal; passing means the agent reaches `done(success, ...)` within budget instead of looping. May start as `xfail` if it's flaky; iterate until reliable.
13. Existing `tests/evals/test_simple_search.py` must still pass (regression).
14. Existing integration tests under `tests/integration/` must still pass (no behavior change for non-looping cases).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Auto-advance hides genuinely-useful re-reads (agent wanting to verify a value) | Agent has `read_grep` for verification; auto-advance annotation makes the bypass visible. |
| Page mutates in a way exact string compare misses (whitespace flicker) | Cache invalidation on any non-empty global_diff is conservative — over-invalidates rather than under-invalidates. |
| Small-diff threshold too aggressive → agent stops calling `read` and misses information off-screen | Threshold is configurable; injected diff includes a hint that it's a *summary*, not the full page state. Agent can still call `read` if exposed. |
| Auto-advance loop on a pathological page | `MAX_AUTO_ADVANCE_HOPS` cap. |

## Non-goals worth stating explicitly

- This design does **not** add a stopping rule for "you have enough information; commit to `done()`." That's finding #1 and a separate brainstorm.
- This design does **not** change the planner / system prompt to weight goal-shaped link names. That's finding #6.
- This design does **not** introduce a new tool. It only refines the obs and tool-list policy around existing tools.
