# Phase 3 — Diagnostic pass

This file is the entire context the Phase 3 subagent needs. Read it; do what it says; return the structured summary at the bottom.

## Role

You are running a diagnostic pass on a SEC 10-K filing for the `/10k-extraction` skill. The main thread will use your summary to author a per-filing extractor script. **Do NOT propose code or edits** — your job is to characterize the document's structural traits, not to design the extractor.

## Inputs the main thread provides

- `<html_path>` — absolute path to the filing's HTML under `task3/data/raw/archive/...`
- `<cik>` and `<accession>` — stable identifiers

## Tools

- Diagnostic CLI: `task3/scripts/extract/_probe.py` with subcommands `head`, `clean-head`, `anchors`, `items`, `find`, `footers`. The `anchors` and `items` subcommands accept `--part-re` / `--item-re` overrides.
- Run from the repo root: `cd task3 && uv run python scripts/extract/_probe.py <html_path> <subcommand> [args]`.

Prefer the probe CLI over ad-hoc Python one-liners.

## Probes to run

Most extraction bugs come from a small number of structural traits. Spot the traits first, then design the script. Don't write a naive extractor and iterate against validation findings (that wastes tokens and obscures the cause).

1. **Inline-XBRL or not?** Look at the raw `head`. If you see `xmlns:ix="..."`, `<ix:header>`, `<ix:hidden>`, the script's cleaner must drop those wrappers (their `nonNumeric` values otherwise pollute the cleaned text).
2. **Cleaner sanity** — `clean-head`: confirm headings appear at the start of lines and line breaks separate logical sections in the first ~2KB.
3. **PART anchor count and ITEM anchor count** — `anchors`. If PART count > ~5, the document has page-running headers (every rendered page starts with `PART I` / `Item 1A`). This single fact dictates the slicing rule used downstream.
4. **ITEM heading shape** — `items`. Sample every match with ~60 chars of context. Note:
   - Bare `Item 1` lines with no period → running headers (the script's regex must require trailing punctuation).
   - `Item 1.\n\nBusiness` with title on a separate line → TOC style.
   - `ITEM 1. BUSINESS` all-caps on one line → body heading, the canonical case.
   - Numbers > 16 → bogus matches (the script's regex constrains `(\d{1,2})` to `1[0-6]|[1-9]`).
5. **Page-footer pattern** — `footers` + targeted `find`. Look for company name + "Form 10-K" + page number patterns (e.g. `Apple Inc. | 2023 Form 10-K | 16`, `Coupang, Inc.\n\n2023 Form 10-K\n\n41`). Report a regex if a recurring pattern is present.
6. **Last item present** — `find` for `Item 15` and `Item 16`. Many filings end at Item 15 with no Item 16. Item 15 then has no terminator heading and the slice runs to EOF, sweeping in financial statements + glossary + auditor reports. This is expected and gets surfaced as a finding, not fixed.
7. **TOC region size** — from `items`, read the **cleaned-text offset** of the last TOC-cluster anchor and the first body anchor (the gap between them is usually obvious — TOC anchors are spaced tens to a few hundred chars apart, then there's a jump of ≥1 KB to the first body anchor). Report both numbers verbatim. The main thread sets `TOC_REGION_END` to a value between them. **Always cleaned-text offsets, never raw-HTML byte positions** — every probe in `_probe.py` operates on the cleaned text, so all offsets you report come from there.

## Probes NOT to run

- Don't grep for specific cross-reference phrases (`Refer to Item`, `See Note`, etc.). Short-body cross-references are surfaced by the validation phase via length, not by phrase. Keep the diagnostic structural.
- Don't propose extractor edits, regex changes, or per-filing constants. The main thread decides those.

## Return shape (≤200 words, exactly this structure)

```
- inline-xbrl: yes|no
- heading style: ALL CAPS | Mixed Case | other
- TOC last anchor (cleaned-text offset): <int>
- first body anchor (cleaned-text offset): <int>
- PART anchor count: <n>  (if > ~5, note "running headers")
- ITEM anchor count: <n>  (TOC + body, before dedup)
- page-footer pattern: <regex if present, else "none observed">
- last item present: 15 | 16
- notable: <one line — anything that doesn't fit the common case, or "none">
```

If the filing is structurally a clean match for an existing per-filing script, name the closest reference (e.g. `19617-000162828026008131.py`) so the main thread can copy-and-adapt rather than start from scratch.
