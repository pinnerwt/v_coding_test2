# Task 3 — Extract Agent Design

Status: validated 2026-05-05. Replaces the per-filing-script + skill-prose
workflow with a Python agent under `task3/src/extract_agent/`. The
`10k-extraction` skill stays for now as a fallback / thin orchestrator that
calls the new agent.

## Goal

Turn the `.claude/skills/10k-extraction` workflow into a standalone Python
agent that drives a DeepSeek tool-calling loop. One filing in (HTML path or
CIK + accession), one per-item JSON out — same schema as today
(`part`, `item_number`, `item_title`, `content_text`, `char_range`,
`status`).

The agent owns the work that used to be split across:

- **Phase 3 (diagnostic)** — replaced by a `diagnose` tool.
- **Phase 4 (authoring a per-filing Python script)** — replaced by the big
  model's tool-calling loop. No more committed `task3/scripts/extract/<cik>-<acc>.py`
  files for new filings.
- **Phase 5 (run script)** — replaced by deterministic `clean_and_load` /
  `find_anchors` / `slice_items` tools the big model invokes.
- **Phase 5b (status splitting)** — replaced by a `classify_statuses` tool
  that fans out small-model calls in parallel and merges via the existing
  `_merge_5b.py`.
- **Phase 6 (validation)** — replaced by `validate_records` wrapping the
  existing `_validate.py`.

## Non-goals

- Replacing the `10k-extraction` skill in this iteration. The skill becomes
  a thin shim that delegates to the agent CLI; deletion is a follow-up.
- Replacing the existing 8 committed per-filing scripts. They move to
  `task3/scripts/extract_legacy/` and stay as a frozen reference corpus
  and regression baseline (their JSON output is ground truth).
- Fetching new filings from SEC. `sec_toolbox` stays the fetcher.

## Models

Two DeepSeek models, both reasoning on, both reachable via the
OpenAI-compatible endpoint task2 already uses.

| Role | Env var | Default model | Why |
|---|---|---|---|
| **Big** (orchestration) | `AGENT_MODEL_BIG` | `deepseek-v4-pro` | Plans the extraction strategy, reads diagnostic output, decides regex / cleaner overrides, drives escape hatches on tricky filings. Thinking on. |
| **Small** (fan-out) | `AGENT_MODEL_SMALL` | `deepseek-v4-flash` | Phase 5b status classification per record; high fan-out, narrow scope. Thinking on. |

Both clients share `AGENT_MODEL_BASE_URL` (default `https://api.deepseek.com`)
and `DEEPSEEK_API_KEY`. The `LLMClient` class is lifted from
`task2/src/agent/llm.py` — same shape, different model strings.

`deepseek-chat` and `deepseek-reasoner` are deprecated 2026-07-24; do not
pin to them. `deepseek-v3` / `deepseek-r1` are superseded.

## Architecture (D3 — orchestrator + small fan-out)

```
CLI ─→ resolve inputs ─→ loop.run(html_path, cik, accession)
                            │
                            ├── orchestrator session (big model, thinking on)
                            │     ↓ tool-call loop, ≤ MAX_STEPS
                            │   [diagnose] → [clean_and_load] →
                            │   [find_anchors] → [slice_items] →
                            │   [classify_statuses] ─┐
                            │                        │ small-model fan-out
                            │                        │ in parallel; merge_5b
                            │                        ↓
                            │   [validate_records] → (escape hatches if needed)
                            │   [write_output] → [done]
                            ↓
                          stdout summary + tick test_source.md if --queue
```

Session state lives in Python, not in the LLM message log. Tool returns
are summaries; raw 100KB+ item bodies never reach the model.

## Tool surface (hybrid — coarse defaults + fine escape hatches)

