# Task 3 — SEC Fetch Toolbox + Survey (Design)

Date: 2026-05-04
Status: approved (brainstorming)
Scope: bootstrap step for Task 3. Build a small reusable client for the four SEC endpoints listed in the brief, then a survey script that runs it against 15 representative filings and produces a written reconnaissance artifact. No parser yet — the parser is a downstream task.

## Goals

1. Have the building block the parser will later call (`SECClient` + cache).
2. Produce a written, evidence-based picture of what SEC actually serves for 10-Ks across eras, filer sizes, and reporting styles. The survey output is the input to subsequent design work.

## Non-goals

- Parsing 10-K Items into structured JSON (separate task).
- XBRL fact extraction (Company Facts is fetched, not interpreted).
- Production-grade resilience (one retry on 429/5xx is enough).
- Beyond-15 filings — survey stays compact and committed.

## Layout

```
task3/
  pyproject.toml         # uv project, ruff config
  uv.lock
  README.md              # how to run; design notes; AI-help log
  prompts/               # task3-specific prompt records
  src/
    sec_toolbox/
      __init__.py
      cli.py             # argparse subcommands; entry: python -m sec_toolbox
      client.py          # SECClient: httpx + UA + 10rps throttle
      cache.py           # disk cache + index.json
      endpoints.py       # one function per endpoint
      survey.py          # callable from CLI: python -m sec_toolbox survey
  tests/
    test_cache.py
    test_client_throttle.py
    test_endpoints.py    # MockTransport-based
    test_cli.py
  data/
    raw/                 # cached payloads (gitignored except .gitkeep)
    index.json           # cache index (gitignored)
    survey/
      report.md          # output of survey run (committed)
      report.csv         # machine-readable rows (committed)
  scripts/
    run_survey.sh
```

`prompts/` lives **inside `task3/`**, not at repo root, per user direction.

## CLI surface

`uv run python -m sec_toolbox <subcommand>`:

- `submissions --cik <CIK>` — Submissions API. JSON to stdout. CIK normalized to 10-digit padded form internally.
- `search --q <query> [--forms 10-K] [--ciks ...] [--dateRange ...]` — Full-text Search JSON to stdout.
- `xbrl --cik <CIK>` — Company Facts JSON to stdout.
- `archive --cik <CIK> --accession <ACC> --filename <FILE>` — writes file to `data/raw/archive/<cik>/<accession>/<filename>` and prints the path. `--accession` accepts both `0000320193-23-000106` and the no-dash form.
- `survey [--list path] [--out data/survey]` — runs the 15-filing slate, writes `report.md` + `report.csv`.

Universal flags:

- `-o PATH` — override output destination.
- `--no-cache` — bypass the cache *read* (still writes).
- `--refresh` — force re-fetch.

## HTTP client

- `httpx.Client` with `transport` parameter injectable for tests.
- `User-Agent`: `os.environ.get("SEC_USER_AGENT", DEFAULT_UA)` with `DEFAULT_UA = "v_coding_test2 task3 squareznft@gmail.com"`. Also sets `Accept-Encoding: gzip, deflate` and the right `Host` per endpoint host.
- Rate limiter: in-process token bucket, 10 tokens/sec, capacity 10. `time_fn` injectable for tests. Applied inside `SECClient.request` so every endpoint inherits it.
- Retry: one retry with 1s backoff on 429 / 5xx; raise otherwise.

## Caching + index

- Key: `sha1(endpoint + normalized_args_json)` hex.
- Layout: `data/raw/<endpoint>/<key>.<ext>`. `ext` = `json` for JSON endpoints, file's actual extension (`htm`, `txt`, …) for `archive`.
- `data/index.json` schema (one row per key):
  ```json
  {
    "<key>": {
      "endpoint": "submissions",
      "args": {"cik": "0000320193"},
      "path": "data/raw/submissions/<key>.json",
      "ext": "json",
      "sha256": "...",
      "fetched_at": "2026-05-04T12:34:56Z",
      "status": 200,
      "content_type": "application/json",
      "bytes": 12345
    }
  }
  ```
