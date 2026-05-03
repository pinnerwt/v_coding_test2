# Narrative History + Mandatory Reason — Design

**Date:** 2026-05-03
**Scope:** Task 2 (Generalized Browser Automation Agent)
**Motivation:** The AX-tree + plateau-interrupt PR (just landed) revealed that no code-level
stuck-detector can reliably tell when the agent is stuck. Case 107 evidence: at step 19 the
agent's obs already contained `"Downloads last month\n59,513,990"` — the exact answer — and the
LLM walked past it for 30 more steps. Byte-equality, near-duplicate detection, and
`(url, action_type)` triple tracking are all whack-a-mole. The agent itself is the only entity
that can recognise "I have the answer, I should call done." So we make the agent write its
own memory and stop fighting it in code.

## Goals

1. Make every action carry a verbalised intent — turn the agent's reasoning into a first-class,
   mandatory side-effect of every tool call.
2. Replace the (thought, action, args, obs) tape-as-prompt-history with a compact unbounded
   `(url, action, reason)` narrative the agent itself authors.
3. Remove all code-level stuck detection. The LLM is the judge.

## Non-goals

- Replacing the AX-tree snapshot enrichment (keep — it works).
- Adding new heuristics. The point is to *remove* them.
- Compressing or summarising the narrative. It stays raw and verbatim. Context-window
  pressure is a future problem; not now.

## Architecture

Three layers, shipped as one PR. The plateau machinery shipped in the prior PR gets reverted
on this branch first, so AX-tree + narrative-history land together as one cohesive PR.

### Layer 1 — Mandatory `reason` on every tool

All 12 tools — `goto`, `back`, `read`, `read_grep`, `list_interactive`, `click`, `type`,
`select_option`, `press_key`, `reason`, `ask_user_question`, `done` — accept a required
`reason: string` argument in their JSON schemas. The system prompt enforces structure:

> The `reason` field MUST capture: (a) what the most recent observation showed
> relative to the goal, and (b) what you expect this action to accomplish, and (c) why
> this over alternatives. 1–3 short sentences.

The existing optional `thought` field is removed — single mandatory `reason` is simpler and
forces the discipline.

The standalone `reason` tool (no-action reflection) stays. Agents can still use it to think
without acting, e.g. when synthesising an answer from accumulated obs.

### Layer 2 — Unbounded `(url, action, reason)` narrative

A new history surface fed to the LLM each turn, in the **system prompt** as a `## Action history`
section. Unbounded — every step since session start.

Format per entry: one line:

```
step <N> | <url> | <action_call> | <reason>
```

Where `action_call` is `name(arg1=v1, arg2=v2)` for the most-discriminating args (e.g.
`click(id=84)`, `goto(url="https://...")`, `read_grep("download", window=300)`); URL is
the page URL at the moment of action issue (i.e., before the action ran).

Token budget: ~150 chars × 50 steps = 7.5KB. Manageable in any modern context.

### Layer 3 — Last 3 observations, raw

In the **user message**, a `## Recent observations (last 3)` section: the last 3 obs
strings, verbatim, separated by `---`. No surrounding `(action, args)` wrapper — just the
raw text. The most recent obs is last.

The existing K_RECENT=8 assistant+tool message pair rendering is **removed**. There is no
tool-call/tool-result interleaving in the conversation history any more. The LLM sees:

```
system: <instructions> + ## Action history (full session, narrative)
user:   Goal + URL notes + Current URL + ## Recent observations (last 3 raw obs)
```

…and responds with a single tool call. Crisp and linear.

### Goto-grounding

Kept, but the allowlist source widens. Currently: URLs found in any prior obs string.
New: URLs found in:

1. Any prior action's args (e.g., `goto(url="...")`).
2. Any prior reason text.
3. Any prior history entry's URL (places the agent has been).

The recent-3 obs window is too narrow to ground new navigations against, so we lift the
check up to the narrative + reasons. The agent's own writing becomes the source of truth
for "URLs we know about."

## Removals

All of these go away in this PR:

