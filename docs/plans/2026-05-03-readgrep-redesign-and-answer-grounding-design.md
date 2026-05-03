# read_grep redesign + done() answer-grounding — design

**Date:** 2026-05-03
**Status:** approved (brainstormed, ready for implementation plan)
**Bundle:** one PR, two orthogonal mechanisms
**Triggering trace:** session `51bf0da0bb4f41d290e5325644b10404` (Wolfram integral) — see `observations.md`

## Problem

Two issues observed on the same trace:

1. **`read_grep` loop / dedup ignored.** The agent burned steps 7–48 (42/50 budget) calling `read_grep` with cosmetic argument variations after the page text confirmed `Definite integral:` had no value (Wolfram renders the result as an image). The dedup synthetic `"(pattern X already searched at step N, no new matches.)"` was treated by the LLM as a soft hint to vary `window`, not a hard "stop."
2. **Silent answer hallucination.** The run terminated `done(success, "9")` despite no prior observation containing `"9"`. The LLM computed the integral from prior knowledge and emitted it as if grounded in the page. Bench scoring treats this as success, hiding the failure.

## Goals

- An LLM that calls `read_grep` once on a NO-MATCH gets enough information to abandon the pattern, not retry it.
- An LLM that calls `read_grep` redundantly hits a hard stop fast, not after 40 retries.
- An answer emitted via `done(success, …)` must be evidenced in actual page text the agent observed.

## Non-goals

- Changing the LLM provider, model, or prompt persona.
- Solving Wolfram-specific image-rendered math (out of scope; the redesign correctly forces `done(failed)` on it).
- Bench-scorer rework — the new `evidence` field is additive; existing scorer reads `status`/`answer` and remains unaffected.

---

## Part 1 — `read_grep` redesign

### Current behavior (browser.py:115-125)
```python
text = await session.page.evaluate("document.body.innerText")
idx = text.lower().find(pattern.lower())
if idx < 0:
    return f"NOT FOUND: {pattern!r}"
start = max(0, idx - window)
end = min(len(text), idx + len(pattern) + window)
return text[start:end]
```
Specific deficiencies the bench trace surfaced:
1. **First match only.** Subsequent occurrences invisible. Defeats the structural-navigation use case (long chronological page → find every "2018").
2. **Match position not marked.** When the match is near offset 0 (Wolfram: `"Definite integral:"` at offset 198), `start=max(0, 198-200)=0`, so the output is `text[0:418]` — byte-identical to `read(offset=0)`'s first 418 chars. The LLM literally cannot distinguish "match found near the top of page" from "no match, returned page header." This is why steps 7–8 in session `51bf0da0` looked like silent failures.
3. **`NOT FOUND` returns no information.** Just `NOT FOUND: 'X'`. No page length, no vocabulary hint, no offset. LLM has no signal for what to try next.
4. **`window` is the only knob.** LLM treats it as semantically meaningful; it isn't (only changes snippet width, not which match is returned).

### New shape
```python
async def read_grep(
    pattern: str,
    context: int = 80,
    max_matches: int = 10,
    offset: int = 0,
) -> str
```

`offset` skips the first N matches (mirrors `read`/`list_interactive` semantics; not a character offset).

**Output: many matches, `offset=0`**
```
12 matches for "2018" in page text (4521 chars). Showing matches 0-9 of 12:
  [@312]  …elected 2018-04-03 in a runoff against…
  [@1245] …2018 census recorded 1.2M residents…
  …
  [@4102] …final tour in late 2018 before retiring…
(2 more matches at offsets 4280, 4399 — call read_grep(pattern="2018", offset=10) for the rest.)
```

**Output: many matches, `offset=10`**
```
12 matches for "2018" in page text (4521 chars). Showing matches 10-11 of 12:
  [@4280] …referendum held 2018-11-04 in 31 districts…
  [@4399] …passed in 2018, repealed in 2024…
```

**Output: `offset` past end of match list**
```
NO MORE MATCHES for "2018" at offset=20 (12 total in page). Call read_grep with offset=0..11 or done().
```

**Output: single match**
```
1 match for "Definite integral:" in page text (761 chars):
  [@198] …RANDOM\nDefinite integral:\nStep-by-step solution\n…
```

**Output: no match**
```
NO MATCH for "x = 9" in page text (761 chars). The text contains:
"Definite integral:", "Step-by-step solution", "Indefinite integral:", "Visual representation"
— try one of these, or call done().
```

### Why
- Match-count up front makes "12 matches", "1 match", or "NO MATCH" unmistakable. Eliminates the LLM's window-tweak heuristic.
- All matches with offsets, paged through `offset`, preserves the structural-navigation use case (long chronological page → find every "2018" without flooding the context).
- NO-MATCH vocabulary hint anchors the next pattern to grounded terms, complementing the existing `_check_read_grep_grounding` (which only blocks ungrounded patterns; never suggests grounded ones).
- Renaming `window`→`context` reduces semantic ambiguity ("window" sounds search-relevant; "context" is clearly snippet width).