- Lookup: build key → if `index.json` has it AND file exists AND `--refresh` not set → return from disk. Otherwise → fetch → write file → update `index.json` atomically (`index.json.tmp` + `os.replace`).
- `--no-cache` skips the read; always writes.

## Survey

- Hardcoded slate of 15 filings in `survey.py` as `[{category, name, cik, target_year_or_recent}, ...]`.
- For each filing: call Submissions → pick closest-to-target 10-K (or most recent for "recent") → call `archive` for the primary document → record one row.
- Row fields (`report.csv`):
  `category, name, cik, accession, filing_date, primary_doc, content_type, ext, bytes, sniffed_kind`
  where `sniffed_kind ∈ {inline_xbrl, html, plain_text, unknown}` (sniffed from first 4 KB).
- `report.md`: short narrative grouped by category, citing CSV rows.

### The 15-filing slate

| Category | # | Filer | CIK | Target |
|---|---|---|---|---|
| A. Modern inline-XBRL | 1 | Apple | 320193 | FY2023 |
| | 2 | Microsoft | 789019 | FY2023 |
| | 3 | NVIDIA | 1045810 | FY2024 |
| B. Heavy "incorporated by reference" | 4 | Berkshire Hathaway | 1067983 | recent |
| | 5 | JPMorgan Chase | 19617 | recent |
| | 6 | ExxonMobil | 34088 | recent |
| C. Older HTML, pre-XBRL | 7 | IBM | 51143 | ~2004 |
| | 8 | General Electric | 40545 | ~2004 |
| | 9 | Coca-Cola | 21344 | ~2004 |
| D. Small-cap / recent IPO | 10 | Palantir | 1321655 | recent |
| | 11 | Reddit | 1834584 | FY2024 |
| | 12 | Rivian | 1874178 | recent |
| E. Older plain-text | 13 | IBM | 51143 | ~1995 |
| | 14 | General Electric | 40545 | ~1995 |
| | 15 | Microsoft | 789019 | ~1995 |

Same filers reappear across A/C/E by design — same company across decades is the cleanest controlled comparison for format drift.

Accession numbers are resolved at script-run time via Submissions, not hardcoded here.

## Testing (TDD, red-first)

- `test_client_throttle.py` — token bucket with fake clock; assert ≤10 requests in one simulated second; assert wait happens on burst.
- `test_cache.py` — write, read-hit, read-miss, `--refresh`, `--no-cache`, atomic index update under simulated mid-write crash.
- `test_endpoints.py` — `MockTransport` returns canned bytes per (URL, method); assert correct URL construction, headers (UA, Host), and output shape per endpoint.
- `test_cli.py` — call `cli.main(argv)` directly with monkeypatched client; assert subcommand dispatch, error exit codes for missing args, `-o` honored.
- Live SEC calls happen only from `survey.py`; CI does not run survey.

## Tooling

- `uv init task3` (or `cd task3 && uv init --no-readme`).
- Runtime deps: `httpx`.
- Dev deps: `pytest`, `ruff`.
- `pyproject.toml` ruff config matches `task2/`'s.
- `.gitignore` (under `task3/`): `data/raw/`, `data/index.json`. Commit `data/survey/report.md` and `data/survey/report.csv`.

## Open risks / things to watch

1. Older filings sometimes have a primary document that is an index `.txt` listing other docs rather than the 10-K itself. The survey will surface this honestly via `sniffed_kind` and a brief note in `report.md`; we don't try to "fix" it now.
2. Full-text Search occasionally rate-limits more aggressively than the documented 10 rps. If we hit it, the single 1s-backoff retry should cover it; if not, we widen the retry policy *with a regression test*, not by guessing.
3. `report.csv` schema is committed — if a parser-driven follow-up wants more columns, that's a schema change with its own commit.
