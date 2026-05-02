---
name: bench-failure-triage
description: Use when the user invokes /bench-failure-triage or asks to triage the next WebVoyager benchmark failure. Re-runs ONE failed case from the most recent bench, reads the fresh trace, and writes findings to observations.md at the repo root. One case per invocation; no auto-fixing; no iteration across cases.
---

# bench-failure-triage

## What this skill does

One pass per invocation:

1. Pick **one** failed case from the most recent WebVoyager bench run
   (`task2/data/bench/webvoyager_*.json`). If a `case_id` arg was
   passed, use that. Otherwise pick the first entry whose `status` is
   not `success`.
2. Re-run that single case via the bench script.
3. Read the fresh trace JSONL.
4. Analyze for **process errors** (loops, ineffective recoveries,
   ignored signals) and **hallucinations** (claims not grounded in any
   prior `obs`).
5. Write findings to `observations.md` at the repo root, **replacing**
   any prior content.
6. Stop. Print one line. Do not move to another case. Do not propose
   fixes. Do not commit anything.

The next invocation re-picks. If the user fixed the case and it now
passes, the latest bench JSON's first failure shifts to the next
unresolved case automatically — that's how "move to next failure"
works without explicit state.

## When NOT to use

- Multi-case analysis. This skill is single-case.
- Suggesting code fixes. Findings only; discuss fixes separately.
- Running the full WebVoyager suite. This skill targets one id.
- Auto-iterating until clean. Each invocation is one pass.

## Prerequisites

Before doing anything else:

1. Verify `task2/data/bench/` contains at least one
   `webvoyager_*.json`. If not, abort with: `No prior bench results. Run: cd task2 && uv run python scripts/bench_webvoyager.py --limit 12`

2. **Restart the agent server.** Always restart for a clean slate,
   regardless of whether it is currently running. Three Bash calls:

   a) Kill any existing process on port 8001:
      ```bash
      lsof -ti :8001 | xargs -r kill -9 2>/dev/null; sleep 1; true
      ```

   b) Start the server (use Bash with `run_in_background: true`):
      ```bash
      cd /home/pgi/v_coding_test2/task2 && set -a && . ./.env && set +a && AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001
      ```

   c) Wait for `/health` to return 200 (poll up to 60s):
      ```bash
      until curl -sf http://127.0.0.1:8001/api/sessions > /dev/null; do sleep 2; done
      ```

   If health does not come up within ~60s, abort with: `Agent server failed to start. Read the background bash output for the uvicorn log.` Do not proceed.

Run these checks via Bash before any other tool call.

## Procedure

### Step 1 — Identify the target case

Read the newest `task2/data/bench/webvoyager_*.json` (sort by
filename — they are timestamped `YYYYMMDDTHHMMSSZ`):

```bash
ls -t task2/data/bench/webvoyager_*.json | head -1
```

If the user passed a `case_id` arg (e.g. `/bench-failure-triage 109`),
use that. Otherwise pick the first `result` whose `status` is not
`"success"`.

If the user gave no arg AND every case in that file is `success`,
print: `No failures in latest bench. Nothing to triage.` Do not
overwrite `observations.md`. Stop.

### Step 2 — Run the single case

```bash
cd task2 && uv run python scripts/bench_webvoyager.py --ids <case_id>
```

Stream the output. Expect 3–5 minutes wall-clock per case. The script
writes a new `webvoyager_<timestamp>.json` to `data/bench/` and a new
trace to `data/traces/<sid>.jsonl`. Do not run this in the
background — the analysis depends on its output.

### Step 3 — Locate the new trace

Read the bench JSON the script just wrote (the newest one). It
contains the `result` for the case. The session id is **not** stored
in the bench JSON, so map case → trace by mtime: pick the newest
`task2/data/traces/*.jsonl` whose mtime is later than the bench run's
`started` timestamp.

### Step 4 — Read everything

Read in this order:

1. The matching bench result entry (status, answer, elapsed_ms,
   blocks, http if present).
2. The full trace JSONL line by line. Parse each line as JSON; the
   shape is `{type, payload, ts}`. The `step` events carry
   `payload.action`, `payload.args`, `payload.obs`, `payload.thought`.
3. `prompts/task2.md` for system-prompt context — only if a finding
   needs it (e.g. the prompt may already nudge against a behavior the
   agent ignored).

### Step 5 — Analyze

Two axes. For every finding you write, you MUST quote evidence
verbatim from the trace. No paraphrased claims.

#### Axis A: Hallucination

Did the final answer (or any intermediate `goto` URL or typed input)
introduce a fact, URL, or number not present in any prior `obs`?

