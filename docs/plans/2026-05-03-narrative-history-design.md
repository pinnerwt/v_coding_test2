# Narrative History + Mandatory Reason — Design

**Date:** 2026-05-03
**Scope:** Task 2 (Generalized Browser Automation Agent)
**Motivation:** The AX-tree + plateau-interrupt PR (just landed) revealed that no code-level
stuck-detector can reliably tell when the agent is stuck. Case 107 evidence: at step 19 the
agent's obs already contained `"Downloads last month\n59,513,990"` — the exact answer — and the
LLM walked past it for 30 more steps. Byte-equality, near-duplicate detection, and
`(url, action_type)` triple tracking are all whack-a-mole. The agent itself is the only entity
that can recognise "I have the answer, I should call done."

## Goals

1. Make every action carry a verbalised intent — turn the agent's reasoning into a first-class,
   mandatory side-effect of every tool call.
2. Give the agent an unbounded narrative trail of its own actions and intents, so it can
   recognise loops/progress/blockers from its own writing.
3. Remove all code-level stuck detection. The LLM is the judge.

## Non-goals

- Replacing the AX-tree snapshot enrichment (keep — it works).
- Adding new heuristics. The point is to *remove* them.
- Compressing or summarising the narrative. It stays raw and verbatim until it doesn't fit;
  context-window pressure is a future problem to solve, not now.

## Architecture

Two layers, shipped as one PR. The plateau machinery shipped in the prior PR gets reverted
on this branch before the new work goes in, so AX-tree + narrative-history land together as
one coherent surface change.

### Layer 1 — Mandatory `reason` on every tool

All tools — `goto`, `click`, `type`, `read`, `read_grep`, `list_interactive`, `back`,
`ask_user_question`, `done`, `reason` itself — accept a required `reason: string` argument
in their JSON schemas. The agent must articulate why it is taking this action: what just
happened, what it expects this action to do, why this over alternatives. The system prompt
enforces the structure ("1–2 short sentences, grounded in the current obs").

The existing optional `thought` field is removed. Agents won't need both; a single mandatory
reason is simpler and forces the discipline.

The standalone `reason` tool (no-action reflection) stays. Agents can still use it to think
without acting, e.g. when synthesising an answer from accumulated obs.

### Layer 2 — Unbounded `(url, action, reason)` narrative + 5-step obs window

Two history surfaces fed to the LLM each turn:

- **Compact narrative** (new): unbounded list of `(url, action, reason)` tuples spanning the
  full session. URL is the page URL at the moment of the action; action is the tool name
  plus the most-discriminating arg (e.g. `click(id=84)`, `goto("https://…")`,
  `read_grep("download")`); reason is the verbatim string the agent wrote. Roughly 100–200
  chars per entry. A 50-step session weighs in at 5–10KB — manageable in any modern context.

- **Recent obs window** (existing, narrowed): the last **5** steps' full
  `(thought, action, args, obs)` shape. Down from 8. The narrative covers older steps; the
  agent doesn't need to scroll back through old raw obs since it has its own summary.

The narrative goes into the system prompt as a "## Action history" section between the goal
and the recent-tape block. Format: one entry per line, JSONL-style or plain
`step N | url | action | reason` — pick whichever the LLM parses better in eyeball-testing.

### Goto-grounding guard

Unchanged. Still checks the recent obs window for the URL. The window dropping from 8 to 5
narrows the lookback slightly; in practice URLs the agent navigated to recently are still
covered, and AX-tree exposes hrefs of clickable links on the current page so re-discovery
is cheap if needed.

## Removals

All of these go away with this PR:

- `PLATEAU_INTERRUPT = 4` constant + `_plateau_interrupt_pending` flag
- `_PLATEAU_INTERRUPT_HINT` and the `plateau_interrupt` kwarg on `build_messages`
- The tool-list restriction wrapper at the top of `loop.py::run`
- `_last_three_match()` helper
- `NOVELTY_WINDOW = 8` and `_no_progress()` helper
- `NO_PROGRESS_GIVEUP = 9` and the coerce-done-on-no-progress branch
- The hinted-redirect machinery (`state == "hinted"` block)
- `no_progress_streak` field on the loop
- `_obs_fingerprint` helper (unless used elsewhere — verify)

