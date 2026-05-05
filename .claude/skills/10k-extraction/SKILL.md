---
name: 10k-extraction
description: Use when the user asks to extract Items 1-16 from a SEC 10-K filing into the Task 3 per-item JSON schema (part, item_number, item_title, content_text, char_range, status), or invokes /10k-extraction. The skill is now a thin shim over `task3/src/extract_agent/` — it resolves identifiers, runs the agent CLI, and surfaces validation findings. Per-filing scripts under `task3/scripts/extract_legacy/` remain available as a fallback when the agent's hybrid tools cannot fit a filing's structure.
---

# 10k-extraction

For each new 10-K document, **run the extract_agent** to produce the per-item JSON schema. One filing = one agent run = one JSON.

The agent (`task3/src/extract_agent/`) bundles deterministic backbone (cleaning, anchor finding, slicing, default classification, validation) with a hybrid LLM-driven tool surface (`clean_and_load`, `find_anchors`, `slice_items`, `classify_statuses`, `validate_records`, `write_output`, `done`, plus escape hatches `read_chars`, `regex_search`, `inspect_record`, `update_record`). The LLM diverges from defaults only when the filing demands it. The skill's job is orchestration around the run: identifier resolution, queue tick, validation surfacing, and post-run reflection.

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
- A CIK + accession (look up in `task3/data/index.json`; entries with `endpoint == "archive"` give `path_relative`), **or**
- Nothing — pick the next unchecked row from `test_source.md` at the repo root. That file is the working queue: a markdown table whose rows carry `CIK | Accession (no dashes) | Path` and a `Done` column with `[ ]` / `[x]`. Take the first row with `[ ]` and use its CIK + accession + path as the inputs. If the user names a row by number ("row 5", "next") resolve against the same file. If `test_source.md` is missing or all rows are ticked, fall back to asking for CIK + accession or a path.

If only a company name or year is given (and no row matches), ask once for CIK + accession or a path. Don't guess.

## Workflow

The skill runs as a chain of phases. Phases 3, 5b, and 6 — diagnostic, status splitting, validation — used to be dispatched to subagents from this thread; they are now implemented inside the agent's tool registry and run as part of the single agent invocation in phase 4. The skill keeps them in the table for orientation: they still exist, just on the other side of the agent boundary.

| Phase | Where it runs | Why |
|---|---|---|
| 1. Resolve input | Main thread | Trivial lookup in `task3/data/index.json` |
| 2. Pick identifiers | Main thread | `<cik>` + `<accession-no-dashes>` (strip dashes) |
| 3. Diagnostic pass | **Inside the agent** (`clean_and_load`, `find_anchors`, `regex_search`, `read_chars`) | The agent probes the cleaned text via its own tools rather than dispatching an external subagent |
| 4. **Run agent** | Main thread | One-line bash; the agent CLI runs the full extraction (clean → anchor → slice → classify → status-split → validate → write) and exits |
| 5b. Status splitting | **Inside the agent** (`classify_statuses`) | The agent calls a Haiku-class classifier on eligible bodies and merges segments deterministically — no main-thread fan-out |
| 6. Validation pass | **Inside the agent** (`validate_records`) | The agent runs the validator before `write_output`/`done`; findings surface in the agent's stdout |
| 7. Report | Main thread | Synthesizes the agent's stdout (per-item summary + findings) into the user-facing reply |
| 8. Tick queue | Main thread | If the inputs came from `test_source.md` (or the row is identifiable by CIK + accession), flip that row's `[ ]` to `[x]` via a single `Edit` call. Skip if no matching row exists. Do this AFTER Phase 7 so the tick reflects a completed report, not a half-finished run. |
| 9. Post-run reflection | Main thread | See "Post-run reflection" at the bottom of this file |

**Run command** (phase 4):
```bash
cd task3 && uv run python -m extract_agent --cik <cik> --accession <accession-no-dashes> \
    --out data/extracted/<cik>-<accession>.json
```

If validation flags issues, do **not** silently retry — report the issues with the agent invocation so the next iteration can adjust the agent (or fall back to a legacy per-filing script — see "Falling back to a legacy per-filing script" below).

Final reply to user: JSON path + a one-line-per-item summary (`Part {p} Item {n} [{status}] -- {title} ({len content_text} chars)`) + a `Findings` section with whatever the validation subagent surfaced.

### Phase 3, 5b, 6 — implemented inside the agent

Phases 3, 5b, and 6 are **implemented inside the agent — see `task3/src/extract_agent/tools/`**. The skill no longer dispatches subagents for them.