Check each claim in the final answer (numbers, names, dates, URLs):
can you find that exact substring (case-insensitive) in any prior
`obs` string in the trace? If not, that's a hallucination candidate.

Discipline rule (non-negotiable): **a hallucination claim must quote
the unsupported span** in the finding. If you can't quote the
unsupported span, drop the claim.

Common shapes:
- Final answer cites a number that doesn't appear in any `obs`.
- `goto` to a URL that wasn't in any prior `obs` (the goto guard
  should have blocked it; if it didn't, that's also a finding).
- Agent grep'd for a value (e.g. `read_grep("59,513,990")`) that
  wasn't in any prior `obs` — pattern fabricated to fish for
  confirmation.

#### Axis B: Process errors

Loops:
- 3+ consecutive same-tool-with-same-args (e.g. four `goto
  https://www.bbc.com/news` in a row).
- 3+ consecutive observations with identical content despite tool
  variance (e.g. `read_grep("9/3", window=N)` doubling N, all
  `NOT FOUND`).
- Window-doubling, offset-walking with no new content surfaced.

Ineffective recoveries:
- `goto` timed out → agent retried `goto` instead of trying `read`
  (the page often partially loaded; the BBC success trace 2c666ce9
  proves `read` works after `goto` timeout).
- `Rate exceeded` body text → agent kept calling `read` instead of
  backing off or trying a different page.
- `ask_user_question` got an empty reply (batch mode) → agent
  continued as if nothing happened.

Other process errors:
- Goto to a URL that was correctly `goto_blocked` (the grounding
  guard fired) → did the agent recover via clicks? If not, finding.
- `done(success)` with an answer that doesn't match the goal.

### Step 6 — Tag severity

- **P0** — wrong result OR wrong process. Anything that prevented a
  correct outcome. Loops, hallucinations, ineffective recoveries,
  abandoned navigation are all P0.
- **P1** — latency / efficiency improvement that isn't a
  wrong-process shape. Example: one redundant `list_interactive` call
  that didn't change the outcome. Be conservative; prefer P0 when in
  doubt.
- **P2** — nice-to-have. Cosmetic, suggestion-only.

### Step 7 — Write `observations.md`

Path: `<repo_root>/observations.md` (NOT inside `task2/`).

**Replace** any prior content. Use the Write tool, not Edit.

Schema:

```markdown
# Observations: Case <id> (<web>)

**Run:** <bench JSON filename> · **Trace:** data/traces/<sid>.jsonl · **Status:** <status>
**Goal:** <goal>
**Final answer:** <answer or "—">
**Steps:** N

## Findings

### [P0] <short title>
- **Where:** step <n>, action `<tool>(<args>)`
- **Evidence:** "<quoted obs or absence-of-evidence statement>"
- **Why it's P0:** <one sentence>

### [P1] <short title>
- **Where:** ...
- **Evidence:** ...
- **Why it's P1:** ...
```

If the case **passed** this run (status = success):

```markdown
# Observations: Case <id> (<web>)

**Run:** <bench JSON filename> · **Trace:** data/traces/<sid>.jsonl · **Status:** success
**Goal:** <goal>
**Final answer:** <answer>
**Steps:** N

## RESOLVED

Case succeeded under the current code. No findings. Re-run
/bench-failure-triage to triage the next failure.
```

### Step 8 — Stop

Print one line:

```
Wrote observations.md. Case <id> is <status>. Re-run /bench-failure-triage for the next pass.
```

Do not propose fixes. Do not commit `observations.md`. Do not run
another case. Do not modify agent code.

## Edge cases

- **Bench HTTP-errors mid-run** (status `http_error`, e.g. 500):
  write what you have and add a P0 finding "bench infrastructure
  error" pointing at the bench JSON. Do not invent trace content
  that doesn't exist.
- **Trace missing** (bench JSON written but no matching trace
  file): treat as bench-infra P0; cite the bench JSON; suggest the
  user check the agent server logs.
- **Same case passed**: emit the RESOLVED template (Step 7). Still
  write `observations.md` so the user can confirm the pass.
- **`case_id` arg points to a case not in the latest bench JSON**:
  proceed anyway — the bench script accepts arbitrary case ids from
  the WebVoyager dataset.

## Discipline rules

- Quote evidence verbatim. No "the trace shows the agent…"; instead,
  `step 12: read_grep("9/3", window=2_000_000_000) → "NOT FOUND: '9/3'"`.
- A hallucination claim without a quoted unsupported span is dropped.
- P0 covers wrong result OR wrong process. Latency without
  correctness impact is P1, not P0.
- No fix recommendations in `observations.md`. Findings only.
- One case per invocation. Do not start a second case "while we're
  at it."
