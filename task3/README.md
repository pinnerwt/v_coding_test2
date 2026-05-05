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
   - Ground truth without public labels: each per-filing JSON under
     `eval/*.golden.json` and `data/extracted/*.json` (the regression
     baseline) was produced by Claude Opus 4.7 with extended thinking
     (high) reading the filing end-to-end, then frozen into a
     deterministic per-filing script under `scripts/extract_legacy/` so
     the diff is byte-comparable. `eval/legacy_overrides.json` records
     the few items where the legacy script knowingly disagrees with the
     literal status rules, each with a written reason — the override
     file is reviewed by hand, not generated.
   - Cross-validation lever: XBRL Company Facts
     (`https://data.sec.gov/api/xbrl/companyfacts/CIK{...}.json`) give
     independent numeric anchors for items 1 / 7 / 7A / 8, used as a
     spot-check when a body comes back suspiciously short.

2. **Failure modes**
   - Categorized into:
     - missing item (anchor not found / wrong heading regex)
     - over-eager slice (TOC stub leaks into body, or two items merged)
     - status mis-classification (cross-reference confused with IBR;
       "Reserved" vs "Not applicable")
     - structural mismatch (index-page filings, exotic 2008-era layouts)
   - The eval set is two layers (see "Eval set diversity" below): a
     15-filing survey slate covering modern-XBRL / heavy-IBR / pre-XBRL
     HTML / small-cap / 1995 plain-text SGML; and a 10-filing
     famous-hard set chosen via web search for awkward structure.

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

### Eval set diversity

Two layers, deliberately covering different axes.

**Survey slate** (`src/sec_toolbox/survey.py:SLATE`, 15 filings):

| Cat | Axis | Filings |
|---|---|---|
| A | Modern inline-XBRL HTML | Apple FY2023, Microsoft FY2023, NVIDIA FY2024 |
| B | Heavy "incorporated by reference" | Berkshire Hathaway, JPMorgan Chase, ExxonMobil (most-recent 10-K) |
| C | Older HTML, pre-XBRL (FY2004) | IBM, General Electric, Coca-Cola |
| D | Small-cap / recent IPO | Palantir, Reddit, Rivian |
| E | Older plain-text SGML (FY1995) | IBM, General Electric, Microsoft |

**Famous-hard set** (`eval/famous_10ks.json`, 10 filings) — chosen by web
search for awkward structure, biased toward 2007–2008 crisis-era and
pre-SOX layouts:

- Berkshire 2002 / 2008 — Buffett-letter integration, minimal Item-style headings
- Intel 2001 / 2008 — pre-SOX table-based HTML; non-sequential Item ordering
- Citigroup 2008, Goldman Sachs 2008 — scattered MD&A, heavy IBR, large bank disclosures
- AIG 2007 / 2008 — pre-collapse / post-bailout sprawl, restated risk
- Lehman 2007, Bear Stearns 2007 — last filings before collapse; Bear's primary doc is plain-text SGML

Combined coverage axes: industries (tech, social media, autos, banks,
insurance, broker-dealers, conglomerates, beverages, energy, defense
analytics); year span 1995 → 2024; filing formats (inline XBRL, modern
HTML, pre-SOX table-HTML, plain-text SGML); size (sub-$1B small-caps to
trillion-dollar megacaps).

### Reported numbers

From the most recent `eval/famous_results.json` (10 filings,
concurrency 5, per-case wall-clock cap):

| Metric | Value |
|---|---|
| Completion rate | 7 / 10 reach `done` |
| Failures | Berkshire 2008, Intel 2001, Citigroup 2008 — all `max_steps_exceeded`, matching the failure modes called out below |
| Cost / filing | median **$0.0295**, mean $0.0429, max $0.1023 (DeepSeek `usage`-based) |
| Total cost across 10 filings | $0.43 |
| Wall-clock latency | median **41.9 s**, mean 53.1 s, max 121.1 s |
| Per-filing cost ceiling | $0.50 (`COST_CEILING_USD`) |

These are single-run numbers. DeepSeek `deepseek-chat` is not
deterministic even at temperature 0, so the same filing can land in
12–17 steps on one run and trip `MAX_STEPS=30` on the next; the
completion-rate column moves by ±1–2 filings between back-to-back
runs without any code change. Treat the table as a smoke signal, not
a benchmark — Berkshire 2008 / Intel 2001 / Citigroup 2008 are the
structurally hard cases (see Failure Analysis), the rest are
LLM-variance noise.

For the 8-filing regression set (`eval/regression.py` against
`scripts/extract_legacy/`), the residual per-item drift on a green run
is dominated by the four entries in `eval/legacy_overrides.json` —
status flips on boundary phrasing (NVIDIA items 2 / 9C / 16, Microsoft
FY2020 item 16), each with a written reason in that file.

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

