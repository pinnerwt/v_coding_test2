# `bench-failure-triage` Skill — Design

## Problem

WebVoyager Tier1 cases fail in distinct ways: hallucinated final answers,
runaway tool-call loops, ineffective recovery from transient errors,
abandoned navigation. Reading each trace by hand to spot which class a
failure falls into is slow and easy to skip. We want a tight loop:
re-run one failed case, read its new trace, write down what went wrong,
fix, repeat — without the human doing the trace-reading triage every
time.

## Goal

Per invocation: pick one failed case, re-run only it, analyze the
fresh trace, and write findings to `observations.md` at the repo root
(replacing any prior file). One case per invocation; the skill does not
move on by itself. No auto-fixing.

## Scope

In:
- A project-level Claude Code skill at
  `.claude/skills/bench-failure-triage/SKILL.md`.
- Reads the most-recent `task2/data/bench/webvoyager_*.json` to pick
  the next failed case (or accepts a `case_id` arg).
- Runs `uv run python scripts/bench_webvoyager.py --ids <case_id>`
  from `task2/`.
- Reads the resulting trace JSONL from `task2/data/traces/`.
- Reads `prompts/task2.md` for system-prompt context.
- Writes `observations.md` at repo root, replacing prior content.

Out:
- Multi-case batching, autonomous iteration across cases.
- Auto-fixing, dispatching code-fix subagents, opening PRs.
- Auto-starting the agent server (could clash with a manual one).
- Long-running history file. Git is the history mechanism if the
  user chooses to commit `observations.md`.

## Architecture

A single SKILL.md that documents the procedure for Claude (the agent
invoking the skill) to follow. There is no separate detector binary
or analysis library — Claude reads the trace and judges, anchored by
discipline rules in the SKILL document.

### Flow

1. **Pre-check.** Confirm `task2/data/bench/` has at least one prior
   bench JSON and that `http://127.0.0.1:8001/health` returns 200.
   Abort with a specific message on either failure.
2. **Pick case.** Use the `case_id` arg if provided; otherwise read
   the newest `task2/data/bench/webvoyager_*.json`, pick the first
   entry whose `status` is not `success`.
3. **Run.** `cd task2 && uv run python scripts/bench_webvoyager.py
   --ids <case_id>`. Stream output. Expect 3–5 minutes per case.
4. **Locate the new trace.** Read the just-written bench JSON; map
   the case id to its session id (if recorded) or fall back to the
   newest `task2/data/traces/*.jsonl` modified after the run started.
5. **Read.** The full trace JSONL, the matching bench result entry,
   and `prompts/task2.md` for system-prompt context.
6. **Analyze (Claude reads, no detector code).** Two axes:
   - **Hallucination:** does the final answer (or any intermediate
     `goto` URL or typed input) introduce a fact, URL, or number not
     present in any prior `obs` string? Discipline rule: a
     hallucination claim must quote the unsupported span and confirm
     the missing-evidence in writing. If the analyst can't quote it,
     the claim is dropped.
   - **Process errors:** repeated identical or near-identical tool
     calls (e.g. `read_grep("9/3", window=N)` doubling N), ignoring
     `NOT FOUND` / `Rate exceeded` repeatedly, retrying timed-out
     `goto` without trying `read`, abandoned recoveries,
     `ask_user_question` with empty reply followed by "continue
     anyway." A loop is 3+ consecutive same-tool-same-args, or 3+
     consecutive observations with the same content despite tool
     variance.
7. **Tag severity.**
   - **P0** — wrong result or wrong process (covers all loops,
     hallucinations, ineffective recoveries — anything that prevents
     a correct outcome).
   - **P1** — latency / efficiency improvement that isn't a
     wrong-process shape (e.g. one extra `list_interactive` that
     wasn't needed).
   - **P2** — nice-to-have.
8. **Write `observations.md`** at the repo root, replacing prior
   content. Schema below.
9. **Stop.** Print one line: "Wrote observations.md. Case <id> is
   <status>. Re-run /bench-failure-triage for the next pass." No
   autonomy beyond this.

### `observations.md` schema

```markdown
# Observations: Case <id> (<web>)

**Run:** <bench JSON filename> · **Trace:** data/traces/<sid>.jsonl · **Status:** <status>
**Goal:** <goal>
**Final answer:** <answer or "—">
**Steps:** N

## Findings

### [P0] <short title>
- **Where:** step <n>, action `<tool>(<args>)`
- **Evidence:** "<quoted obs or absence-of-evidence>"
- **Why it's P0:** <one sentence>

### [P1] <short title>
…
```

If the case passed this run: a single section "RESOLVED — case
succeeded under previous fix" with the answer; no P0/P1 findings.

## Edge cases

- **Bench HTTP-errors mid-run** (e.g. status 500, 502): write what we
  have and add a P0 finding "bench infrastructure" pointing at the
  bench JSON. Do not invent trace content.
- **Server not running**: abort. Tell user the exact command:
  `cd task2 && AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app --port 8001`.
- **No prior bench JSON**: abort. Tell user to run a multi-case bench
  first (`scripts/bench_webvoyager.py --limit 12`).
- **All prior cases passed** (no failure to triage): print "no
  failures in latest bench" and stop without overwriting
  `observations.md`.
- **Trace missing** (run wrote bench JSON but no trace file): treat
  as bench-infra P0; cite the bench JSON.

## Discipline rules (encoded in SKILL.md)

- Quote evidence verbatim. No paraphrased "the trace shows…"
- A hallucination claim without a quoted unsupported span is dropped.
- Severity is wrong-result-or-process for P0, not "I think this is
  important." Latency without correctness impact is P1.
- Don't recommend fixes in `observations.md`. Recommendations are a
  separate conversation.
- Don't move on to another case. One case per invocation.

## Testing

This is a procedure document, not code. Validation is empirical:
run the skill against the current latest bench result, confirm
`observations.md` is generated with at least one P0 finding for case
112 (Wolfram, known runaway loop), and confirm the report cites
specific step numbers and quotes from the trace.

## Out of scope

- A `--diff` mode that compares this pass's findings to a previous
  pass.
- Auto-committing `observations.md`.
- Severity escalation rules (e.g. "P1 becomes P0 after 3 passes").
- Cross-case pattern detection ("same loop appears in 3 cases →
  raise to top of report").
