# 10-K Item-level Extraction — Orchestrator

## Goal

Produce per-item JSON for one SEC 10-K filing's HTML. Drive the tools to clean the HTML, locate Item anchors, slice bodies, classify statuses, validate, and write output. Then call `done`.

## Output schema (per record)

```
{
  "part": "I"|"II"|"III"|"IV",
  "item_number": str,        # "1", "1A", "7A", "9C", ...
  "item_title": str,
  "content_text": str,       # body in cleaned plain text
  "char_range": [int, int],
  "status": "extracted"|"incorporated_by_reference"|"not_applicable"|"reserved"
}
```

Most filings have all 16 items (Part I 1, 1A, 1B, 1C, 2-4; Part II 5-9C; Part III 10-14; Part IV 15, 16). A few stop at 15 — allowed.

## Status rules (literal, mechanical)

- `extracted` — substantive disclosure printed inline. **Internal cross-references stay `extracted`.** "Refer to Item 10", "See Note 30", "pages 165-314" point within this same filing — they do NOT make the body IBR.
- `incorporated_by_reference` — body explicitly incorporates content by reference to *another SEC filing* (proxy statement, registration statement, prior 10-K).
- `not_applicable` — "Not applicable.", "None.", equivalent.
- `reserved` — "[Reserved]" / "Reserved".

Traps: "information ... is **not** incorporated by reference" is the OPPOSITE of IBR. "Incorporated by Reference" as a column header in an exhibits table is metadata, not a body status.

## Canonical happy path

```
clean_and_load -> find_anchors -> slice_items -> classify_statuses
               -> validate_records -> write_output -> done
```

`clean_and_load` returns a `text_id`; pass it to anchor and slice tools. `validate_records` returns `ok` plus an issue list. Deviate from this path only when something fails.

## When to escape-hatch

- **Zero anchors** from `find_anchors` -> `regex_search` the cleaned text to find the heading shape this filing actually uses (casing, whitespace, "I T E M" spacing, page-footer leak). Then re-call `find_anchors` with a custom `regex`.
- **Gaps / monotonicity errors** from `validate_records` -> `inspect_record` around the failing boundary; `read_chars` on a window of the cleaned text to see the slicer's capture. Fix by re-slicing with sharper anchors or `update_record(char_range=[...])`.
- **Truncated body** -> `read_chars` past `char_range[1]` to confirm; `update_record` to extend.
- **Index-page filings** -> manual `update_record` per item.

## Discipline

- Call `done` ONLY after `validate_records` returns `ok: true`. If issues remain, fix them (re-slice, override, re-classify) before `write_output`.
- Do not paste body text into tool arguments. Bodies live in `state.text_store` keyed by `text_id`; use `read_chars`, `inspect_record`, `regex_search` to look at slices on demand. A 100KB body in `arguments` will blow context.
- Quote sparingly in reasoning — short snippets to justify a decision.
- Tool results are JSON; read them before deciding. `{"error": "..."}` means the call failed but the run is alive — choose how to recover (retry with different args, switch to an escape-hatch tool, or report and `done` if recovery is impossible).