The tape itself stays — it's the substrate for the recent-5 obs window. Just no streak
counters or fingerprint comparisons over it.

`max_steps` (caller-supplied, defaults to ~25–30) is the only code-level safety net.

## Branch strategy

Revert the 6 plateau commits on `task2-web-agent` *before* starting the new work, so the
final PR contains: AX-tree commits (kept) + narrative-history commits (new). Plateau never
reaches `main`. The reverted commits are recoverable from reflog if we ever change our
minds.

Commits to revert (in reverse order):
- `6d930b7` fix(task2): plateau pending clears on novel obs
- `87d3fee` feat(task2): inject plateau interrupt hint
- `1e9b91a` feat(task2): reason clears plateau pending
- `7f1b511` docs(task2): explain hinted-redirect gate
- `1d7b61e` feat(task2): restrict tool set at plateau
- `eea03ec` feat(task2): introduce PLATEAU_INTERRUPT constant

The hinted-redirect block at `loop.py:379-395` predates plateau and won't be touched by the
revert; it gets removed as part of the new work (it's part of the "all stuck-detection out"
cleanup).

## Tests (TDD red-first)

**Schema:**

- `test_every_tool_schema_requires_reason` — iterate the registry, assert every tool's
  JSON schema lists `reason` in `required`.
- `test_tool_call_without_reason_rejected` — a synthetic LLM response with a tool call
  missing `reason` is treated the same as `ToolNameNotAllowed` today (error obs, retry).

**Narrative:**

- `test_narrative_history_records_url_action_reason` — drive 3 steps, assert the narrative
  passed to `build_messages` has 3 entries with the right URLs, actions, reasons.
- `test_narrative_history_unbounded` — drive 20 steps, assert all 20 narrative entries are
  present (no window).
- `test_recent_obs_window_is_5` — drive 10 steps, assert the obs-bearing tape segment in
  the system prompt has exactly the last 5 steps.

**Removals (regression):**

- Existing tests for plateau (`test_loop_plateau_interrupt.py`) deleted.
- Existing tests for `NO_PROGRESS_GIVEUP` (`test_loop_no_progress.py`) deleted.
- Existing tests for stuck-detector at 9 (`test_loop_stuck.py`) deleted.
- Tests for hinted-redirect (search for `state == "hinted"`) deleted.
- Tests that *use* the streak counter as a fixture knob updated to drive max_steps instead.

**Behavioural:**

- `test_max_steps_is_only_backstop` — drive a session with no `done`, assert termination at
  exactly `max_steps` LLM calls.
- `test_done_short_circuits` — at any step the LLM picks `done(success, ...)`, the run ends
  immediately regardless of step count.

## Verification

After implementation:

1. `uv run pytest` — all green. Net test count drops (deleted plateau/no-progress/stuck
   suites) — that's expected.
2. `uv run ruff check .` — clean.
3. Re-run the full bench: `uv run python scripts/bench_webvoyager.py --limit 12`. The bar:
   match or exceed the prior 9/12 baseline. Expectation: case 107 specifically should flip
   to success because the agent's narrative will have written `"saw 59,513,990"` in step
   19's reason and step 20's reason can refer to that and call `done`.

## Out of scope

- **Narrative compression.** Long sessions might eventually hit context limits. Add a
  rolling summary or LLM-based compaction only when measured to be a problem.
- **Reason-quality enforcement beyond prompt.** Tempting to add a regex/length validator for
  the reason field, but that's exactly the whack-a-mole pattern we're moving away from.
  Trust the LLM; if reasons degenerate, fix the system prompt.
- **Per-tool reason field rename.** Some tools currently use `thought` as the field name in
  their `args`. The `reason` rename happens uniformly across all tools in this PR.
