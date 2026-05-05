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

The skill runs as a chain of phases. Two of them — diagnostics and validation — produce a lot of probe/log output that would pollute main context if read directly; dispatch those to an `Explore` subagent and read only its summary. The authoring and run phases stay in the main thread because they're where judgment lives (which constants to tune, when to apply the anti-benchmaxxing checks at the bottom of this file).

| Phase | Where it runs | Why |
|---|---|---|
| 1. Resolve input | Main thread | Trivial lookup in `task3/data/index.json` |
| 2. Pick identifiers | Main thread | `<cik>` + `<accession-no-dashes>` (strip dashes) |
| 3. **Diagnostic pass** | **Explore subagent** | Probes emit ~10KB+ output per filing; main thread only needs the structural summary |
| 4. Author script | Main thread | Decision-making phase — must see prior conversation, prior filings' scripts, and the anti-benchmaxxing rules |
| 5. Run script | Main thread | One-line bash; cheap |
| 5b. **Status splitting** | **Haiku Agent fan-out (parallel)** | One bounded subagent per `extracted` record. Each classifies its body into status spans; main thread merges and rewrites the JSON in place |
| 6. **Validation pass** | **Explore subagent** | Reads the full JSON + runs `_validate.py`; main thread only needs the grouped findings |
| 7. Report | Main thread | Synthesizes the diagnostic summary, the run output, and the validation findings into the user-facing reply |

**Run command** (phase 5):
```bash
cd task3 && uv run python scripts/extract/<cik>-<accession>.py \
    <html_path> --out data/extracted/<cik>-<accession>.json
```

**When NOT to delegate** (authoring, phase 4): a subagent doesn't see the conversation history. It can't tell whether a quirk in this filing is genuinely new or the third time we've seen JPM-style cross-refs, and it won't apply the "How to edit this skill" discipline at the bottom of this file when deciding whether to add a constant to the per-filing script vs. promote it. Keep authoring in the main thread.

If validation flags issues, do **not** silently retry — report the issues with the script path so the next iteration can edit it.

Final reply to user: JSON path + a one-line-per-item summary (`Part {p} Item {n} [{status}] -- {title} ({len content_text} chars)`) + a `Findings` section with whatever the validation subagent surfaced.

### Phase 3 dispatch (Diagnostic pass — Explore subagent)

The phase file `.claude/skills/10k-extraction/phase3-diagnostic.md` is the entire context the subagent needs. Dispatch with a prompt of the form:

```
Read .claude/skills/10k-extraction/phase3-diagnostic.md and follow it.

Inputs:
  html_path: <absolute path under task3/data/raw/archive/...>
  cik:       <cik>
  accession: <accession-no-dashes>
```

The subagent returns a ≤200-word structural summary. Use it to inform Phase 4 (authoring).

### Phase 5b dispatch (Status splitting — Haiku Agent fan-out)

After the per-filing script writes the JSON, the main thread re-reads it and dispatches one Haiku Agent per `extracted` record to detect mixed-status content (substantive disclosure interleaved with explicit "incorporated by reference" sentences). The phase file `.claude/skills/10k-extraction/phase5b-status-split.md` is the entire context each subagent needs.