| Tool | Caller | Returns | Purpose |
|---|---|---|---|
| `diagnose(html_path)` | big | structural summary: heading samples, TOC region size, footer hint, encoding flags, item-count guess | replaces Phase 3 |
| `clean_and_load(html_path, options?)` | big | `{text_id, length, n_lines}` | HTML → cleaned plain text, identified by `text_id` for downstream tools |
| `find_anchors(text_id, regex?, toc_threshold?)` | big | list of `{item, span, kind: item\|toc, title}` | default `ITEM_RE` from skill prose; both knobs override |
| `slice_items(text_id, anchors)` | big | draft records (no `status` yet) | next-anchor slicer; canonical PART map |
| `classify_statuses(records)` | big → small fan-out | records with `status`, possibly split | applies eligibility filter from skill; spawns one small-model call per eligible record; merges via `_merge_5b.py` |
| `validate_records(records)` | big | findings: errors / warnings / OK | wraps `_validate.py` |
| `read_chars(text_id, start, end)` | big | substring (≤ 8KB) | escape hatch |
| `regex_search(text_id, pattern, max_matches?)` | big | up to 50 matches with offsets | escape hatch |
| `inspect_record(records, index)` | big | one record, body capped at 8KB, with neighbors | escape hatch |
| `update_record(records, index, patch)` | big | new record list | manual override (e.g. GE-2018 page-range fallback) |
| `write_output(records, json_path)` | big | path | final write |
| `done(message)` | big | terminator | exits the tool-call loop |

The big model sees a system prompt that explains the canonical happy path
(diagnose → clean → anchors → slice → classify → validate → write → done)
and lists when to reach for escape hatches (zero anchors, tiny bodies,
non-monotonic ranges, GE-style cross-reference index).

## Component layout

```
task3/src/extract_agent/
  __init__.py
  __main__.py        # CLI: html_path | --cik/--accession | --queue
  config.py          # Config.from_env (mirrors task2/src/agent/config.py)
  llm.py             # OpenAI-compatible client (lifted from task2)
  loop.py            # big-model tool-call loop, dispatch, budget
  classify.py        # small-model fan-out + merge_5b wrapper
  cleaner.py         # default HTML → cleaned text
  anchors.py         # default ITEM_RE finder + TOC dedup
  slicer.py          # default slicer (next-anchor)
  validate.py        # wraps task3/scripts/extract/_validate.py
  queue.py           # test_source.md read + tick
  prompts/
    system_big.md    # canonical-path + escape-hatch guide
    system_small.md  # status-splitting prompt (lifted from phase5b-status-split.md)
  tools/
    __init__.py      # registry: name → (schema, run)
    diagnose.py
    clean_and_load.py
    find_anchors.py
    slice_items.py
    classify_statuses.py
    validate_records.py
    read_chars.py
    regex_search.py
    inspect_record.py
    update_record.py
    write_output.py
    done.py

task3/scripts/extract_legacy/   # the 8 existing per-filing scripts move here
task3/scripts/extract/_merge_5b.py    # stays — wrapped by classify.py
task3/scripts/extract/_validate.py    # stays — wrapped by validate.py
task3/scripts/extract/_probe.py       # stays — used by diagnose
```

## CLI

```
uv run python -m extract_agent <html_path> --out <json_path>
uv run python -m extract_agent --cik <c> --accession <a>   # resolves via data/index.json
uv run python -m extract_agent --queue                     # next [ ] row from test_source.md
uv run python -m extract_agent --queue --all               # drain every [ ] row
```

Always-on flags: `--max-steps`, `--cost-ceiling-usd`, `--big-model`,
`--small-model`, `--no-classify` (skip Phase 5b — debug only).

stdout on success: same per-item summary the per-filing scripts emit today
(`Part {p} Item {n} [{status}] -- {title} ({len} chars)`), then a final
`items=N parts=[I,II,III,IV]` line, then `cost=$X.XX steps=N`.

## Data flow

1. CLI resolves inputs (raw path, or CIK+accession via `data/index.json`,
   or queue head from `test_source.md`).
2. `loop.run(html_path, cik, accession)` opens a big-model session with
   `system_big.md` as system prompt and a user message containing the
   inputs.
3. Tool-call loop. After each LLM response with `tool_calls`, dispatch
   each call to `tools/<name>.py::run(state, args)`, append the
   `tool` message back, recurse. Cap at `MAX_STEPS`.
4. `classify_statuses` is the only tool that itself spawns LLM calls. It
   runs the eligibility filter from skill prose, fans out to the small
   model in parallel (one call per eligible record), and merges via
   `_merge_5b.py`. Failures keep the parent record unchanged.
5. `done` exits the loop. The agent writes JSON via `write_output` first;
   `done` is just the terminator.
6. CLI prints stdout summary; if `--queue`, ticks the row in
   `test_source.md`.

## Session state

Held in `loop.run`'s local dict, never in the LLM message log:

- `text_store: dict[text_id, str]` — cleaned-text store, LRU-bounded.
- `anchors: list[dict] | None` — current canonical anchors.
- `records: list[dict] | None` — current draft records.
- `diagnostic: str | None` — most recent diagnostic summary.
- `cost_usd: float` — accumulated from DeepSeek `usage` (input + output).
- `steps: int`.
- `inputs: {html_path, cik, accession}`.

