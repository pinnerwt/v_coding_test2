---
name: 10k-extraction
description: Use when the user asks to extract Items 1-16 from a SEC 10-K filing into the Task 3 per-item JSON schema (part, item_number, item_title, content_text, char_range, status), or invokes /10k-extraction. The skill writes a fresh per-filing Python extractor at task3/scripts/extract/<cik>-<accession>.py, runs it, validates the output, and surfaces suspicious results so the next iteration of the skill can address them.
---

# 10k-extraction

For each new 10-K document, **author a fresh Python extractor tailored to that filing**, run it, and emit the per-item JSON schema. One filing = one script = one JSON.

This is deliberate: 10-K HTML varies enough (heading casing, page-footer leak, "incorporated by reference" wording, pre-XBRL plain-text tables) that a single universal parser hides failures. Per-filing scripts force engagement with each document and accumulate as a corpus we can later generalise from.

## Output schema (per item)

| Field | Type | Notes |
|---|---|---|
| `part` | `"I"` \| `"II"` \| `"III"` \| `"IV"` | Roman numeral |
| `item_number` | string | e.g. `"1"`, `"1A"`, `"7A"`, `"9C"` |
| `item_title` | string | Best-effort from heading text |
| `content_text` | string | Full item body in the cleaned plain text |
| `char_range` | `[int, int]` | `[start, end]` offsets in the *cleaned* plain text (not raw HTML) |
| `status` | enum | `"extracted"` \| `"incorporated_by_reference"` \| `"not_applicable"` \| `"reserved"` |

The script writes a JSON array of these objects to disk.

## Inputs

- A local HTML path, **or**
- A CIK + accession (look up in `task3/data/index.json`; entries with `endpoint == "archive"` give `path_relative`).

If only a company name or year is given, ask once for CIK + accession or a path. Don't guess.

## Workflow

1. **Resolve input** to a local HTML path under `task3/data/raw/archive/...`.
2. **Pick stable identifiers**: `<cik>` and `<accession-no-dashes>` (strip the dashes from the accession number).
3. **Author script** at `task3/scripts/extract/<cik>-<accession>.py`. Create parent dirs if needed. Each script is self-contained: stdlib only by default, `argparse` CLI taking `<html_path> --out <json_path>`. See **Authoring guidance** below.
4. **Run it** from `task3/`:
   ```bash
   cd task3 && uv run python scripts/extract/<cik>-<accession>.py \
       <html_path> --out data/extracted/<cik>-<accession>.json
   ```
5. **Validate** the output JSON (see **Validation** below). If validation flags issues, do **not** silently retry — report the issues to the user with the script path so the next iteration can edit it.
6. **Return** the JSON path + a one-line-per-item summary: `Part {p} Item {n} [{status}] -- {title} ({len content_text} chars)`.

## Diagnostic pass first (do this BEFORE writing the script body)

Most extraction bugs come from a small number of structural traits of the HTML. Spot the traits first, then design the script — don't write a naive extractor and iterate against validation findings (that wastes tokens and obscures the cause).

Use the diagnostic CLI at `task3/scripts/extract/_probe.py` rather than ad-hoc Python one-liners. It exposes the probes below as subcommands (`head`, `clean-head`, `anchors`, `items`, `find`, `footers`), with `--part-re` / `--item-re` overrides on `anchors` and `items` for testing alternate patterns against a new filing without editing code.

Run these probes against the raw HTML and the cleaned text. They are cheap and answer the questions that drive every subsequent design choice:

1. **Inline-XBRL or not?** `head -c 2000 <html>`. If you see `xmlns:ix="..."`, `<ix:header>`, `<ix:hidden>`, the cleaner must drop those wrappers (their nonNumeric values will pollute the cleaned text otherwise).
2. **Cleaner sanity**: print `len(cleaned_text)` and the first ~2KB. Confirm headings appear at the start of lines and that line breaks separate logical sections.
3. **Count PART anchors**: `len(list(PART_RE.finditer(text)))`. **If > ~5, the document has page-running headers** (every rendered page starts with "PART I" / "Item 1A"). This single fact dictates the slicing rule (see §4 below).
4. **Sample ITEM anchors**: print every match with 60 chars of context. Look for:
   - Bare `Item 1` lines with no period — running headers; the regex must require trailing punctuation.
   - `Item 1.\n\nBusiness` with the title on a separate line — TOC style; the regex must allow `\s*` (which spans `\n`) between the period and the title.
   - `ITEM 1. BUSINESS` all-caps on one line — body heading; the canonical case.
   - Numbers > 16 — bogus matches; constrain `(\d{1,2})` to `(1[0-6]|[1-9])`.
5. **Look for page footers**: `grep` the cleaned text for the company name + "Form 10-K" + page number patterns (e.g. `Apple Inc. | 2023 Form 10-K | 16`, `Coupang, Inc.\n\n2023 Form 10-K\n\n41\n\nTable of Contents`). These leak into short Items (Mine Safety, [Reserved], 9C) — either strip in the cleaner or trim post-extract.