**Eligibility filter** — only fan out for records where ALL of:
- `status == "extracted"` (other statuses are mechanical and unambiguous);
- `len(content_text) >= 200` (shorter bodies — `"Refer to Item 10."`, `"Not applicable."` — can't meaningfully split, save the call);
- `not (item_number == "15" and len(content_text) >= 500_000)` (Item 15 EOF run-on contains financial statements + glossary + auditor reports, not item content; sub-classifying that would dominate cost without honoring the schema's intent).

**Dispatch pattern** — fan out in parallel: one Claude Code Agent tool call per eligible record, all in a single assistant message (per `superpowers:dispatching-parallel-agents`). Use `model: "haiku"` and `subagent_type: "general-purpose"`. Each prompt has the form:

```
Read .claude/skills/10k-extraction/phase5b-status-split.md and follow it.

Inputs:
  item_number: <e.g. "10">
  item_title:  <e.g. "Directors, Executive Officers and Corporate Governance">
  body:
"""
<full content_text for this record>
"""

Return ONLY the JSON object specified in the phase file. No prose.
```

**Merge** — the subagent returns *segments* (snippet-anchored), not character offsets. The phase file forbids it from computing offsets directly because token-models cannot count characters reliably (this was discovered in the JPM 2025 GREEN test where Haiku reported a body length of 2272 vs the true 3923). For each subagent return:
1. Parse the JSON; reject (and keep the parent record unchanged) if `segments` is missing/malformed or contains unknown statuses.
2. For each segment, locate `starts_with` in the parent body via `body.find()`. Reject if missing OR appears more than once (ambiguous). The segment's `body_start` is `body.find(starts_with)`. The segment's `body_end` is the **next segment's `body_start`** (or `len(body)` for the last segment). This means inter-sentence whitespace between segments is absorbed into the prior segment — a deliberate choice, because IBR sentences typically end with `". "` or `".\n\n"` and asking the subagent to choose which side of the whitespace owns the byte is brittle (Haiku will pick differently across runs and miss by 1–2 chars). Sanity-check `ends_with`: it must occur exactly once in the body, and its match must fall inside `[body_start, body_end)`.
3. Validate the resulting spans: `segments[0].body_start == 0`, monotonic starts, adjacent statuses differ.
4. Convert each span to absolute by adding the parent's `char_range[0]`.
5. Replace the parent record with one new record per segment — same `part`, `item_number`, `item_title`; `content_text` sliced from the parent's body using `body_start`/`body_end`; `char_range` absolute; `status` from the segment.
6. If the subagent returned a single segment covering the whole body with `status == "extracted"` (the common case), this is a no-op.

**Write back** — re-write `task3/data/extracted/<cik>-<accession>.json` with the merged records, sorted by `char_range[0]`. Phase 6 then validates the rewritten file.

**Cost note** — every `extracted` body above the 200-char floor gets a Haiku call. For a typical 23-item filing, that's ~10–15 calls per filing; for very large bodies (Item 1A at 100KB+, Item 8 at 60KB+) the input cost dominates. Always-on by user decision; do not gate on signal regex.

### Phase 6 dispatch (Validation pass — Explore subagent)

The phase file `.claude/skills/10k-extraction/phase6-validation.md` is the entire context the subagent needs. Dispatch with a prompt of the form:

```
Read .claude/skills/10k-extraction/phase6-validation.md and follow it.

Inputs:
  json_path:   task3/data/extracted/<cik>-<accession>.json
  script_path: task3/scripts/extract/<cik>-<accession>.py
```

The subagent returns a ≤150-word findings list (Errors / Warnings / OK). Surface this verbatim in the user-facing reply under a `Findings` heading.

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

4. **Deduplicate to one record per item — drop TOC anchors first, then keep the longest body.** TOC stubs are a navigation aid, not output. The naive "longest body wins" rule fails when both candidates are tiny — e.g. Item 6 `[Reserved]` where the body anchor slices 0 chars (next heading is immediately adjacent) and the TOC anchor slices ~20 chars (the page-number stub). The TOC version then wins by length and yanks `char_range` back to the TOC region, breaking monotonicity.
   - Use a TOC-region threshold (typically 8000 chars; pick from the diagnostic pass — TOC anchors cluster in the first ~5–8KB). For each `item_number`, if any anchor has `match_start > TOC_REGION_END`, drop all anchors with `match_start ≤ TOC_REGION_END` from that item's pool. Then take the longest body from what remains.
   - Tag each anchor's `part` from the canonical `ITEM_TO_PART` map (not from the most-recent `PART` anchor — see §5 below for why).
   - Drop matches whose item number isn't in the canonical map (defensive against the rare stray `ITEM 60` style false positive that survives the regex).
   - If a body item legitimately splits across the document (rare — typically only Item 8 financial statements), keep the longest contiguous slice; downstream consumers expect a single `[start, end]` per item.

5. **Slice content** — `content_text` = cleaned text from end of heading line to start of the **next ITEM anchor only**:
   ```python
   item_starts = sorted(a["span"][0] for a in anchors if a["kind"] == "item")
   ```
   - **Why ITEM-only, not ITEM+PART**: page-running headers repeat `PART I` at the top of every rendered page. Treating PART matches as boundaries truncates Item 1's body at the first page break (e.g. MSFT's Business section drops from ~70KB to ~3KB). Items are sequential within and across Parts, so the next ITEM anchor is always a safe boundary.
   - `body_start` = `text.find('\n', heading_match.end())` (end of heading line). `next_start` = next item anchor start. `char_range = [body_start, next_start]`.
   - If the captured title was empty, fall back to the next non-empty line within ~200 chars after `body_start`.
   - **Trim trailing standalone page numbers from short bodies** before saving: `if len(body) < 500: body = re.sub(r"\n+\s*\d{1,4}\s*$", "", body).rstrip()`. Plain page-number leak is more common than the company-bar variant — short items (Mine Safety, [Reserved], 9C, "Refer to Item 10.") consistently end with `"\n\n32"`-style page numbers because the next heading sits right after the page break. The 500-char gate matters: on a 39K-char Item 1 body, the same regex could chop a legitimate trailing standalone number (table footnote ref, year `2025`, etc.); the leak only happens when the next heading sits adjacent to a page break, which only happens on items short enough to fit on one or two rendered pages.

6. **Classify status** for each item using these rules, in order:
   - `incorporated_by_reference` — body contains "incorporated" within ~5 words of "by reference" in the first ~800 chars **and** body length < 4000 chars. Allow intervening words: `r"incorporat\w*(?:\s+\w+){0,5}\s+by\s+reference"` — filings write "incorporated **herein** by reference", and the bare `incorporated by reference` regex misses these.
   - `reserved` — heading title (trimmed, lowercased) is `"[reserved]"` **or** `"reserved"` (JPM-style filings drop the brackets) **or** body matches `\breserved\b` near the start with body length < 1500.
   - `not_applicable` — body matches `\bnot\s+applicable\b` near the start with length < 1500, **or** body is just `None.` / `None` / `N/A` (case-insensitive, after trim).
   - else `extracted`.

   **Don't transitively classify `incorporated_by_reference`.** A body that says `"Refer to Item 10."`, `"Refer to Note 30"`, or `"Refer to pages 165–314."` is an *internal* cross-reference within the same 10-K. The substantive content lives elsewhere in the document, not in another SEC filing. These stay `extracted` (the body is what it literally is — short, but extracted faithfully). Only the explicit "incorporated … by reference" phrasing triggers IBR. Reasoning: the status field has to be mechanical to be auditable; once it interprets cross-references transitively, two readers will disagree about edge cases (Item 10 itself often half-points to the proxy and half-prints the executive officers list, so "Items 11–14 are IBR via Item 10" is itself ambiguous).

7. **CLI contract**: `argparse` with positional `html_path`, `--out` required. Write the JSON array to `--out`. Stdout MUST be the per-item summary the user-facing reply needs — one line per item in the form `Part {p} Item {n} [{status}] -- {title} ({len content_text} chars)`, followed by a final `items={n} parts={[...]}` line. The main thread captures this stdout directly for Phase 7; producing the summary inside the script eliminates a redundant JSON re-read round trip per filing.

The skill does not ship a single template; if a previous filing's script is a good starting point, the skill may copy it to the new path and adapt — but the resulting script must still be specific to the new filing (tuned regex, tuned cleaner where needed) and committed alongside its output JSON.

## Failure modes and how the design rules out each one

The design choices above each defend against a specific failure mode observed in the corpus. If a future filing surfaces a new mode, add a row here.

| Failure mode | Symptom in validation | Defended by |
|---|---|---|
| Bare `Item 1` page running headers matched as anchors | Item count 30–40+; massive char gaps inside Part I | `ITEM_RE` requires `[\.\:]` trailing punctuation |
| `PART I` page running headers used as slice boundaries | Item bodies truncated to ~3KB despite huge underlying section | Slice on ITEM anchors only; PART used solely for `part` assignment |
| TOC heading wins over body heading | Empty / very short Item bodies; titles look like TOC fragments | Drop TOC-region anchors first (`match_start ≤ ~8000`) when any later anchor exists, then take longest body |
| Both TOC and body candidates tiny → TOC wins by length, breaks monotonicity | Validator: `Item N char_range start <small> < previous end <large>` | Same TOC-region drop rule above (Item 6 `[Reserved]` is the canonical case — JPM 2025) |
| Reserved item with unbracketed `Reserved` title tagged `extracted` | `Item 6 [extracted] -- Reserved (0–2 chars)` | Classifier accepts `[reserved]` **or** `reserved` as title text |
| Trailing standalone page numbers leak into short item bodies | `Item 4 [not_applicable]` body = `"Not applicable.\n\n32"` | Strip `r"\n+\s*\d{1,4}\s*$"` post-extract **only when `len(body) < 500`** — long bodies might legitimately end in a standalone number (year, table footnote ref) |
| Internal cross-references look like extractor bugs | Items 1C / 3 / 7 / 7A / 8 / 11 / 13 / 14 with 17–300 char bodies that say `"Refer to Item 10."` / `"Refer to Note 30"` / `"Refer to pages 165–314."` | Not a bug — JPM and similar filings consolidate sections. Classify as `extracted` (literal body); surface in findings, do not transitively tag IBR |
| No Item 16 → Item 15 sweeps to EOF (financial statements + glossary + auditor reports) | `Item 15 [extracted]` body = ~1MB on JPM-style filings | Expected for filings ending at Item 15. Surface in findings; the validator's `>2000-char gap` rule does not catch upper-bound run-on. Bounding requires a per-filing terminator pattern (e.g. `^Glossary of Terms`) which this skill does not impose |
| Stray "Item 60" / footnote references | Spurious extra Items with random titles | `(\d{1,2})` constrained to `1[0-6]|[1-9]` |
| `incorporated herein by reference` not classified | Items 10–14 tagged `extracted` with ~150-char proxy-statement body | `incorporat\w*(?:\s+\w+){0,5}\s+by\s+reference` |
| Page-footer leak into short Items | `Item 4 [not_applicable]` body = `"Not applicable.\n\nApple Inc. \| 2023 Form 10-K \| 17"` | Strip footer pattern in cleaner OR trim post-extract |
| Inline-XBRL `<ix:hidden>` text appearing in cleaned output | Garbage numeric tokens at the top of the cleaned text; offsets shifted | Cleaner strips `<ix:header>` / `<ix:hidden>` |
| Body has no "Item N." anchors at all (older or non-standard filings, e.g. GE 2018) — the only `Item N.` text lives in a single end-of-doc TOC with page-range pointers (`Item 1.\nBusiness\n4-5, 12-35`); body uses page-numbered narrative without item-anchored headings | All items extracted with 1-17 char bodies (sliced between adjacent TOC entries), every item flagged "largest occurrence < 50 chars" | Out of reach of pure regex anchors. Either author a per-filing section-name → item-number map (e.g. `BUSINESS` → 1, `RISK FACTORS` → 1A) merged into anchors, or fall back to an LLM TOC pass that emits `(item, body_span)` pairs |

## Validation

The validation pass lives in `.claude/skills/10k-extraction/phase6-validation.md` (dispatched per Phase 6 above). The mechanical checks (status sanity, char_range monotonicity, gaps > 2000, page-footer leak, duplicate item records) live in `task3/scripts/extract/_validate.py` — the source of truth. Surface the subagent's findings verbatim under a `Findings` heading in the user reply; those are the loop closure points that tell us what to tune in the next per-filing script.

## How to edit this skill (anti-benchmaxxing)

When a per-filing extraction surfaces a new trait, the temptation is to lift the fix into this skill on the spot. That overfits the general guidance to one document. Before editing SKILL.md, run these four checks:

- **N≥2 before lifting.** A single filing's quirk stays in its per-filing script docstring (or a per-filing constant). Promote to SKILL.md only after the same trait shows up in a second unrelated filing. Per-filing scripts are the dumping ground for one-offs by design — that's why the skill mandates one script per filing.
- **Structural over surface.** Before adding a keyword, phrase, or vendor-specific token to the skill, ask "what's the underlying structural property?" Token rules (`Refer to Item|Note|pages`) almost always have a structural equivalent (`body < 500 chars resolving elsewhere`). If one exists, use it — token rules benchmaxx by definition because the next filing will use different tokens for the same shape.
- **Probes ask questions; they don't assert answers.** Diagnostic guidance should say "eyeball anything under 500 chars" rather than "grep for these three phrases." A probe that pre-decides what to find pre-decides what's interesting and trains the next author to miss the variant.
- **Precise checks belong in `_validate.py`, not in skill prose.** If a finding can be expressed as a regex on output JSON, it's a validator rule (mechanical, surface, fine to be specific). Skill prose is for principles. Conflating the two grows SKILL.md without raising the floor.

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