- **Phase 3 (Diagnostic)** — the agent probes the cleaned text via `task3/src/extract_agent/tools/find_anchors.py`, `task3/src/extract_agent/tools/regex_search.py`, and `task3/src/extract_agent/tools/read_chars.py` (after `task3/src/extract_agent/tools/clean_and_load.py`). It decides per-filing whether the defaults suffice or whether to diverge.
- **Phase 6 (Validation)** — `task3/src/extract_agent/tools/validate_records.py` runs the same mechanical checks (status sanity, char_range monotonicity, gaps > 2000, page-footer leak, duplicate item records) before `write_output`/`done`. Findings appear in the agent's stdout under a `Findings` heading; surface them verbatim in the user-facing reply.

### Phase 5b — status splitting inside the agent

Status splitting is implemented inside `task3/src/extract_agent/tools/classify_statuses.py`, which calls a Haiku-class classifier per eligible record and merges segments deterministically (the merge logic is `task3/src/extract_agent/merge_5b.py`, the same module the legacy fan-out used). The agent does the eligibility filter, the fan-out, and the merge; this thread does not.

The eligibility filter prose below is preserved verbatim because it is load-bearing if anyone consults `phase5b-status-split.md` or `task3/src/extract_agent/tools/classify_statuses.py`:

**Eligibility filter** — only fan out for records where ALL of:
- `status == "extracted"` (other statuses are mechanical and unambiguous);
- `len(content_text) >= 200` (shorter bodies — `"Refer to Item 10."`, `"Not applicable."` — can't meaningfully split, save the call);
- `not (item_number == "15" and len(content_text) >= 500_000)` (Item 15 EOF run-on contains financial statements + glossary + auditor reports, not item content; sub-classifying that would dominate cost without honoring the schema's intent);
- `re.search(r"incorporat\w*(?:\s+\w+){0,8}\s+by\s+reference", content_text, re.IGNORECASE)` matches at least once. Without this anchor phrase there is no plausible IBR sentence in the body, so Phase 5b has nothing to find — the call is guaranteed to return one full-body `extracted` segment. Cost asymmetry is what justifies the gate: a false negative here silently drops a real IBR (expensive); a false positive just dispatches a Haiku call that returns the no-op single segment (cheap). The `{0,8}` window keeps `"incorporated **herein** by reference to"`, `"incorporated, by reference,"`, `"is hereby incorporated by reference"` and similar variants in scope. Do **not** try to also exclude negation (`"not incorporated by reference"`) or table-header occurrences here — let those pass through; precision lives in Phase 5b itself, the pre-filter is recall-only. Empirically (Berkshire 2025, MSFT FY2020) ~80% of `extracted` records contain no IBR phrase at all and skip the dispatch with no quality loss.

## Authoring guidance (the agent's deterministic defaults)

These are the agent's deterministic defaults (encoded in `task3/src/extract_agent/cleaner.py`, `task3/src/extract_agent/anchors.py`, `task3/src/extract_agent/slicer.py`, `task3/src/extract_agent/default_classify.py`, and `task3/src/extract_agent/validate.py`). They survive the migration; the agent's hybrid-tool surface lets the LLM diverge from them only when the filing demands it. Read this section to understand what the agent does by default and why.

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

The agent's tool surface (`clean_and_load`, `find_anchors`, `slice_items`, `classify_statuses`, `validate_records`, `write_output`, `done`, plus escape hatches `read_chars`, `regex_search`, `inspect_record`, `update_record`) lets the LLM diverge from these defaults when the filing demands it — e.g. tightening the cleaner for an unusual XBRL container, or overriding a single record via `update_record` after `inspect_record` reveals a bad slice.

## Falling back to a legacy per-filing script

If the agent fails repeatedly on a filing whose structural pattern doesn't fit the hybrid tools (the canonical case is GE 2018: no `Item N.` body anchors, all items live in an end-of-doc cross-reference index), it is still acceptable to copy one of the frozen scripts from `task3/scripts/extract_legacy/` as a starting point and adapt it. The output schema is unchanged. Surface the fallback in the user-facing reply so the next iteration knows not to retry the agent on this filing.

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
| Body has no "Item N." anchors at all (older or non-standard filings, e.g. GE 2018) — the only `Item N.` text lives in a single end-of-doc cross-reference index with **non-contiguous** page-range pointers (`Item 1.\nBusiness\n4-5, 12-35, 43-44`); body uses page-numbered narrative without item-anchored headings | All items extracted with 1-17 char bodies (sliced between adjacent TOC entries), every item flagged "largest occurrence < 50 chars" | Out of reach of pure regex anchors. Realized approach in `task3/scripts/extract_legacy/40545-000004054519000014.py` (GE 2018): parse the `FORM 10-K CROSS REFERENCE INDEX` table to obtain `(item, page_spec)`, build `page_number → (start, end)` from a recurring page-footer regex (e.g. `r"GE 2018 FORM 10-K\s+(\d+)"`), then slice each item's longest contiguous page range. Multi-range items legitimately overlap (Item 1 ⊂ Item 7 in GE 2018); validator monotonicity warnings are expected, not bugs. Items with page spec "Not applicable" → `not_applicable`; with proxy footnote markers `(a)/(b)/(c)/(d)` only → `incorporated_by_reference`. **Stays N=1 / per-filing** — do not lift the page-footer regex or cross-reference parsing into SKILL.md until a second filing demonstrates the same pattern with a different vendor token. This is the canonical "fall back to a legacy per-filing script" case (see section above). |

## Validation

Validation runs inside the agent via `task3/src/extract_agent/tools/validate_records.py` (which delegates to `task3/src/extract_agent/validate.py` — the source of truth for the mechanical checks: status sanity, char_range monotonicity, gaps > 2000, page-footer leak, duplicate item records). Findings appear in the agent's stdout; surface them verbatim under a `Findings` heading in the user reply. Those are the loop closure points that tell us what to tune in the agent (or what justifies a legacy fallback).

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
# typical end-to-end
cd task3
uv run python -m extract_agent --cik 320193 --accession 000032019323000106 \
    --out data/extracted/320193-000032019323000106.json
```

## Post-run reflection (Phase 9)

After Phase 7 (the user-facing report) and Phase 8 (queue tick), spend one short pass asking: *of the things this run surfaced, what could the skill / phase docs / subagent prompts / validator have caught earlier?* The goal is not "what went wrong on this filing" — Phase 7 already covered that — but "what guidance, if it had been present at the start of the run, would have prevented the friction we saw?"

This reflection has two failure modes and the section structure exists to ward off both. The first is **silence**: shipping the run, ticking the box, and never noticing that the same correction is being made on every filing — the skill never learns. The second is **benchmaxxing**: every quirk gets lifted to skill prose and SKILL.md grows into a list of last-filing's mistakes. The four anti-benchmaxxing checks at the top of "How to edit this skill" exist for the second mode; this section's discipline exists for the first.

### How to run the reflection

For each candidate finding, write one bullet of the form:

> **{symptom in plain words}** — *destination:* `skill prose | phase doc | subagent prompt | _validate.py | per-filing script docstring | drop`. *Why that destination:* {one sentence, naming which of the four anti-benchmaxxing checks this clears or fails}.

A finding without a destination decision is not a finding yet — it's an observation. Either it earns one of the destinations above, or it stays in the per-filing script's docstring and waits for N≥2.

### The four checks, restated for reflection use

These are the same checks as "How to edit this skill" — repeated here because reflection is when you'll be tempted to skip them.

1. **N≥2 before lifting.** If this is the first time the trait has appeared, the destination is the per-filing script docstring or "drop", not skill prose. Cross-reference the failure-modes table and prior per-filing script docstrings before claiming N=1.
2. **Structural over surface.** A vendor-specific token (footer string, phrase, exact item title) belongs in the per-filing script. A *property* of the document or the pipeline (e.g. "Haiku emits ASCII quotes regardless of body encoding", "TOC anchors cluster in the first ~8 KB") can live in skill prose.
3. **The destination has to be load-bearing in code, not in prose.** If the fix is a prose rule the next author has to remember, ask whether the same fix can live in `_validate.py` (mechanical check), the merge script (deterministic transform), or a Phase 5b prompt constraint (closes the loop without manual recall). Prose rules that depend on a human noticing them are the weakest destination — use them only when no code destination exists.
4. **Reflection output ≤ ~5 bullets.** If you have ten findings, eight of them are observations, not findings. Pick the two most load-bearing.

### What to look for (questions, not assertions)

These are deliberately questions — answers will differ per filing, and a probe that pre-decides what's interesting will train future-you to miss the variant.

- Did the merge step reject any subagent return for a reason that would recur on every filing? (E.g. character-class assumptions in snippet matching, ambiguous anchors that token-models predictably miss.)
- Did the per-filing script need a hand-tuned constant that the diagnostic phase could have surfaced if its probes asked one more question?
- Did Phase 6 surface a warning that was actually expected (proxy IBR stubs, Item 8 auditor signature run-on)? If so, is the validator over-warning, or is the warning load-bearing on a different filing?
- Did the user-facing reply have to explain a "this is fine" that the validator could have suppressed?
- Did a phase doc say something that the subagent demonstrably ignored? (Strengthening the prompt is rarely the right answer — usually the right answer is to make the merge code defensive against the violation.)

### Where the bullets land

Apply them in the same run if they pass the four checks. Skill prose edits, phase doc edits, and `_validate.py` edits all happen in this thread; per-filing script docstring edits stay with the filing. If a bullet doesn't pass the four checks, leave it on the floor — re-deriving it next run is cheap, and overfitting is expensive.
