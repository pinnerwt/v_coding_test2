---
name: bench-sweep
description: Use when the user invokes /bench-sweep or asks to run the full WebVoyager benchmark (cases 101–113), record success rate / token usage / latency to history, and write a delta-vs-last-run observations report. Restarts the agent server, runs all 13 cases at concurrency 5 (matched to the server's run_loop semaphore), appends one row to bench/metrics_history.jsonl, then re-writes observations.md with fail-case findings (loops, hallucination, tool misuse, read-overuse) and metric deltas.
---

# bench-sweep

## What this skill does

One invocation = one full sweep:

1. Restart the agent server (clean slate).
2. Run **all** 13 WebVoyager cases (`101..113`) in one bench process.
3. Aggregate per-case + total: success rate, latency, token usage
   (input cached/uncached, output, calls).
4. Append one JSON row to `task2/data/bench/metrics_history.jsonl`
   (the canonical machine-readable history; a downstream Python script
   plots its evolution as line charts).
5. Analyze every non-success case in this run for: trace loops,
   hallucinations, tool misuse, `read()` overuse.
6. Compute deltas vs the previous row in `metrics_history.jsonl`.
7. **Delete** any existing `observations.md` at repo root, then write
   a fresh one with the metrics summary, deltas, and fail-case
   findings.
8. Stop. Print one line. No fixes. No commits. No code changes.

## When NOT to use

- Single-case triage. Use `/bench-failure-triage` for that.
- Partial runs / smoke tests. This skill always sweeps 101–113.
- Anything that mutates agent code or commits files.
- "Just give me numbers without restarting." The restart is part of
  the contract — token-cache state must be reset for the cached/
  uncached split to be comparable across runs.

## Prerequisites

Same as `/bench-failure-triage`'s server-restart preamble. Reuse it
verbatim — do not invent a different startup sequence.

In summary (full detail is in `bench-failure-triage` SKILL.md if any
step is unclear):

1. Kill anything on port 8001 (best-effort; skip if denied).
2. Start uvicorn in background with env loaded from `task2/.env` and
   `AGENT_RESTRICT_GOTO=true`.
3. Wait until `:8001` is listening (`ss -ltn`, up to 60s).
4. Sanity-check `DEEPSEEK_API_KEY` reached the uvicorn process via
   `/proc/$PID/environ`.

Do **not** trust `/api/llm_health` as readiness — it 401s on
DeepSeek's `/models` even when chat works. Reachable HTTP on 8001 is
the readiness signal.

If the server cannot be started AND nothing is listening on 8001,
abort: `No agent server reachable on 127.0.0.1:8001. Aborting sweep.`

## Procedure

### Step 1 — Snapshot the previous metrics row

Before running anything that takes minutes, read the last line of
`task2/data/bench/metrics_history.jsonl` if it exists. Keep that JSON
in working memory — you'll diff against it in Step 6. If the file
doesn't exist, the previous row is `null` and deltas are reported
as `n/a (first run)`.

### Step 2 — Run the full sweep

```bash
cd task2 && uv run python scripts/bench_webvoyager.py \
  --ids 101,102,103,104,105,106,107,108,109,110,111,112,113 \
  --concurrency 5 \
  --timeout 600
```

The server's session semaphore is `asyncio.Semaphore(5)`
(`src/agent/server.py`), so concurrency=5 is the matched ceiling —
higher values just queue inside the server. Stream output. Expect
~10–15 min wall at concurrency=5 (down from ~30–60 min serial). The
script writes one timestamped `data/bench/webvoyager_*.json` and 13
traces (`data/traces/<sid>.jsonl` + `<sid>.llm.jsonl` sidecars).

If the bench process itself errors (non-zero exit AND no output JSON),
write `observations.md` with a single P0 finding "bench infra error"
pointing at the stderr; do not append a row to
`metrics_history.jsonl`. Stop.

### Step 3 — Map cases to traces

Read the new bench JSON (newest `data/bench/webvoyager_*.json`). Each
result now carries `sid` directly (server returns it in
`/api/run_sync` body — see `src/agent/server.py` `run_loop`):

```python
trace_path = Path(f"task2/data/traces/{result['sid']}.jsonl")
sidecar_path = Path(f"task2/data/traces/{result['sid']}.llm.jsonl")
```

This mapping is unambiguous under parallel runs (mtime order is not,
because cases overlap). If a result lacks `sid` (older bench JSON, or
the case errored before reaching the server's response path), fall
back to the legacy mtime-after-`T0` mapping but mark the case as
`(sid: legacy-mtime)` in observations.md so the user knows the
mapping was a guess.

If a result has `sid` but the trace file does not exist, that's a P0
finding ("missing trace for case X, sid Y").

### Step 4 — Aggregate metrics

For each `(case_id, sid)` pair:

- **latency_ms** — from the bench JSON entry's `elapsed_ms`.
- **token usage** — sum across all records in
  `data/traces/<sid>.llm.jsonl`:
  - `input_cached`  = Σ `usage.prompt_cache_hit_tokens`
  - `input_uncached` = Σ `usage.prompt_cache_miss_tokens`
  - `input_total` = Σ `usage.prompt_tokens` (sanity: should equal
    cached + uncached when both fields are present)
  - `output` = Σ `usage.completion_tokens`
  - `calls` = number of records

Aggregate totals across all 13 cases. Also compute:

- `success_rate` = n_success / n_total
- `latency.avg_ms` = mean of per-case `elapsed_ms`
- `latency.p50_ms`, `latency.p95_ms` — percentiles over the 13 cases

Use a one-shot Python heredoc; do not write a permanent script.
Reuse `scripts/cost_report.py`'s sidecar parser only as reference —
don't import it.

### Step 5 — Append to metrics history

File: `task2/data/bench/metrics_history.jsonl`.

Append **one** line — a single JSON object — with this schema:

```json
{
  "ts": "<bench JSON 'started' field, ISO-8601 UTC>",
  "bench_file": "webvoyager_<...>.json",
  "case_ids": ["101", "...", "113"],
  "n_total": 13,
  "n_success": 11,
  "success_rate": 0.846,
  "latency_ms": {"avg": 95000, "p50": 90000, "p95": 200000, "sum": 1234567},
  "tokens": {
    "input_total": 123456,
    "input_cached": 98765,
    "input_uncached": 24691,
    "output": 6789,
    "calls": 234
  },
  "per_case": [
    {"id": "101", "status": "success", "elapsed_ms": 90123,
     "input_cached": 1000, "input_uncached": 500, "output": 200, "calls": 15}
  ]
}
```

This file is the contract with the downstream charting script.
Schema-stability matters more than prettiness. Do NOT pretty-print
(one line per run keeps `tail -n 30` cheap). Do NOT rewrite history.
Append-only.

### Step 6 — Compute deltas vs previous row

For each metric in the new row that also exists in the previous row,
compute absolute and percent delta:

- `success_rate`
- `latency_ms.avg`, `latency_ms.p95`
- `tokens.input_cached`, `tokens.input_uncached`, `tokens.output`,
  `tokens.calls`

Format: `tokens.output: 6,789 (Δ +812, +13.6%)`. Round percent to one
decimal. Use `(Δ n/a)` when the previous row lacks the field. Use
`Δ n/a (first run)` for every field if there is no previous row.

### Step 7 — Per-fail analysis

For every case in this sweep with `status != "success"`, read its
trace and apply these checks. **Quote evidence verbatim** — same
discipline as `bench-failure-triage`.

| Check | Trigger | Severity hint |
|---|---|---|
| **Loop** | 3+ consecutive same-tool-with-same-args, OR 3+ consecutive identical `obs` strings, OR window-doubling `read_grep` with no new content | P0 |
| **Hallucination** | Final answer / `goto` URL / `read_grep` pattern contains a substring not present in any prior `obs` | P0 |
| **Tool misuse** | `goto` to a URL that was already `goto_blocked`; `done(success)` whose answer doesn't match the goal; `ask_user_question` continuing on empty reply; `goto` retry after `goto` timeout instead of `read` | P0 |
| **`read()` overuse** | `read` / `read_grep` count > 8 in a single trace, OR `read` calls comprise > 60% of all tool calls | P1 (P0 if it co-occurs with a loop) |

A finding without a quoted span is dropped. No paraphrased claims.

### Step 8 — Write observations.md

Path: `<repo_root>/observations.md` (NOT inside `task2/`).

**Delete the existing file first**, then `Write` (not `Edit`) the new
content. Schema:

```markdown
# Bench Sweep — <ISO-8601 UTC timestamp>

**Run:** <bench JSON filename> · **Cases:** 101–113 · **Server:** restarted

## Metrics

| Metric | This run | Previous | Δ |
|---|---:|---:|---:|
| Success rate | 11/13 (84.6%) | 10/13 (76.9%) | +7.7 pp |
| Latency avg (ms) | 95,000 | 102,000 | -6.9% |
| Latency p95 (ms) | 200,000 | 210,000 | -4.8% |
| Input cached | 98,765 | 80,000 | +23.5% |
| Input uncached | 24,691 | 30,000 | -17.7% |
| Output | 6,789 | 5,977 | +13.6% |
| LLM calls | 234 | 220 | +6.4% |

History row appended to `task2/data/bench/metrics_history.jsonl`.

## Fail-case findings

### Case 109 — <web_name> — status: failed

#### [P0] <short title>
- **Where:** step 7, action `read_grep("9/3", window=N)` repeated 4×
- **Class:** loop · read-overuse
- **Evidence:** "step 7..10: read_grep(\"9/3\", window={2_000, 4_000, 8_000, 16_000}) → \"NOT FOUND: '9/3'\" each time"
- **Why P0:** Window-doubling with no new content surfaced — pure stall.

#### [P0] <short title>
- **Class:** hallucination
- **Where:** final answer
- **Evidence:** "answer cites \"59,513,990\" but no prior obs contains that substring (verified via grep over trace)"
- **Why P0:** Number was fabricated; agent grep'd for it after the fact (read_grep(\"59,513,990\")) returning NOT FOUND.

### Case 112 — <web_name> — status: timeout
...
```

If every case succeeded, the body collapses to:

```markdown
## Fail-case findings

All 13 cases passed. No findings.
```

If the previous row is missing, the table's `Previous` and `Δ`
columns show `—` and `Δ n/a (first run)`.

### Step 9 — Stop

Print one line:

```
Wrote observations.md and appended row to metrics_history.jsonl. <n_success>/13 succeeded. Re-run /bench-sweep for the next data point.
```

Do not propose fixes. Do not commit anything. Do not start another
sweep.

## Discipline rules

- Quote evidence verbatim in fail-case findings.
- Append-only on `metrics_history.jsonl`. Never rewrite a prior row,
  even to "fix" it — write a new row with corrected data.
- Schema of the JSONL row is a contract with the downstream charting
  script. Don't add/remove fields without the user's say-so.
- A finding without a quoted span is dropped.
- `read()` overuse alone is P1, not P0 — promote to P0 only when it
  co-occurs with a loop or hallucination.
- `observations.md` is replaced wholesale every run (delete then
  Write). It is **not** a log; the log is `metrics_history.jsonl`.
- One sweep per invocation. Do not re-run failed cases inline.

## Edge cases

- **Bench process crashes mid-run** (some `results` written, some
  not): aggregate what's there, but mark `n_total` as the count
  actually attempted, not 13. Add a P0 "bench crashed at case X"
  finding with the stderr tail.
- **Trace JSONL missing for a case**: P0 "missing trace"; aggregate
  metrics for that case as `null` in `per_case`; exclude from totals
  but include in `n_total`.
- **Sidecar `*.llm.jsonl` missing** (trace exists but no LLM log):
  per-case token fields are `null`; note in observations.md but do
  not abort.
- **`metrics_history.jsonl` does not yet exist**: create it with the
  new row as line 1. Deltas all `n/a (first run)`.