- **Tail cross-reference index**
  - Variant of the index-page case: real bodies live in the front, but
    the document tail (offsets > 90% of the doc) repeats every item
    heading packed in a 20-char window (`ITEM 1.\nITEM 2.\nITEM 3.\n...`).
    `find_anchors` previously surfaced these via last-write-wins on its
    `by_item` dict, hiding the real body anchor from the agent.
  - Mitigated by computing the longest-body anchor per item inside
    `find_anchors` (same logic the slicer uses) and exposing a new
    `duplicates: {item: count}` field so the agent sees ambiguity up
    front. Still pathological cases: Intel 2001, Citigroup 2008 — the
    body uses bare-numeric headings only in the cross-ref index and
    section names everywhere else, so no anchor regex matches the
    actual body. Treated as out-of-scope for the agent; legacy script
    required.

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
uv run python eval/run_famous.py --concurrency 4 --per-case-timeout 240
```

Writes `eval/famous_results.json`. No legacy ground truth; this is a
wider-coverage signal. `--per-case-timeout <seconds>` (default 240)
wraps each case in `asyncio.wait_for` so a single stuck filing cannot
hold the shared event loop hostage when running with `--concurrency >
1`; exceeded cases land in the summary as `status=wall_timeout`
instead of pinning a CPU core indefinitely.

### Survey

```bash
uv run python -m sec_toolbox survey
```

Outputs: `data/survey/report.md`, `data/survey/report.csv`.

## Where AI helped me

1. Implement the TDD/e2e tests for every tool (`tests/extract_agent/`).
2. Implement all the codes.
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
- **Repo-level prompts:** [`../prompts/task3.md`](../prompts/task3.md)

## Architecture

Single Python process. Two LLM clients (big planner + small classifier),
both OpenAI-compatible. The agent loop:

1. `clean_and_load` — strip HTML, normalize text, return a `text_id`.
2. `find_anchors` — regex sweep for `Item N[A-Z]?` headings; named
   groups required. Returns `by_item` (longest-body anchor per item,
   matching the slicer) and `duplicates: {item: count}` so the agent
   can see when multiple anchors survived dedupe and reason about
   tail cross-reference indexes.
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

## HTTP API

`sec_toolbox.api:app` is a FastAPI service that wires
`sec_toolbox.fetch.Fetcher` (rate-limited SEC fetch + on-disk cache
under `data/raw/archive/`) to `extract_agent.run_loop`.

### Endpoints

- `GET /health` → `{"status":"ok"}`
- `GET /docs` → auto-generated OpenAPI / Swagger UI
- `POST /extract` → run the agent on one filing

### `POST /extract`

Body — exactly one of these two shapes:

| Mode | Body | Notes |
|---|---|---|
| Identifier | `{"cik":"320193","accession":"0000320193-23-000106"}` | `primaryDocument` is resolved via the SEC submissions API |
| URL | `{"url":"https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"}` | Must match `…/Archives/edgar/data/{CIK}/{acc-no-dashes}/{filename}` |

Validation responses:

| Status | When |
|---|---|
| `422` | empty body, lone `cik` or lone `accession`, or both `url` and `cik+accession` set |
| `400` | URL doesn't match the SEC archive shape |
| `404` | accession isn't in the company's `recent` submissions |
| `500` | agent didn't reach `done` (e.g. `max_steps_exceeded`); `detail` carries `{status, cost_usd, steps}` |

Successful response:

```json
{
  "cik": "320193",
  "accession": "0000320193-23-000106",
  "filename": "aapl-20230930.htm",
  "items": [
    {
      "part": "I",
      "item_number": "1",
      "item_title": "Business",
      "content_text": "…",
      "char_range": [0, 51234],
      "status": "extracted"
    }
  ],
  "stats": {"status": "done", "cost_usd": 0.0234, "steps": 11}
}
```

### Examples

```bash
# Identifier mode
curl -s https://api.pinner.top/extract \
  -H 'content-type: application/json' \
  -d '{"cik":"320193","accession":"0000320193-23-000106"}' \
  | jq '{cik, accession, n_items: (.items|length), stats}'

# URL mode
curl -s https://api.pinner.top/extract \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"}' \
  | jq '.items[0]'

# Health
curl -s https://api.pinner.top/health
```

There is no auth on the deployed instance — requests are rate-limited
upstream by SEC EDGAR (10 req/sec, enforced inside `sec_toolbox`).

## Zeabur

> **Deploy URL:** api.pinner.top/ (shared host with task 2)