Capture the answers in the script's module docstring (one or two lines: "running headers: yes; heading case: ALL CAPS; page footer: 'Company | Year Form 10-K | <n>'"). This is the per-filing tailoring the skill exists to encode.

## Authoring guidance (what the per-filing script must do)

The backbone is the same across filings. The per-filing tweaks live in the regexes and the cleaner — informed by the diagnostic pass.

1. **HTML → cleaned plain text** — `clean_html(raw) -> str`:
   - Strip `<script>`, `<style>`, `<head>`, `<noscript>` content entirely.
   - For inline-XBRL filings, also strip `<ix:header>` and `<ix:hidden>` (their `nonNumeric` values otherwise appear as garbage at the top of the cleaned text and shift offsets).
   - Insert a newline at block-level tag boundaries (`p`, `div`, `br`, `tr`, `li`, `h1-6`, `table`, `section`, `article`, `td`, `th`, `ul`, `ol`, `header`, `footer`, `hr`).
   - Decode HTML entities; normalise NBSP (` `) to space; drop zero-width spaces.
   - Collapse runs of horizontal whitespace; trim trailing spaces per line; collapse 3+ newlines to 2.
   - **Optional per-filing**: strip recurring page-footer patterns observed in the diagnostic pass (regex over the cleaned text). For company-bar footers like `Apple Inc. | 2023 Form 10-K | 16`, a single `re.sub` removes the leak before slicing, so short items (Mine Safety, [Reserved]) stay clean.
   - **Why a custom cleaner each time**: some filings use ALL-CAPS headings inside `<font>` tags; some hide Item headings inside table cells; older filings have plaintext heading lines. Tune the cleaner per filing.

2. **`ITEM_RE` — find Item headings.** Default:
   ```python
   ITEM_RE = re.compile(
       r"^\s*ITEM\s+(1[0-6]|[1-9])([A-C])?\s*[\.\:]\s*(.*?)$",
       re.IGNORECASE | re.MULTILINE,
   )
   ```
   - **Required trailing `.` or `:`** — this is what filters out bare `Item 1` running headers (MSFT, JPM hit this hard; without the constraint you get 30+ false positives per filing).
   - **Number constrained to 1–16** — Form 10-K only goes up to Item 16; allowing `\d{1,2}` lets stray matches like "Item 60" (a footnote ref in IBM) slip in.
   - **`(.*?)` for the title** — accepts an empty title because TOC entries often put the title on the next line; the slicer falls back to "next non-empty line" when the captured title is blank. The `\s*` between `[\.\:]` and the title spans `\n`, so titles immediately below the heading line are still captured by the same match.
   - Loosen punctuation only if the diagnostic pass shows a filing with no period after the number (rare, mostly pre-XBRL plaintext).

3. **`PART_RE` is not used.** Drop it entirely. The on-disk PART headings are unreliable for two distinct reasons:
   - Page-running headers can repeat `PART I` at the top of every rendered page (MSFT, JPM hit ~20+ matches each), polluting any "most-recent PART" assignment.
   - Some filings (JPM 2025 is the canonical case) only emit a body `PART I` heading and never re-emit `PART II/III/IV` in the body — they exist only in the TOC. A walk-and-tag-with-most-recent-PART scheme then assigns every body Item to Part I.

   Instead, **assign `part` from the canonical SEC Form 10-K item→part map** — this is fixed by the form itself and never wrong:
   ```python
   ITEM_TO_PART = {
       "1": "I", "1A": "I", "1B": "I", "1C": "I", "2": "I", "3": "I", "4": "I",
       "5": "II", "6": "II", "7": "II", "7A": "II", "8": "II",
       "9": "II", "9A": "II", "9B": "II", "9C": "II",
       "10": "III", "11": "III", "12": "III", "13": "III", "14": "III",
       "15": "IV", "16": "IV",
   }
   ```

4. **Do NOT deduplicate occurrences.** Each `ITEM_RE` match becomes its own output record, with its own `char_range`. The schema's `char_range` is a single `[start, end]` pair per record; if the same item appears in multiple non-contiguous places (TOC stub + body, or split body sections), each appearance must be a separate record. Downstream consumers can deduplicate by `item_number` and pick the longest body if they want a single canonical entry.
   - Tag each anchor's `part` from the canonical `ITEM_TO_PART` map (not from the most-recent `PART` anchor — see §5 below for why).
   - Drop matches whose item number isn't in the canonical map (defensive against the rare stray `ITEM 60` style false positive that survives the regex).

5. **Slice content** — `content_text` = cleaned text from end of heading line to start of the **next ITEM anchor only**:
   ```python
   item_starts = sorted(a["span"][0] for a in anchors if a["kind"] == "item")
   ```
   - **Why ITEM-only, not ITEM+PART**: page-running headers repeat `PART I` at the top of every rendered page. Treating PART matches as boundaries truncates Item 1's body at the first page break (e.g. MSFT's Business section drops from ~70KB to ~3KB). Items are sequential within and across Parts, so the next ITEM anchor is always a safe boundary.
   - `body_start` = `text.find('\n', heading_match.end())` (end of heading line). `next_start` = next item anchor start. `char_range = [body_start, next_start]`.
   - If the captured title was empty, fall back to the next non-empty line within ~200 chars after `body_start`.

