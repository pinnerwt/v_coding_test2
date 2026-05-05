# Phase 5b — Status splitting (Haiku subagent fan-out)

## Role

You are classifying a single 10-K Item body into non-overlapping status spans. The Item body has already been extracted by a deterministic rule-based pass; your job is to recognize when it contains a *mix* of substantive disclosure and explicit incorporation-by-reference, and to mark the boundaries.

**Do NOT:**
- Rewrite or summarize the body. The text is fixed.
- Compute character offsets yourself. Token-counting models cannot count characters reliably; quote text instead and let the main thread compute offsets.
- Classify cross-references as IBR. `"Refer to Item 10."` / `"Refer to Note 30"` / `"See pages 165–314"` are *internal* cross-references within the same 10-K — those stay `extracted`. Only the explicit `"incorporated … by reference"` phrasing pointing to *another* SEC filing (proxy statement, registration statement, prior 10-K) is IBR.
- Be misled by the literal phrase appearing in negation or table headers. `"information ... is not incorporated by reference"` is the *opposite* of IBR. `"Incorporated by Reference"` as a column header in an exhibits table is metadata, not a content classification.

## Inputs the main thread provides

```
item_number: <e.g. "10">
item_title:  <e.g. "Directors, Executive Officers and Corporate Governance">
body:        <the full content_text string for this item, body-relative offset 0>
```

## Status enum

- `extracted` — substantive disclosure printed inline in this 10-K.
- `incorporated_by_reference` — body explicitly says this content lives in another SEC filing and is incorporated by reference.
- `not_applicable` — body is "Not applicable." / "None." or equivalent.
- `reserved` — Item is a placeholder ("[Reserved]" / "Reserved").

## Output schema (return verbatim, JSON only)

The output is *segments*, not character offsets. Each segment quotes a short verbatim snippet from the body that uniquely locates its boundaries. The main thread converts these snippets to character offsets via `body.find()`.

```json
{
  "segments": [
    {"status": "extracted",                  "starts_with": "<first ~40 chars of this segment, verbatim>", "ends_with": "<last ~40 chars of this segment, verbatim>"},
    {"status": "incorporated_by_reference",  "starts_with": "Information to be provided in Items 10",      "ends_with": "fiscal year ended December 31, 2025."},
    {"status": "extracted",                  "starts_with": "Code of Conduct and Code of Ethics",          "ends_with": "<last ~40 chars of body, verbatim>"}
  ]
}
```

Constraints (the main thread will reject otherwise):
- `segments` is a list of 1+ entries.
- Segments are in document order; together they cover the full body with no gaps and no overlaps.
- The first segment's `starts_with` MUST be the first ~40 chars of the body (so the main thread can anchor to offset 0).
- The last segment's `ends_with` MUST be the last ~40 chars of the body (so the main thread can anchor to `len(body)`).
- Each `starts_with` / `ends_with` snippet must be a verbatim substring that occurs **exactly once** in the body. If a candidate snippet appears more than once, lengthen it until it is unique.
- Snippets must be sentence-aligned (start at sentence start, end at sentence end including the period). If a single paragraph mixes IBR and substantive content, cut at the sentence boundary closest to the IBR sentence's start/end.
- Adjacent segments MUST have different `status` (don't emit two consecutive `extracted` segments — collapse them).
- One segment per body is the common case; the body has uniform status. Do NOT split unless you have a confident IBR sentence to extract.

## Decision flow

1. Skim the body. Is there an explicit `"incorporated … by reference"` sentence pointing to another SEC filing (proxy / registration statement / prior 10-K)?
   - **No** → return one segment whose `starts_with` is the body's first ~40 chars and `ends_with` is the body's last ~40 chars, with the body's overall status (`extracted` for substantive, `incorporated_by_reference` if the WHOLE body is the IBR sentence with no other content, `not_applicable` / `reserved` if obviously so).
   - **Yes** → continue.
2. Locate the IBR sentence (start and end).
3. Decide whether substantive content surrounds it. If the IBR sentence is the entire body (or the entire body minus a header/footer), classify the whole body as `incorporated_by_reference` (one segment).
4. If substantive content surrounds it, emit:
   - one `extracted` segment before the IBR sentence (if any text precedes),
   - one `incorporated_by_reference` segment containing the IBR sentence (sentence-aligned),
   - one `extracted` segment after (if any text follows).

## Worked example (use as calibration, not as a template)

Body (an Item 10 body with executive officers list, an IBR sentence, then Code of Conduct and Insider Trading paragraphs):

```
Executive officers of the registrant
Age
Name
...
[long executive officers table — substantive disclosure]
...
Unless otherwise noted, during the five fiscal years ended December 31, 2025, all of JPMorganChase's above-named executive officers have continuously held senior-level positions with JPMorganChase. There are no family relationships among the foregoing executive officers. Information to be provided in Items 10, 11, 12, 13 and 14 of this 2025 Form 10-K and not otherwise included herein is incorporated by reference to the Firm's Definitive Proxy Statement for its 2026 Annual Meeting of Stockholders to be held on May 19, 2026, which will be filed with the SEC within 120 days of the end of the Firm's fiscal year ended December 31, 2025.

Code of Conduct and Code of Ethics
[Code of Conduct paragraph — substantive]
Insider Trading Policy
[Insider Trading paragraph — substantive]
```

Three segments:

```json
{
  "segments": [
    {"status": "extracted",                 "starts_with": "Executive officers of the registrant",         "ends_with": "no family relationships among the foregoing executive officers."},
    {"status": "incorporated_by_reference", "starts_with": "Information to be provided in Items 10, 11, 12", "ends_with": "fiscal year ended December 31, 2025."},
    {"status": "extracted",                 "starts_with": "Code of Conduct and Code of Ethics",            "ends_with": "<last ~40 chars of body, verbatim>"}
  ]
}
```

The IBR sentence boundary is the period before "Information" and the period after "December 31, 2025".

## What the main thread does with your output

1. For each segment, locates `starts_with` and `ends_with` in the body via `str.find()`. Rejects if either snippet is missing or appears multiple times.
2. Derives `body_start` = `body.find(starts_with)`, `body_end` = `body.find(ends_with) + len(ends_with)` for each segment.
3. Validates: covers `[0, len(body)]`, no gaps, no overlaps, adjacent segments differ in status.
4. Converts each segment to absolute `char_range` by adding the parent record's `char_range[0]`.
5. Emits one record per segment, all sharing the parent's `part` / `item_number` / `item_title`.
6. Replaces the parent record in the final JSON.

If you return invalid segments (missing snippets, ambiguous snippets, gaps, overlaps, unknown status), the main thread will keep the parent record unchanged and surface the rejection in the user reply. So: when in doubt, return one segment covering the full body.
