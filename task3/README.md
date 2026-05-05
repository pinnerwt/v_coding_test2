# Task 3 — SEC 10-K Item-level Structured Extraction

Extract Items 1–16 from a SEC 10-K HTML filing into the per-item JSON schema
(`part`, `item_number`, `item_title`, `content_text`, `char_range`, `status`).
The `extract_agent` package drives a tool-using LLM loop (clean → anchor →
slice → classify status → validate → write) over a single filing.

## Evaluation

The extractor is evaluated along three axes:

1. **Per-item correctness**
   - 16 records emitted in canonical order, with the right `status` and a
     body whose `char_range` falls inside the cleaned text store.
   - Measured by diffing against legacy per-filing extractors
     (`scripts/extract_legacy/`) which act as the deterministic ground
     truth on the original survey set.

2. **Failure modes**
   - Categorized into:
     - missing item (anchor not found / wrong heading regex)
     - over-eager slice (TOC stub leaks into body, or two items merged)
     - status mis-classification (cross-reference confused with IBR;
       "Reserved" vs "Not applicable")
     - structural mismatch (index-page filings, exotic 2008-era layouts)
   - Eval set is intentionally biased toward famous/hard 10-Ks
     (`eval/famous_10ks.json`) chosen via web search for awkward
     structure.

3. **Efficiency**
   - DeepSeek `usage` is the source of truth for prompt / completion
     tokens; the loop tracks `cost_usd` and aborts on `COST_CEILING_USD`.
   - Latency is the wall-clock per filing (`eval/run_famous.py` measures
     it with a per-case cap).

### Method

- All extractions write a JSON artifact under `data/extracted/`.
- `eval/regression.py` runs the agent against every filing with a legacy
  baseline and reports per-item drift (length ratio, `char_range`
  Hausdorff, `status` mismatch).
- `eval/run_famous.py` runs the 10 famous-hard filings in parallel with a
  per-case wall-clock cap and writes `eval/famous_results.json`.
- `eval/legacy_overrides.json` lets a known-bad legacy item be marked
  authoritative for the regression diff (so we don't chase a baseline bug
  forever).

This is the same lightweight loop:
benchmark → failure triage → fix → re-run.

## Failure Analysis

Common failure patterns observed:

- **TOC stub leakage**
  - Anchor finder hits the table-of-contents row before the body and
    `slice_items` returns the stub as the body.
  - Mitigated by deduping TOC-region anchors first, then taking the
    longest body for each `(part, item_number)` (saved as a memory rule).

- **Internal cross-reference vs incorporation by reference**
  - "Refer to Item 10" / "See Note 30" inside this filing is `extracted`,
    not `incorporated_by_reference`. The trap is the literal phrase
    "incorporated by reference" appearing in body text that points within
    the same 10-K.
  - Mitigated by tightening the `system_big.md` status rules and a
    sub-agent classifier that requires evidence of an *external* SEC
    filing.

- **Index-page layouts**
  - A handful of filings use a pure cross-reference table with no item
    bodies in the 10-K HTML itself.
  - Detected by anchor density clustered in a small TOC region. The agent
    writes a stub artifact, surfaces the finding in `done(message=...)`,
    and falls back to the legacy script for those CIKs.

- **Looping / step-budget exhaustion**
  - LLM keeps re-validating after `validate_records` returns soft
    warnings.
  - Mitigated by an explicit "after at most one fix-then-revalidate, ship
    with surfaced findings" rule in the system prompt and a hard
    `MAX_STEPS` cap.

These failures are tracked in the regression / famous result JSONs and
iterated through one at a time.

## Key Design Tradeoffs

### 1. Deterministic per-filing scripts vs tool-using agent

- Per-filing scripts (`scripts/extract_legacy/`)
  - Pros: cheap, repeatable, exact `char_range`, no LLM cost.
  - Cons: each script is hand-tuned to one filing; doesn't generalize.

- Agent loop (`src/extract_agent/`)
  - Pros: one code path covers many filings; survives layout drift.
  - Cons: token cost per filing, occasional looping, status calls fuzzy
    on edge phrasing.

→ Kept the legacy scripts as the regression baseline (they are the
ground truth on the survey set) and shipped the agent as the production
path.

---

### 2. Big planner + small classifier vs single model

- Big-only is simpler but burns tokens on cheap classification.
- Splitting `classify_statuses` into a smaller model halves the cost on
  the 16-status pass.

