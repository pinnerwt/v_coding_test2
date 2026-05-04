# Task 3 — LLM-Fallback TOC Extraction (Design)

## Problem

Across 13 cached 10-K filings tested via `POST /extract`, four returned zero items:

- MSFT 2020, MSFT 2023, BRKA 2025 — TOC is correctly detected by `find_toc_region`, but `segment._is_item_entry` rejects every entry because it checks only `entry.text` ("Business", "Risk Factors") and the "Item N." prefix lives in a sibling table cell. The anchor `target` does carry the canonical form (`#item_1_business`, `#ITEM_1A_RISK_FACTORS`).
- GE 2018 — `find_toc_region` latches onto a nested MD&A sub-TOC (11 hash-anchored entries with no "Item" tokens) instead of the master 10-K TOC. The link-density detector is fooled because GE's master TOC isn't anchor-heavy.

Three of the four failures are a one-line bug. The fourth needs a different TOC-locator strategy. Heuristics layered on top of the existing detector keep accumulating edge cases; an LLM is a better fit for the "where is the master TOC" decision when the heuristic mis-locks.

## Goals

1. Fix the `_is_item_entry` bug so MSFT 2020/2023 and BRKA 2025 extract correctly with no LLM in the path.
2. Add an LLM fallback for cases like GE 2018 where the heuristic produces a region but it's the wrong one.
3. Keep the response shape and downstream pipeline (`resolve_entries_to_body`, slicing, `verify`) unchanged.
4. Keep the unit-test suite offline; gate any live LLM call behind an env flag.

## Non-Goals

- LLM-driven body-text segmentation. Slice math stays heuristic.
- Response caching for the LLM call (filing-level disk cache already exists upstream).
- Multi-stage prompting. One tool call per fallback invocation.
- Replacing the heuristic detector wholesale.

## Architecture

Three components, each landed independently:

### 1. `segment._is_item_entry` fix

Recognize an entry as an Item when **either** `entry.text` matches `^Item \d+[A-Z]?` (current behavior) **or** `entry.target` matches `#item[_-]?\d+[a-z]?` (case-insensitive, allow optional separator). When the text lacks the prefix, derive `item_number` from the target.

This is a one-line predicate change plus a small helper to extract the number from a target. Lands as its own commit. Resolves three of four failing fixtures.

### 2. New module `sec_toolbox/toc_llm.py`

Single public function:

```python
def propose_toc(rendered: Rendered, html: bytes, *, client: LLMClient | None = None) -> TOCRegion | None:
    ...
```

Pipeline:

1. Slice `rendered.text[:50_000]`. Master TOC is in this window across every observed filing.
2. Call the existing `LLMClient` with a tool spec:
   ```
   report_toc(
     toc_end_marker: str,                    # short verbatim string at end of TOC
     items: [
       {
         item_number: "1" | "1A" | ...,      # canonical form
         anchor: "#item_1_business" | null,   # if filing exposes anchors
         heading_snippet: "Item 1. Business" # ~30-80 chars verbatim from body
       },
       ...
     ]
   )
   ```
3. Locate `toc_end_marker` in the rendered text → defines `region.text_end`.
4. For each item, resolve a body location:
   - If `anchor` is non-null and `id_index[anchor.lstrip('#')]` exists → use that source byte (existing fast path).
   - Else `rendered.text.find(heading_snippet, region.text_end)` → text offset; map back to source byte via `rendered.source_offset`.
   - If neither resolves → drop that item.
5. Build a synthetic `TOCRegion` with one `TOCEntry` per resolved item, ordered by body position. `region.text_start` = position of first TOC entry's appearance in the front slice; `region.text_end` = position of `toc_end_marker`.

Returns `None` on any failure: client raises, JSON malformed, zero items resolved, `toc_end_marker` not found.

### 3. Confidence gate in `segment.segment`

After `find_toc_region` returns a region and `resolve_entries_to_body` runs, count item-shaped body locations. If

- fewer than **10** item-shaped locations, **or**
- fewer than **80%** of TOC entries resolved to a body location,

call `propose_toc(rendered, html)`. If it returns a region, replace the heuristic's region and re-run resolution. If `propose_toc` returns `None`, fall through with the heuristic's result (current behavior).

Thresholds are constants, not tunables. They're a backstop, not a knob; the eval set determines if they need adjustment.

## Data Flow

```
html
  │
  ▼
render_html ──► Rendered
  │
  ▼
find_toc_region ──► TOCRegion?
  │
  ├── None ────────────────────────┐
  │                                ▼
  │                         propose_toc ──► TOCRegion?
  │                                │
  ▼                                ▼
resolve_entries_to_body ──► locations
  │
  ▼
confidence gate (count + % resolved)
  │
  ├── pass ──► slice items
  │
  └── fail ──► propose_toc → resolve → slice items
```