---

## Part 2 — Dedup escalation (A + C)

### Current behavior (loop.py:372-380, 504-512, 249)
- `_read_grep_seen: dict[str, int]` maps lowercased `pattern` → first-seen `step_idx`.
- Recorded at `loop.py:504-512`, **but only when `obs` does NOT start with `"NOT FOUND:"`**. Consequence: the LLM can re-call the same NOT-FOUND pattern indefinitely and dedup never fires.
- Cleared at `loop.py:249` on any page-text mutation, alongside `read_cache` / `list_interactive_cache` / `_hidden_tools`.
- Synthetic wording: `(pattern "X" already searched at step N, no new matches.)`. Ignores `window`. No escalation, no hide, no streak counter.

### Replacement: hash-based dedup
Drop `_read_grep_seen` entirely. Replace with one cache keyed on the hash of the actual obs string:
```python
self._read_grep_obs_hashes: dict[str, int]   # sha256(obs) → first-seen step_idx
self._read_grep_dedup_streak: int = 0
```

This subsumes the NOT-FOUND-not-recorded gap by construction (NO MATCH outputs hash too) and eliminates the special-casing of match-vs-no-match output prefixes. Pagination through `offset` produces different obs → different hashes → both pass naturally.

### Dispatch flow at the `read_grep` site (replaces loop.py:372-380, 504-512)
1. Call the underlying `read_grep` tool. (Cheap — `document.body.innerText` + string scan + small format pass.)
2. Hash the resulting obs: `h = hashlib.sha256(obs.encode("utf-8")).hexdigest()`.
3. If `h in self._read_grep_obs_hashes`:
   - Replace obs with synthetic A (below).
   - Increment `self._read_grep_dedup_streak`.
   - Tape and trace receive the synthetic, not the real obs.
4. Else:
   - Record `self._read_grep_obs_hashes[h] = step_idx`.
   - Tape and trace receive the real obs.
   - Reset `self._read_grep_dedup_streak = 0`.
5. Any non-`read_grep` tool call also resets `self._read_grep_dedup_streak = 0`.
6. Page mutation (existing `loop.py:249` block): clear `_read_grep_obs_hashes` AND `_read_grep_dedup_streak` alongside the other caches.

### A: synthetic wording
Replace the current `"(pattern X already searched at step N, no new matches.)"` with:
```
DUPLICATE: read_grep returned the same output as step N. Page text has not changed since.
Try a different pattern, a different offset, or call done().
```

### C: hide `read_grep` after K consecutive hash-hits
- When `self._read_grep_dedup_streak >= 2` (i.e. 3rd consecutive duplicate-output call), do both:
  1. Add `read_grep` to `self._hidden_tools` (mirrors `read`/`list_interactive` exhaustion at `loop.py:425/457`).
  2. Replace this turn's obs with the harder message:
     ```
     read_grep is no longer available this turn — it returned identical output 3× in a row.
     Use 'read' with a different offset, list_interactive, or done().
     ```
- Re-enable `read_grep` (remove from `_hidden_tools`) on the next page mutation, mirroring how `read` is re-enabled today.

### What does and does not trigger the hide
| Sequence | Hide? | Why |
|---|---|---|
| `(pat=X)` × 3 with no other tool calls | yes (3rd call) | byte-identical obs each time |
| `(pat=X, offset=0)` then `(pat=X, offset=10)` then `(pat=X, offset=0)` | no | offset=10 produces different obs → hash miss → streak resets |
| `(pat=X, ctx=80)` then `(pat=X, ctx=300)` | depends | different snippet widths → different hash → no synthetic, no hide. The new match-count-up-front output makes context-tweaks visibly low-value but the loop does not punish them. |
| `(pat=X, A, X, X, X)` where A is `read` | no for first 3, yes at 5th | non-`read_grep` call resets streak; 3rd consecutive `pat=X` after the reset trips. |
| `(pat=X)` then navigate (page mutation) then `(pat=X)` | no | hash cache cleared on mutation; treated as fresh call. |

### Why K=2 (3rd hit triggers)
Matches the existing `_wall_streak >= 2` cadence (loop.py:271). One escalation threshold across the codebase.

---

## Part 3 — `done()` evidence requirement (γ)

### Schema change
```python
done(
    status: Literal["success", "failed"],
    answer: str,
    evidence: str = "",
)
```

### Validation (inside the `done` tool, before `LoopDone` is raised)