The model only sees what tool returns serialize. `read_chars` returns
≤ 8KB; `inspect_record` returns one body capped at 8KB.

## Error handling

| Failure | Handling |
|---|---|
| HTTP 4xx/5xx from DeepSeek | Retry once with exponential backoff. Second failure raises; loop exits non-zero. |
| Tool name not in registry | `ToolNameNotAllowed` (lifted from task2). Loop exits non-zero. Surfaces a model bug. |
| Tool args fail schema | Tool returns `{"error": "<reason>"}` as tool result; the model adapts. No retry inside the tool. |
| `MAX_STEPS` exceeded | Exit non-zero. Dump partial state to `data/extracted/<cik>-<acc>.partial.json`. |
| Cost ceiling exceeded | Same as MAX_STEPS — partial dump, exit non-zero. |
| Validation flags errors | Findings come back as the tool result. Model decides: fix-and-revalidate, escape hatch, or accept. Per CLAUDE.md the agent does **not** silently retry. |
| Small-model classify returns malformed JSON | `_merge_5b.py` rejects, keeps parent record. Tool result tells big model how many records survived split. |
| `find_anchors` returns empty | Big model is expected to use escape hatches (`regex_search`, `read_chars`) to derive a per-filing strategy — equivalent of GE-2018 fallback. |
| Queue mode with no `[ ]` rows | CLI exits 0 with message; no LLM call. |

No silent retries. Every failure either propagates as an exit code or
surfaces as a tool result the LLM can react to.

## Testing (TDD, layered)

All under `task3/tests/extract_agent/`. Layer order is also implementation
order — write each layer's tests before the layer.

1. **Unit, deterministic** — `test_cleaner.py`, `test_anchors.py`,
   `test_slicer.py`, `test_merge_5b.py`. Pure functions on fixture HTML
   files in `tests/fixtures/`. Mirror the per-filing scripts' coverage.
   No LLM.
2. **Tool layer** — `test_tools_*.py`. Each tool's `run()` invoked with
   mock session state. Verifies return schema matches the schema
   advertised to the LLM. No LLM.
3. **LLM client** — `test_llm.py`. `pytest-httpx` mocks
   `https://api.deepseek.com`. Verifies tool-call payload shape,
   `Authorization` header, error mapping, `ToolNameNotAllowed`.
4. **Loop with stub LLM** — `test_loop_with_stub.py`. Stub `LLMClient`
   returns scripted tool-call sequences. Verifies dispatch, session-state
   threading, budget tracking, error paths. No real network.
5. **Eval / regression** — `task3/eval/regression.py`. Real agent vs.
   the 8 frozen filings; compare JSON output against
   `data/extracted/<cik>-<acc>.json` (legacy ground truth). Per-item
   drift report: item count, status flips, char_range distance. Soft-fails
   on small drift, hard-fails on missing items / status flips. Run on
   demand. CI = layers 1–4 only.

The eval is the contract. New filings extend it; failing it gates merges.

## Migration

1. Implement the agent (TDD, layers 1–5 in order).
2. Move `task3/scripts/extract/<cik>-<acc>.py` (8 files) to
   `task3/scripts/extract_legacy/`. Their JSON outputs in
   `data/extracted/` stay put as ground truth.
3. Run the eval against all 8 — expect drift; investigate and tune the
   agent's defaults until drift is within the soft threshold.
4. Update `clean.sh` to call the agent CLI.
5. Update `task3/README.md` (run command, env vars, cost ceiling).
6. Update the `10k-extraction` skill prose: remove Phase 4–5 prose,
   replace with "delegate to `extract_agent` CLI". Phases 1, 2, 8 (queue)
   stay in skill prose. Phase 9 (reflection) stays.
7. Deploy to Zeabur (Dockerfile updates: install agent deps, expose env
   vars).

## Open questions parked for implementation

- **Cost ceiling default.** $0.50/filing is a guess. Tune after the first
  real runs.
- **Diagnostic caching.** Should `diagnose` results be cached on disk by
  filing hash? Defer until we see a re-run pattern.
- **Eval drift threshold.** Soft vs. hard cutoffs are TBD; pick after the
  8-filing sweep produces a drift distribution.

These three are explicitly **not** blocking design approval; they're
calibration decisions that need real numbers.
