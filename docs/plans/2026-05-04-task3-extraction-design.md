# Task 3 — 10-K item-level extraction: design

## Goal

From a fetched 10-K filing produce structured per-Item JSON: `part`, `item_number`, `item_title`, `content_text`, `char_range`, `status` ∈ {`extracted`, `incorporated_by_reference`, `not_applicable`, `reserved`}.

Must work across decades and formatting eras: modern inline-XBRL, older HTML, plain-text-rendered HTML, with and without dense "incorporated by reference" sections.

## Why not pattern-matching alone

The first instinct — locate `Item N.` headings by regex, classify status by keyword presence (`[Reserved]`, `Not Applicable`, `incorporated by reference`) — passes a casual happy-path test and breaks the moment a 2012 filing writes "Reference is made to the Proxy Statement" or a 1998 filing writes "None." Per-decade thresholds and keyword lists push the brittleness one layer deeper without removing it.

## The human-reader frame

How a person reads a 10-K to extract its sections:

1. Open the filing, find the **table of contents**. SEC rules effectively guarantee one. The TOC is the filing's own self-description: how many sections it has, what they're called, where each begins.
2. Flip to each section. Recognise the heading by **visual prominence** — bigger/bolder/centered, surrounded by whitespace. Not by matching the literal word "Item."
3. Read the paragraph(s) under the heading. Ask one question: *what is this slice doing functionally?* — substantive disclosure / pointer elsewhere / nothing-to-report / reserved-or-withdrawn. Reading comprehension, not keyword spotting.

The pipeline mirrors those three steps.

## Pipeline

### Stage 1 — Fetch (already built)

`sec_toolbox` covers fetching, caching, throttling, the four endpoint helpers. Out of scope here.

### Stage 2 — Render

HTML → plain text + an offset map. Each character in the rendered text knows which byte range of the source HTML produced it. Lets us emit honest `char_range` values regardless of which surface we segment on.

We also retain per-chunk **layout features** during rendering: relative font size, bold/italic, alignment, surrounding whitespace, capitalization ratio, line length. These are the cues the eye uses; we capture them once and keep them attached to the rendered text.

For inline-XBRL filings the `ix:` markup is preserved as offsets back into the source but contributes nothing to segmentation.

### Stage 3 — Find the table of contents

A TOC is a structurally distinct region: a dense cluster of short headings, each followed by a page number or anchor link, near the start of the document. We locate the region by its shape, not by searching for "Item." Encoding differs across eras (HTML anchors, leader dots, plain-text indentation) but the shape is invariant.

Output: an ordered list of TOC entries, each with the entry text and a target (anchor ID, page ref, or null).

### Stage 4 — Resolve TOC entries to body locations

Each TOC entry maps to a body location:

- **Modern HTML / inline-XBRL** — anchor link follows directly to the heading.
- **Older HTML / plain text** — find the heading text occurring later in the document with high visual prominence (the cues captured in Stage 2). The match is on the heading text the filing chose, not on a canonical name.

Output: ordered list of (item-as-filed, body byte range start) tuples. The end of each item's range is the start of the next.

### Stage 5 — Status classification by reading comprehension

Each slice gets one of four statuses. The classifier asks the human-reader question and routes by slice length to control cost:

- **Long slice** (substantive content trivially evident) → presumed `extracted`. No LLM call.
- **Short slice** → LLM reads it and returns one of: substantive / cross-reference / not-applicable / reserved. The prompt phrases the question the way a human asks it; it does not enumerate keywords.

The LLM is a reader, not an extractor. It sees only short slices plus, in fallback, short heading candidates. Bulk filing text never enters the LLM. Cost stays roughly constant regardless of filing size.

### Stage 6 — Fallback when no TOC region found

Rare, but possible for very old filings. Collect every visually-prominent chunk and have the LLM label each one ("is this a 10-K section heading? if so which Item?"). Cheap — typically 30 candidates, one line each.

### Stage 7 — Verification (no public ground truth)

The system has to grade itself.

- **Schedule check** against the SEC's published Item list for the filing's year (Item 6 renamed 2021; Item 1C added 2023). A missing or extra Item is a flag, not a fix.
- **Metadata cross-check** against XBRL Company Facts (period end, filer name, CIK).
- **`char_range` roundtrip** — the slice extracted from source bytes must match `content_text` after re-rendering.
- **Eval set** — 15 filings already surveyed, era-stratified. Per-Item presence + status diff is tracked across runs as a regression signal.

## Cost shape

Per filing the LLM sees:

- 0 calls if the TOC resolves cleanly and every Item slice is long (the modal case).
- N short-slice classifications where N = number of items rendered short (typically Items 10–14 for IBR-heavy filings, plus Reserved/NA cases).
- ~30 heading-label calls only in the no-TOC fallback.

No call ever depends on filing size. A 13 MB JPMorgan filing and a 1.5 MB Apple filing cost roughly the same.

## Status classification cases revisited

- **Whole-item IBR** (Exxon Item 11): slice short, reader says (b) cross-reference → `incorporated_by_reference`.
- **Internal cross-references inside substantive content** ("see Note 12"): slice long, skips LLM, stays `extracted`. Internal pointers do not change Item status.
- **Mixed item — substantive prefix, IBR remainder**: medium-length slice. The schema permits only the four statuses, so the binary call is "dominant content wins" → `extracted`. Recorded as a known limitation; eval set should include at least one such case.
- **Pre-2021 Item 6**: filings called it "Selected Financial Data" with substantive tables. Status `extracted`. The `reserved` status applies only to post-2021 filings where the SEC rule changed.
- **`None` / `Inapplicable` / `There is nothing to report`**: reader returns (c) → `not_applicable`. No keyword list.

## Known limitations

- Mixed-content items get a binary status; partial IBR is not surfaced.
- The TOC-finder relies on a TOC existing. We have no current data point of a 10-K without one; the no-TOC fallback is theoretical until we hit one.
- Visual-prominence detection on plain-text-rendered HTML is weaker than on HTML with explicit styling. Era-stratified eval should reveal this if it bites.
- Cross-validation against XBRL Company Facts catches metadata errors, not content errors.

## What this does not include

- A web/API surface. Out of scope for this design; comes after the parser is reliable.
- Storage / persistence beyond the existing on-disk archive cache.
- Any extraction *within* an item (e.g. risk-factor enumeration). The brief asks for item-level structure only.