→ Two clients (`AGENT_MODEL_BIG`, `AGENT_MODEL_SMALL`) with the small
client only used by `classify_statuses`. The split is one env-var swap
away from collapsing back to a single model.

---

### 3. LLM-driven slicing vs deterministic anchors

- Pure LLM slicing is robust to layout but expensive and produces
  fuzzy `char_range`.
- Anchor-based slicing (`find_anchors` regex over cleaned text) is cheap
  and gives exact ranges, but breaks on filings with unusual heading
  shapes.

→ Default to deterministic anchors with a one-shot LLM escape hatch
(`regex_search` + a re-call of `find_anchors` with a custom regex). If
that still fails, ship a stub and surface the structural mismatch in
`done`.

---

### 4. Step budget and corrective passes

- Unbounded loops are how an agent burns cost without making progress.
- Hard caps make some filings unfixable.

→ `MAX_STEPS=30`, plus a soft cap inside the prompt: at most three
read-only inspections, at most two corrective passes, then ship even if
`validate_records` still has warnings. Findings go in `done(message=)`.

---

### 5. Other design decisions

**Loop / control**
- Single `run_loop` over OpenAI-style tool calls; no planner/executor
  split.
- `done` is the only terminal tool; `write_output` must be the call
  immediately preceding it.
- Cost ceiling (`COST_CEILING_USD`) is checked before every LLM call —
  abort beats overspend.

**Tools / observation**
- Body text never travels through tool arguments. `clean_and_load`
  returns a `text_id` and bodies live in `state.text_store`; tools
  reference them by id.
- Read-only inspection is split into three narrow tools (`read_chars`,
  `regex_search`, `inspect_record`) so the agent picks the cheapest
  window.
