# Task 3 — Extraction Pipeline

`POST /extract` (api.py) → `Fetcher`/`DiskCache` pulls the 10-K HTML (cached on
disk, throttled by `SECClient`) → `extract()` (extract.py) → `graph.run()`
(graph.py, LangGraph DAG) → `verify_schedule()` annotates the items vs. the
canonical taxonomy → JSON response.

The graph is a **static DAG**: the only conditional edge reads
`state["toc_region"]`, never raw LLM output. Every LLM call goes through one
primitive: `reader.read(rendered, start, end, intention)`. An `Intention` is
`(name, system prompt, parser)`; failures collapse to `None` so callers
degrade.

## Pipeline diagram

```
                       POST /extract
                            │
                            ▼
                 Fetcher (cache → SEC EDGAR)
                            │ html bytes, fiscal_year
                            ▼
        ┌──────────── LangGraph DAG (graph.build_graph) ────────────┐
        │                                                           │
        │  START                                                    │
        │    │                                                      │
        │    ▼                                                      │
        │  render             (render_html → text + source_offset)  │
        │    │                                                      │
        │    ▼                                                      │
        │  build_anchor_index (#id → byte offset)                   │
        │    │                                                      │
        │    ▼                                                      │
        │  discover_page_pattern  ── LLM (head/mid/tail sample)     │
        │    │   pattern: "" possible                               │
        │    ▼                                                      │
        │  apply_page_pattern     deterministic regex sweep         │
        │    │                                                      │
        │    ▼                                                      │
        │  locate_toc             ── LLM (head+tail window)         │
        │    │                                                      │
        │    ├── toc_region is None ───────────────► emit (no items)│
        │    │                                                      │
        │    ▼ toc_present                                          │
        │  classify_toc_shape     ── LLM                            │
        │    │                                                      │
        │    ▼                                                      │
        │  parse_toc              ── LLM (items, ranges, footnotes) │
        │    │                                                      │
        │    ▼                                                      │
        │  resolve_ranges         deterministic, four strategies:   │
        │    │     1. label_ref → footnote text (override)          │
        │    │     2. anchor → id_index                              │
        │    │     3. heading_snippet → text.find()                 │
        │    │     4. first_page/last_page → page_index             │
        │    │     else: text_start=None (defer to next node)       │
        │    ▼                                                      │
        │  locate_item_body       LLM ONLY for unresolved ranges    │
        │    │                                                      │
        │    ▼                                                      │
        │  slice_bodies           cut text between consecutive      │
        │    │                    starts; label_refs use override   │
        │    ▼                                                      │
        │  classify_status        LLM per slice; ≥400 tokens auto = │
        │    │                    "extracted"; cache by text        │
        │    ▼                                                      │
        │  emit                   join with taxonomy.items_for_year │
        │    │                    → final brief schema              │
        │    ▼                                                      │
        │  END                                                      │
        └───────────────────────────────────────────────────────────┘
                            │ items
                            ▼
                    verify_schedule(items, fy)
                            │
                            ▼
                    {items, verification}
```

## Branches that fork the answer

Points where two filings can diverge before producing the final list:

1. **No LLM client** (no API key) — every sub-agent node returns `None`;
   `toc_region=None`; `emit` returns `[]`. Pipeline silently produces zero
   items.
2. **`locate_toc` fails / anchor not found in text** — short-circuits over
   `classify_toc_shape → parse_toc → … → classify_status` straight to `emit`,
   again zero items.
3. **`discover_page_pattern` returns nothing** — `page_index = {}`. Page-based
   ranges in `_resolve_page` will all miss; resolution falls through to
   `locate_item_body` LLM fallback (extra cost, slower).
4. **Per range, four resolution strategies** in `_resolve_one` (priority
   order):
   - `label_ref` → footnote text → `content_override` slice (skips body
     lookup entirely; `char_range = (0, 0)`).
   - `anchor` (`#id`) → `id_index` hit.
   - `heading_snippet` → `text.find()` after TOC.
   - `first_page` → `page_index` hit (may also use `last_page` for explicit
     end).
   - All miss → `text_start=None` → `locate_item_body` LLM fallback; if that
     also fails the range is dropped.
5. **`classify_status` per-slice fork** — slices with ≥400 whitespace tokens
   auto-tag `"extracted"` (no LLM); shorter slices ask the LLM, with a
   per-text cache so duplicates only cost one call. LLM failure → defaults to
   `"extracted"`.
6. **`emit` taxonomy join** — items present in `parsed_toc` but not in the
   year's canonical schedule keep the TOC's title and `part=None`; canonical
   items missing from the TOC simply don't appear (they're flagged later by
   `verify_schedule`, not by the graph).

So a single filing's output is determined by: did TOC parsing succeed → which
of the four range strategies hit per item → did short slices get
LLM-classified or fall back to `"extracted"`. Everything else is
deterministic.
