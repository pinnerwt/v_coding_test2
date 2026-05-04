You are extracting the table of contents (TOC) of an SEC Form 10-K filing.

## Input

You receive the first ~50,000 rendered characters of the filing. The master TOC is in this window.

## Output (strict JSON, no prose)

Call the `report_toc` "tool" by emitting **only** a JSON object with this shape:

```json
{
  "toc_end_marker": "string — short verbatim text that appears immediately AFTER the master TOC and BEFORE Item 1's body content, e.g. 'PART I' or 'Item 1. Business' as it appears at the body location, not the TOC location",
  "items": [
    {
      "item_number": "1" | "1A" | "1B" | "1C" | "2" | "3" | "4" | "5" | "6" | "7" | "7A" | "8" | "9" | "9A" | "9B" | "9C" | "10" | "11" | "12" | "13" | "14" | "15" | "16",
      "anchor": "#item_1_business" or null,
      "heading_snippet": "Item 1. Business — verbatim 30-80 chars from the BODY of the filing where this item's content begins"
    }
  ]
}
```

## Rules

- Emit **only** the JSON object. No markdown fences, no commentary.
- `item_number` MUST be one of the canonical values above. Skip non-canonical items (e.g., "Information about our Executive Officers" — that's a sub-section of Item 1, not a top-level item).
- `anchor` is the `href` from the TOC link if visible (`<a href="#item_1_business">`); otherwise `null`. Do not invent anchors.
- `heading_snippet` is verbatim text from the **body** of the filing (where the item's content actually begins), not from the TOC itself. It should be unique enough to substring-match.
- `toc_end_marker` MUST appear in the input text after the TOC and before any item's body. "PART I" is usually a reliable marker.
- Order items as they appear in the TOC.
- If you can't identify a master 10-K TOC in the input, return `{"toc_end_marker": "", "items": []}`.