**For `status="success"`:**
1. `evidence` must be non-empty.
2. `evidence` must be ≥ 10 characters. Prevents trivial `evidence="9"` matching any page containing a 9.
3. After whitespace-collapse + lowercase, `evidence` must be a substring of the concatenation of all prior tape `obs` strings.
4. After whitespace-collapse + lowercase, `answer` must be a substring of `evidence`. Forces citation of page text *that contains the answer*, not a tangentially related sentence.

**For `status="failed"`:**
- `evidence` is optional.
- If provided, only check #3 (must appear in prior obs). Allows `evidence="blocked by cloudflare"` self-reports without forcing a fake answer field.

### On validation failure
- Raise a non-`LoopDone` exception caught by the registry → returned as obs to the LLM. The LLM gets one more turn.
- Wording:
  ```
  ERROR: done() rejected — evidence "X" not found in any prior observation.
  Cite a substring of page text you actually read, or call done(status="failed", evidence="<short reason>").
  ```
  (Variants for the other failure modes: too short, answer not in evidence.)

### `coerce_done_via_llm` (max_steps fallback, loop.py:205-217)
Same schema applies. If the coerced answer cannot be evidenced from tape, force `status="failed"` automatically — the budget is already gone; we don't loop.

### Trace event impact
The `done` trace event payload gains an `evidence` field (additive). Existing bench scorer reads `status`/`answer` only and is unaffected. Bench triage gains a new grep handle: `done.payload.status=="success" && !done.payload.evidence` should be empty by construction — if not, it's a bug.

---

## Tests (TDD red bar before any production change)

### `tests/unit/test_loop_anti_repeat.py` (extend)
- `test_read_grep_obs_hash_dedup_fires_on_byte_identical_repeat`
- `test_read_grep_obs_hash_dedup_does_not_fire_when_offset_produces_new_output`
- `test_read_grep_obs_hash_dedup_does_not_fire_when_context_changes_output`
- `test_read_grep_dedup_streak_hides_tool_after_3_consecutive_dup_outputs`
- `test_read_grep_dedup_streak_resets_on_other_tool_call`
- `test_read_grep_dedup_streak_resets_on_page_mutation`
- `test_read_grep_dedup_synthetic_fires_for_repeated_no_match_pattern`  # replaces the not_found_recorded test — same intent, achieved by hash mechanism
- `test_read_grep_synthetic_wording_says_DUPLICATE`

### `tests/unit/test_browser_read_grep.py` (new)
- `test_read_grep_returns_match_count_and_positions_for_multiple_matches`
- `test_read_grep_no_match_includes_page_vocabulary_hint`
- `test_read_grep_single_match_concise_output`
- `test_read_grep_caps_at_max_matches_and_indicates_more`

### `tests/unit/test_done_evidence.py` (new)
- `test_done_success_requires_non_empty_evidence`
- `test_done_success_evidence_must_be_in_prior_obs`
- `test_done_success_answer_must_be_in_evidence`
- `test_done_success_evidence_min_length_10`
- `test_done_failed_evidence_optional`
- `test_done_failed_evidence_when_provided_must_be_in_prior_obs`
- `test_done_validation_error_returned_as_obs_not_loopdone`
- `test_coerce_done_max_steps_forces_failed_when_unevidenced`

### Bench validation
- Re-run the 12-case WebVoyager bench after the change.
- Specifically verify session-104 (Wolfram integral) ends `done(failed)` (or some non-hallucinated outcome) — no more `done(success, "9")` without evidence.
- Target bar: ≥ 9/12 success, no regressions on prior-passing cases (matches the project's standing bench bar).
- Write findings to `observations.md` per `bench-failure-triage` skill conventions.

---

## Risks

- **False positives on evidence check.** A goal like "How many results does the search return?" with answer "1,234" could fail the substring check if the page renders it as `1234` (no comma) or `1.2K`. Mitigation: whitespace+case normalize only; if false-positive rate is high in bench, add comma-strip and digit-only normalization in a follow-up. Fail-closed is the right default — better a false reject we measure than a hallucination we ship.
- **`coerce_done_via_llm` forced-fail at max_steps could hide cases where the answer was correct but the LLM failed to phrase evidence.** Mitigation: log a `warning` trace event in this path so bench triage can audit.
- **Bench scoring shift.** Some prior `done(success, …)` answers may be ungrounded too. Expect the success rate to drop in the immediate post-merge bench until the LLM adapts. Acceptable: ungrounded "successes" were lying anyway.

## Out of scope (follow-up branches)

- Three-way status (`grounded_success` / `ungrounded_success` / `failed`) — defer; γ is binary.
- Goal-class whitelist for computational answers — explicitly rejected during brainstorm; whitelists the exact failure mode.
- Bench-scorer awareness of the `evidence` field — additive change; do separately if useful.
