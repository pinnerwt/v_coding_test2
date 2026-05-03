# Task 2 — LLM Sidecar Logging + Cost Analyzer (Design)

## Problem

The Task 2 agent now runs against DeepSeek (`deepseek-chat`), so each call has a real $ cost. The existing trace records `usage` (`prompt_tokens`, `completion_tokens`) per step, which is enough to plot total $ per session but not enough to answer "where in the prompt did those tokens go?" Without that attribution, any cost-reduction work (smaller model for some call-sites, tape compaction, scout sub-agent, …) is guessing.

Separately, a failed step today shows the agent's *output* in the trace but not the *input* — you cannot reconstruct what context the model saw when it made a bad decision, because the page state has moved on.

## Scope

This design covers **observability only**:

- A per-session sidecar file recording every LLM call (request + response + usage).
- A small CLI analyzer that consumes sidecars and reports cost + token attribution.

It deliberately does **not** commit to a cost-reduction lever. The lever decision is unblocked by the data this design produces, in a follow-up.

## Architecture

Add a sibling to the existing `TraceWriter`:

- `data/traces/<sid>.jsonl` — existing user-facing tape, streamed to the SPA. **Unchanged.**
- `data/traces/<sid>.llm.jsonl` — new developer-facing log, one JSON line per LLM call. Never read by the UI.

New module: `agent/llm_trace.py` exposing `LLMTraceWriter` (append-only JSONL, parent dir created on init — same shape as `TraceWriter`).

`LLMClient.chat()` stays provider-agnostic and unaware of sessions. The loop owns the writer and calls it explicitly after each `await llm.chat(...)`. This mirrors the existing trace-write pattern and avoids changing `LLMClient`'s constructor.

## Per-line schema

```jsonc
{
  "ts": "2026-05-03T...",
  "step_idx": 7,                    // matches main trace's step counter
  "latency_ms": 842,
  "request": {
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "messages": [...],              // truncation applied per Truncation policy
    "tools": [...],                 // tool defs, or null
    "tool_choice": "...",           // or null
    "temperature": 0.2
  },
  "response": {
    "message": {...},               // full assistant message incl. tool_calls
    "usage": {                      // verbatim from provider
      "prompt_tokens": 4123,
      "completion_tokens": 87,
      "total_tokens": 4210,
      "prompt_cache_hit_tokens": 3800,
      "prompt_cache_miss_tokens": 323
    }
  }
}
```

No `usd` field. USD is a derivation, not a fact — DeepSeek does not return it, prices change, and providers may be swapped. The analyzer prices it from `usage` at read time. The sidecar records what *happened*, not what it *cost*.

The DeepSeek-specific `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` are passed through verbatim because cache-hit tokens are priced ~10× cheaper than miss tokens; any USD calculation that ignores the split is wrong by a meaningful margin.

## Truncation policy

Per-message-content cap of 12000 chars, applied to each `messages[i].content`, to assistant tool-call args, and to tool-result content. System prompts and short messages pass through untouched; the cap mostly bites tool-result messages (a11y trees, page reads).

```python
TRUNCATE_CHARS = 12000

def _truncate(s: str) -> str | dict:
    if len(s) <= TRUNCATE_CHARS:
        return s
    return {"truncated": s[:TRUNCATE_CHARS], "original_chars": len(s)}
```

Recording `original_chars` keeps the truncation reversible-in-spirit: the analyzer can still report "this step's tool-result was 45 KB" even though only the first 12 KB is on disk.

**Trade-off accepted:** truncation breaks **exact replay**. The model that ran originally saw the *full* content (and `usage.prompt_tokens` reflects that). The sidecar is therefore a faithful **cost record + decision record**, but a partial **replay record**. Replay-fidelity is the cost of bounded sidecar size.

## Analyzer — `task2/scripts/cost_report.py`

CLI: `uv run python scripts/cost_report.py [--session SID | --all] [--prices prices.json]`

Reads sidecars, emits markdown to stdout:

```
Session 9206cf2d... — goal: "Go to canirun.ai..."
  Total: 49 LLM calls, 187,432 prompt tokens, 4,891 completion tokens, $0.0631
  Cache hit rate: 91.2% (170,892 / 187,432 prompt tokens)
  Per-step:
    Step 1: 1,847 prompt / 41 completion / $0.0007
    ...
  By message role (across all calls):
    system        : 12.1% of prompt tokens
    user          :  3.4%
    assistant     : 18.7%
    tool          : 65.8%   ← biggest lever
```

**Section-attribution heuristic:** bucket by `messages[i].role`. Coarse but actionable — it tells you whether cost is in the system prompt, the agent's accumulated `assistant` chain, or the `tool` observations. Finer-grained slicing inside the system prompt is YAGNI until the role-level numbers point at it.

`prices.json` schema (small, hand-edited, optional):

```json
{
  "deepseek/deepseek-chat": {
    "input_miss": 0.27,
    "input_hit":  0.07,
    "output":     1.10
  }
}
```

Per 1M tokens. Keys are `provider/model`. Defaults to a built-in dict if file missing or model not listed.

## Error handling

Sidecar writes are fire-and-forget. Wrap each write in try/except; on failure emit one `llm_log_failed` event into the **main trace** (so failures are visible in the SPA) and continue. Same posture as `distill_failed`. Logging never breaks a run.

## Testing (TDD, red first in this order)

1. **`tests/unit/test_llm_trace_writer.py`** — pure unit. Construct an `LLMTraceWriter`, write two events, assert the file has two well-formed JSON lines with the expected schema and a truncation marker on an oversize content.
2. **`tests/integration/test_loop_emits_llm_sidecar.py`** — integration. Run a 3-step session against a fake `LLMClient` (returns canned tool-calls), assert `<sid>.llm.jsonl` has 3 lines, each with `step_idx` matching the main trace's step indices, and `usage` passed through verbatim.
3. **`tests/unit/test_cost_report.py`** — analyzer unit. Hand-craft a small sidecar fixture (with cache-hit/miss split, with a truncated content), run the analyzer in-process, assert the $ figure matches an arithmetic expectation and the by-role percentages sum to 100%.

No new tests for `LLMClient` itself — it stays unchanged.

## Out of scope (explicitly)

- No UI changes. Sidecars are developer-only.
- No cost-reduction lever (sub-agent, tape compaction, smaller model for some call-sites). That decision waits on the analyzer's output.
- No retention / rotation policy for sidecars. They live next to the main trace and inherit whatever volume policy the deploy uses.
- No exact-replay tooling. Truncation precludes it; if exact replay is needed later, that is a separate design.
