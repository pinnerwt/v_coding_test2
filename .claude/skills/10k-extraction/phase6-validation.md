# Phase 6 — Validation pass

This file is the entire context the Phase 6 subagent needs. Read it; do what it says; return the structured findings list at the bottom.

## Role

You are validating the output JSON of a `/10k-extraction` run. The main thread will use your findings to decide whether each issue warrants a per-filing script edit, an N≥2 promotion to `SKILL.md`, or "expected, surface in user reply."

**Do NOT propose fixes.** Your job is to report; the main thread decides.

## Inputs the main thread provides

- `<json_path>` — path to the output JSON, typically `task3/data/extracted/<cik>-<accession>.json`
- `<script_path>` — path to the per-filing extractor that produced it (informational; you don't run it)

## Tools

- Validator CLI: `task3/scripts/extract/_validate.py validate <json_path>`. Run from repo root via `cd task3 && uv run python scripts/extract/_validate.py validate <relative-json>`.
- Read the JSON directly for structural cross-checks the validator can't see.

## Steps

1. **Run the validator.** Capture all of its output. The validator is the source of truth for mechanical surface checks (status sanity, char_range monotonicity, char_range gaps > 2000, page-footer leak, duplicate item records).
2. **Read the JSON.** Cross-check anything the validator can't:
   - Item count (15–30 is typical; outside, flag).
   - All four parts (`I`, `II`, `III`, `IV`) present at least once. Filings with no Part IV are rare but possible — warning, not error.
   - Any item with body length < 200 chars and `status == "extracted"` → likely an internal cross-reference (`Refer to Item N`, `See Note N`, etc.). Not a bug, but worth surfacing.
   - Any item with body length > 100 KB → likely run-on. Expected for Item 15 if the filing has no Item 16; suspicious otherwise.
   - Any title containing `[Reserved]` but `status != "reserved"`.

## What is and is not a bug

- Mis-tagged status, monotonicity violation, missing parts, > 2000-char gap → **error**.
- Short cross-reference bodies, expected EOF run-on on Item 15, items genuinely marked `not_applicable` / `reserved` with empty bodies → **warning** (informational, expected behavior).
- Don't escalate "short body" to error if the body is `Refer to Item N` style — short cross-references are correct extraction, not a bug. The literal text *is* what the document says.

## Return shape (≤150 words, exactly this structure)

```
## Errors
- <one-line description per item; cite item_number and what's wrong>
- ...  (if none, write "- none")

## Warnings
- <one-line description per item; expected-but-noteworthy patterns>
- ...  (if none, write "- none")

## OK
- <one line confirming what passed: e.g. "22 items across Parts I–IV; all
  status tags consistent with body content; char_range monotonic.">
```

If the same finding affects multiple items, group them on one line (`Items 11/13/14 — short cross-reference bodies (17 chars each, "Refer to Item 10.")`). Don't enumerate identical issues.