- `find_anchors` accepts a custom `regex` with named groups — the
  contract is checked at call time and raises on missing groups (so the
  agent can't silently get junk).

**Status defenses**
- `classify_statuses` runs through the small model with a strict prompt
  separating IBR (must reference *another* SEC filing) from internal
  cross-references (still `extracted`).
- "[Reserved]" and "Not applicable." are matched literally before the
  LLM is asked.

**Infra / observability**
- DeepSeek `usage` is the source of truth for tokens; `_estimate_cost`
  uses cache-miss prices for `deepseek-chat` / `deepseek-reasoner`.
- Per-filing JSON output lives under `data/extracted/<cik>-<acc>.json`,
  matching the legacy layout so the regression diff is byte-comparable.
- `eval/run_famous.py` and `eval/regression.py` are async + parallel;
  the SEC client is rate-limited at the toolbox layer
  (`sec_toolbox.throttle`).

**LLM choice**
- DeepSeek `deepseek-chat` for both big and small by default. Reasoning
  ON for the planner, OFF for the classifier. Swap is one env var.

## How to run

### Single filing

By HTML path:

```bash
uv run python -m extract_agent path/to/filing.htm --out data/extracted/out.json
```

By CIK + accession (resolved via `data/index.json`):

```bash
uv run python -m extract_agent \
  --cik 320193 --accession 0000320193-23-000106 \
  --out data/extracted/320193-000032019323000106.json
```

### Drain the queue

```bash
bash clean.sh
# equivalent:
uv run python -m extract_agent --queue ../test_source.md --all --out-dir data/extracted
```

The queue parser is header-aware — it accepts the original
`CIK | Accession | Path | Done` shape and the 7-column
`# | Done | CIK | Accession (no dashes) | Period | Filing | Path` shape.
Rows whose `Path` cell is wrapped in backticks have those stripped.

### Regression eval

```bash
uv run python eval/regression.py            # all filings with a legacy baseline
uv run python eval/regression.py --cik 320193
```

### Famous-hard smoke test

```bash
uv run python eval/run_famous.py
```

Writes `eval/famous_results.json`. No legacy ground truth; this is a
wider-coverage signal.

### Survey

```bash
uv run python -m sec_toolbox survey
```

Outputs: `data/survey/report.md`, `data/survey/report.csv`.

## Where AI helped me

1. Implement the TDD/e2e tests for every tool (`tests/extract_agent/`).
2. Implement all the codes — 0 lines were written by me.
3. Created a `/10k-extraction` skill that:
   - resolves identifiers and runs the agent CLI;
   - falls back to the per-filing legacy script when the agent's hybrid
     tools can't fit a filing's structure;
   - surfaces validation findings instead of silently shipping bad JSON.
4. Brainstormed the status taxonomy (extracted vs IBR vs reserved vs
   N/A) — most of the iteration on the system prompt was driven by
   triaging concrete misclassifications, not abstract design.
5. Web search for the 10 most famously hard 10-Ks (Berkshire, Apple,
   etc.) to seed `eval/famous_10ks.json`.
6. Asked AI to refactor recurring text-wrangling helpers (regex search,
   first-N-KB read) into the tool set the agent now uses, instead of
   re-implementing them per filing.

## Introduction

A single-process Python package that takes one 10-K HTML filing in and
emits the per-item JSON list out. The agent runs a ReAct-style loop over
a fixed tool catalogue (`tools/`); the orchestrator system prompt
(`prompts/system_big.md`) pins a canonical 7-call happy path and
narrowly scoped escape hatches.

- **Brainstorming prompt:** [`prompts/brainstorming.md`](./prompts/brainstorming.md)
- **Pipeline notes:** [`docs/pipeline.md`](./docs/pipeline.md)
- **Repo-level prompts:** [`../prompts/task3.md`](../prompts/task3.md)

## Architecture

Single Python process. Two LLM clients (big planner + small classifier),
both OpenAI-compatible. The agent loop:

1. `clean_and_load` — strip HTML, normalize text, return a `text_id`.
2. `find_anchors` — regex sweep for `Item N[A-Z]?` headings; named
   groups required.
3. `slice_items` — cut between consecutive anchors; dedup TOC-region
   anchors; pick longest body per `(part, item_number)`.
4. `classify_statuses` — small-model pass over each slice → one of
   `extracted | incorporated_by_reference | not_applicable | reserved`.
5. `validate_records` — overlap / required-item / soft-warning checks.
6. `write_output` — emit the per-filing JSON artifact.
7. `done` — terminal call, must follow `write_output`.

Body text is never passed in tool arguments; bodies live in
`state.text_store` and tools reference them by `text_id`. The cost
ceiling is checked before every LLM call and aborts the loop on
overrun. The legacy per-filing scripts under `scripts/extract_legacy/`
remain available as a fallback for filings the agent cannot fit (e.g.
index-page layouts).

## Local development

```bash
cd task3
uv sync
uv run pytest -v                       # unit + integration + eval helpers
uv run ruff check .                    # lint
uv run python -m extract_agent --help
```

## Environment variables

Read by `Config.from_env()`:

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | _(required)_ | API key for the DeepSeek-compatible endpoint |
| `AGENT_MODEL_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible base URL |
| `AGENT_MODEL_BIG` | `deepseek-chat` | Planner model (drives the loop) |
| `AGENT_MODEL_SMALL` | `deepseek-chat` | Classifier model (status pass) |
| `MAX_STEPS` | `30` | Hard cap on agent loop iterations |
| `COST_CEILING_USD` | `0.50` | Abort if accumulated `usage` cost exceeds this |
| `SEC_USER_AGENT` | `Research test@example.com` | Required by SEC EDGAR |

## Storage

- `data/index.json` — fetched-filing index (endpoint, args, relative
  path). Used by the CLI to resolve `--cik` + `--accession`.
- `data/raw/archive/<cik>/<accession>/...` — cached SEC archives.
- `data/extracted/<cik>-<accession>.json` — per-filing extraction
  artifacts. Same layout used by both the agent and the legacy scripts,
  so the regression diff is byte-comparable.
- `data/survey/report.{md,csv}` — survey output.
- `eval/famous_results.json` — last famous-hard smoke run.

## Docker

```bash
cd task3
docker build -t task3-extract .
docker run --rm -p 8080:8080 \
  -e DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY \
  -v $(pwd)/data:/app/data \
  task3-extract
```

The container exposes `sec_toolbox.api:app` on port 8080 (filing
fetch + extraction endpoint).

## Zeabur

1. Connect this repo and point the service at `task3/`.
2. Set the env vars above (at minimum `DEEPSEEK_API_KEY` and
   `SEC_USER_AGENT`).
3. Attach a persistent volume mounted at `/app/data` (cached archives
   and extracted JSONs survive redeploys).
4. Deploy. The service exposes port 8080.

> **Deploy URL:** _pinner.top/_ (shared host with task 2)