6. **Classify status** for each item using these rules, in order:
   - `incorporated_by_reference` — body contains "incorporated" within ~5 words of "by reference" in the first ~800 chars **and** body length < 4000 chars. Allow intervening words: `r"incorporat\w*(?:\s+\w+){0,5}\s+by\s+reference"` — filings write "incorporated **herein** by reference", and the bare `incorporated by reference` regex misses these.
   - `reserved` — heading title text is `[Reserved]` (bracketed) **or** body matches `\breserved\b` near the start with body length < 1500.
   - `not_applicable` — body matches `\bnot\s+applicable\b` near the start with length < 1500, **or** body is just `None.` / `None` / `N/A` (case-insensitive, after trim).
   - else `extracted`.

7. **CLI contract**: `argparse` with positional `html_path`, `--out` required. Write the JSON array to `--out`; print a short summary to stdout (item count, parts seen).

The skill does not ship a single template; if a previous filing's script is a good starting point, the skill may copy it to the new path and adapt — but the resulting script must still be specific to the new filing (tuned regex, tuned cleaner where needed) and committed alongside its output JSON.

## Failure modes and how the design rules out each one

The design choices above each defend against a specific failure mode observed in the corpus. If a future filing surfaces a new mode, add a row here.

| Failure mode | Symptom in validation | Defended by |
|---|---|---|
| Bare `Item 1` page running headers matched as anchors | Item count 30–40+; massive char gaps inside Part I | `ITEM_RE` requires `[\.\:]` trailing punctuation |
| `PART I` page running headers used as slice boundaries | Item bodies truncated to ~3KB despite huge underlying section | Slice on ITEM anchors only; PART used solely for `part` assignment |
| TOC heading wins over body heading | Empty / very short Item bodies; titles look like TOC fragments | Dedup-by-last across the document |
| Stray "Item 60" / footnote references | Spurious extra Items with random titles | `(\d{1,2})` constrained to `1[0-6]|[1-9]` |
| `incorporated herein by reference` not classified | Items 10–14 tagged `extracted` with ~150-char proxy-statement body | `incorporat\w*(?:\s+\w+){0,5}\s+by\s+reference` |
| Page-footer leak into short Items | `Item 4 [not_applicable]` body = `"Not applicable.\n\nApple Inc. \| 2023 Form 10-K \| 17"` | Strip footer pattern in cleaner OR trim post-extract |
| Inline-XBRL `<ix:hidden>` text appearing in cleaned output | Garbage numeric tokens at the top of the cleaned text; offsets shifted | Cleaner strips `<ix:header>` / `<ix:hidden>` |
| Body has no "Item N." anchors at all (older or non-standard filings, e.g. GE 2018) — the only `Item N.` text lives in a single end-of-doc TOC with page-range pointers (`Item 1.\nBusiness\n4-5, 12-35`); body uses page-numbered narrative without item-anchored headings | All items extracted with 1-17 char bodies (sliced between adjacent TOC entries), every item flagged "largest occurrence < 50 chars" | Out of reach of pure regex anchors. Either author a per-filing section-name → item-number map (e.g. `BUSINESS` → 1, `RISK FACTORS` → 1A) merged into anchors, or fall back to an LLM TOC pass that emits `(item, body_span)` pairs |

## Validation (run after the script completes)

For each filing's output, check and surface:

- **Item count**: 15-30 is typical; outside that, flag.
- **All four parts present**: at least one item under each of Part I/II/III/IV. Filings with no Part IV are rare but possible — note as a warning, not a hard fail.
- **Status sanity**:
  - Items whose `content_text` (trimmed) is `None.` / `None` / `N/A` but `status == "extracted"` → mis-tagged, flag.
  - Items whose `item_title` contains `[Reserved]` but `status != "reserved"` → mis-tagged, flag.
- **Page-footer leak**: items with `len(content_text) < 200` whose `content_text` contains `"Form 10-K"` or matches `r"\|\s*\d+\s*$"` (trailing page number) — likely picked up footer text, flag.
- **Char_range gaps**: between consecutive items in the same part, if `next.char_range[0] - this.char_range[1] > 2000`, the script lost a body chunk — flag.
- **Char_range monotonicity**: ranges must be non-decreasing across the array; otherwise the script got confused.

Report flags as a bulleted list under a `Findings` heading in the response. These are the loop closure points — they tell us what to tune in the next per-filing script (or what general rule to lift back into authoring guidance).

## Out of scope

- Fetching new filings from SEC. The fetcher (`sec_toolbox`) is a separate concern; this skill assumes the HTML is already on disk under `task3/data/raw/archive/...`.
- LLM-based extraction. v0 is rule-based. An LLM fallback for low-confidence sections is a future cost-tier feature.
- Cross-validation against XBRL Company Facts. Future feature.

## Quick reference

```bash
# typical end-to-end (after the per-filing script is authored)
cd task3
uv run python scripts/extract/320193-000032019323000106.py \
    data/raw/archive/320193/000032019323000106/aapl-20230930.htm \
    --out data/extracted/320193-000032019323000106.json
```