- `K_RECENT = 8` constant + the older/recent split in `build_messages`
- `NOVELTY_WINDOW = 8` and `_novelty_line`
- `_obs_fingerprint`, `_histogram_line`, `_short`, `_call_str` helpers
- `NO_PROGRESS_GIVEUP = 9` and the coerce-done-on-no-progress branch
- `PLATEAU_INTERRUPT = 4`, `_PLATEAU_INTERRUPT_HINT`, `_plateau_interrupt_pending`
- Tool-list restriction wrapper in `loop.py::run`
- `_last_three_match()`, `_no_progress()`, `_step_key`
- The hinted-redirect block at `loop.py:379-395` (state == "hinted")
- `no_progress_streak` field on the loop
- `state` machinery (the FSM tracking "none"/"hinted"/"asked")
- `plateau_interrupt` kwarg on `build_messages`
- Existing assistant/tool message pair rendering for the recent tape (replaced by
  Layer 3's raw-obs section)
- Associated tests: `test_loop_plateau_interrupt.py`, `test_loop_no_progress.py`,
  `test_loop_stuck.py`, parts of `test_loop_anti_repeat.py` that exercise hinted-redirect
  and streak counters.

`max_steps` (caller-supplied, defaults ~25–30) is the only code-level safety net.

## Tape vs context

The tape (in-memory list of step records) **stays** as the substrate. We keep writing the
full record (url, action, args, reason, obs) to the trace JSONL for debugging — trace is for
humans, context is for the LLM. The change is purely in what `build_messages` renders.

Tape entry shape changes from:

```python
{"thought": str, "action": str, "args": dict, "obs": str}
```

to:

```python
{"url": str, "action": str, "args": dict, "reason": str, "obs": str}
```

(thought removed; url and reason added.)

## Branch strategy

Revert the 6 plateau commits on `task2-web-agent` *before* starting the new work.
Reverted commits (in reverse order):

- `6d930b7` fix(task2): plateau pending clears on novel obs
- `87d3fee` feat(task2): inject plateau interrupt hint
- `1e9b91a` feat(task2): reason clears plateau pending
- `7f1b511` docs(task2): explain hinted-redirect gate
- `1d7b61e` feat(task2): restrict tool set at plateau
- `eea03ec` feat(task2): introduce PLATEAU_INTERRUPT constant

The hinted-redirect block predates plateau and is removed as part of the new work
(it's part of "all stuck-detection out").

The AX-tree snapshot commits (`8bb9cca` through `b0958b2`) are preserved — that work
stands.

## Tests (TDD red-first)

**Schema:**

- `test_every_tool_schema_requires_reason` — iterate registry, assert every tool's
  parameters JSON has `reason` in `properties` AND in `required`.
- `test_tool_call_without_reason_produces_error_obs` — synthetic LLM tool call with
  args missing `reason` → loop emits an error obs and continues (no crash).

**Tape shape:**

- `test_tape_entry_records_url_at_action_time` — drive 2 steps; assert tape[0].url
  is the URL when step 0's action was issued, tape[1].url is the URL at step 1's
  issue (which may differ if step 0 navigated).
- `test_tape_entry_records_reason_verbatim` — assert tape[i].reason == the reason
  the LLM passed in args.

**Narrative rendering:**

- `test_build_messages_renders_full_narrative_in_system` — drive 5 steps, assert
  the system message content contains 5 narrative lines in order, each formatted
  `step <N> | <url> | <action_call> | <reason>`.
- `test_build_messages_narrative_unbounded` — drive 25 steps, assert all 25 lines
  present.

**Recent obs:**

- `test_build_messages_includes_last_three_obs_raw` — drive 5 steps with distinct
  obs strings; assert user message contains exactly the last 3 obs strings,
  separated by `---`, no other wrapper text.
- `test_build_messages_obs_fewer_than_three_when_tape_short` — drive 2 steps;
  assert user message has 2 obs, not 3.
- `test_build_messages_no_assistant_tool_pair_messages` — assert the message list
  has exactly 2 messages (system + user), not (system + user + 2N for the tape).

**Goto-grounding:**

- `test_goto_allowed_from_prior_reason` — feed a tape where step 0's reason mentions
  `https://example.com/foo`; assert step 1's `goto("https://example.com/foo")` is
  allowed.
- `test_goto_allowed_from_prior_action_args` — feed a tape where step 0 was
  `goto("https://example.com/bar")`; assert step 1's `goto("https://example.com/bar")`
  is allowed.
- `test_goto_blocked_when_url_in_no_prior_reason_or_action` — assert URL absent from
  all of {history urls, action args, reason text} → blocked.

**Behavioural:**

- `test_max_steps_is_only_backstop` — drive a session with no `done`; assert
  termination at exactly `max_steps` LLM calls.
- `test_done_short_circuits` — at any step LLM picks `done(success, ...)`; run ends.

**Removals (regression):**

- `test_loop_plateau_interrupt.py` deleted.
- `test_loop_no_progress.py` deleted.
- `test_loop_stuck.py` deleted.
- `test_loop_anti_repeat.py` — keep behavioural assertions about `done` and
  `ask_user_question` paths, delete sections that exercise streak/hinted/plateau.
- Existing tests asserting on K_RECENT or NOVELTY_WINDOW deleted.

## Verification

After implementation:

1. `uv run pytest -x` — all green. Net test count drops; expected.
2. `uv run ruff check .` — clean.
3. Re-run the full bench: `uv run python scripts/bench_webvoyager.py --limit 12`.
   Bar: match or exceed prior 9/12 baseline. Expectation: case 107 flips to
   success because step 19's reason ("found 59,513,990") survives in the narrative
   into step 20+, where the agent can call `done`.

## Out of scope

- **Narrative compression.** Long sessions might eventually hit context limits.
  Add a rolling summary or LLM-based compaction only when measured to be a problem.
- **Reason-quality enforcement beyond prompt.** Tempting to add a regex/length
  validator for the reason field, but that's exactly the whack-a-mole pattern we're
  moving away from. Trust the LLM; if reasons degenerate, fix the system prompt.
- **Bench-level eval of reason quality.** Worth doing in a follow-up: sample
  reasons across a bench run, sanity-check they capture obs faithfully.