`propose_toc` always returns a `TOCRegion` shaped exactly like the heuristic's, so downstream code is unchanged.

## Error Handling

| Failure mode | Handling |
|---|---|
| `LLMClient` raises (network, auth) | `propose_toc` returns `None`. Segment falls through with whatever the heuristic produced (possibly empty). |
| LLM returns malformed JSON / missing tool call | Caught in `toc_llm`, returns `None`. |
| `toc_end_marker` not found in text | Returns `None`. |
| Zero items resolve | Returns `None`. |
| Single item fails to resolve | That item is dropped; remaining items proceed. Verifier reports it as missing. |
| LLM hallucinates a non-canonical `item_number` | `_match_item` returns `None` for that slice (existing behavior); slice is still emitted, just without canonical metadata. Better: filter unknown numbers in `propose_toc` against `taxonomy.items_for_year`. |

## Testing (TDD, in order)

All tests live under `task3/tests/`. The eval harness already iterates the cached filings — extending it counts.

1. **`test_segment.py::test_item_entry_recognized_via_anchor_target`** — write fixture-light test using a minimal HTML scrap with `<a href="#item_1_business">Business</a>` (text lacks the "Item 1" prefix). Assert `_is_item_entry` returns True and `_extract_item_number_from_entry` returns `"1"`. **Red first.**
2. **`test_segment.py::test_msft_2023_extracts_full_item_set`** — uses cached `data/raw/archive/789019/000095017023035122/msft-20230630.htm`. Assert `segment(...)` returns ≥ 20 slices and includes Item 1, 1A, 7, 8.
3. **`test_toc_llm.py::test_propose_toc_resolves_via_anchor`** — mock the LLM (intercepting `httpx` POST inside `LLMClient`) to return a canned tool call for the GE 2018 fixture. Assert returned `TOCRegion` has the expected entry count and `text_start`/`text_end` fall within the front 50K-char window.
4. **`test_toc_llm.py::test_propose_toc_returns_none_on_malformed`** — mock `httpx` to return invalid JSON. Assert `None`, no exception.
5. **`test_segment.py::test_confidence_gate_falls_back_to_llm`** — fixture: GE 2018. Mock the LLM to return a known good payload. Assert the heuristic's bad region is rejected and the LLM region wins.
6. **`test_toc_llm.py::test_propose_toc_live[ge-2018]`** — gated on `RUN_LIVE_LLM=1`. Calls real DeepSeek; asserts ≥ 18 items extracted.
7. **`tests/test_eval.py`** — extend the eval set to assert all four currently-failing fixtures (MSFT 2020/2023, BRKA 2025, GE 2018) reach a per-filing item count threshold.

Mocking strategy: mock the `httpx` POST inside `LLMClient`, not the client API. The contract under test is "LLM returns this tool-call JSON shape" — the JSON-parsing path needs to run.

## What Stays Unchanged

- `taxonomy.py` — source of truth for `part`, canonical `item_title`, year-specific schedule.
- `verify.py`, `extract.py`, `api.py` — unchanged. Same response shape.
- `render.py`, `cache.py`, `client.py`, `fetch.py` — unchanged.
- Existing prominence-fallback inside `resolve_entries_to_body` stays for filings where heuristic gets the right region but a few entries lack anchors.

## Cost & Latency

- 50K chars ≈ 13K input tokens. Tool call response ≈ < 1K output tokens.
- DeepSeek `deepseek-chat`: ~$0.27/M input, ~$1.10/M output → **~$0.005 per fallback call**.
- Latency: heuristic-good filings unchanged (no network). Fallback adds one round-trip; observed extract.py timings of 5–15 s for filings already exercising the live API are the same envelope.
- Filings that pay the fallback cost: only those failing the confidence gate. On the current 13-fixture set, that's GE 2018 after the `_is_item_entry` fix lands.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM picks the wrong region (hallucinates a sub-TOC) | Confidence gate also applies to LLM output: if the resulting `TOCRegion` resolves < 10 items, fall through to empty result rather than serving garbage. |
| Front-slice misses the master TOC on filings with very long cover pages | Surfaces in eval. Bump window or page through. Out of scope for v1. |
| `toc_end_marker` is too short and matches the TOC itself | Resolver requires `text_end > region.text_start`; if the marker matches inside the front of the TOC, drop the result. Document this constraint in the prompt. |
| LLM cost creeps up if more filings start failing the gate | Cost is monitored via existing `LLMClient.usage` reporting. Cheap unit cost; alert if per-call rate climbs. |

## Open Questions

None blocking implementation. The threshold values (10 items / 80% resolved) are first-pass picks; the eval will tell us if they need tightening.
