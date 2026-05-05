# Task 3 — SEC 10-K Item-level Structured Extraction

Extract Items 1–16 from a SEC 10-K HTML filing into the per-item JSON schema
(`part`, `item_number`, `item_title`, `content_text`, `char_range`, `status`).
The `extract_agent` package drives a tool-using LLM loop (slice → classify
status → validate → write) over a single filing.

## Run

Single filing by HTML path:

```bash
uv run python -m extract_agent path/to/filing.htm --out data/extracted/out.json
```

Single filing by CIK + accession (resolved via `data/index.json`):

```bash
uv run python -m extract_agent \
  --cik 320193 --accession 0000320193-23-000106 \
  --out data/extracted/320193-000032019323000106.json
```

Drain the queue at the repo root (`test_source.md`):

```bash
bash clean.sh
# equivalent:
uv run python -m extract_agent --queue ../test_source.md --all --out-dir data/extracted
```

The queue parser is header-aware — it accepts both the original
`CIK | Accession | Path | Done` shape and the real 7-column
`# | Done | CIK | Accession (no dashes) | Period | Filing | Path` shape.
Rows whose `Path` cell is wrapped in backticks have those stripped.

## Env vars

Read by `Config.from_env()`:

- `DEEPSEEK_API_KEY` — required.
- `AGENT_MODEL_BASE_URL` — defaults to `https://api.deepseek.com`.
- `AGENT_MODEL_BIG`, `AGENT_MODEL_SMALL` — model IDs for the planner / tool client.
- `MAX_STEPS` — agent step ceiling per filing.
- `COST_CEILING_USD` — abort the loop if accumulated `usage` cost exceeds this.

## Regression eval

Compare the agent's output against legacy per-filing JSON in `data/extracted/`:

```bash
uv run python eval/regression.py            # all filings
uv run python eval/regression.py --cik 320193
```

The legacy per-filing extractors (the deterministic baseline) now live under
`scripts/extract_legacy/`; they are kept as the regression ground truth, not
as the production path.

## Survey

```bash
uv run python -m sec_toolbox survey
```

Outputs: `data/survey/report.md`, `data/survey/report.csv`.
