# Observations: Bench validation for live-interactive-section + click fast-fail PR

**Run:** `task2/data/bench/webvoyager_20260503T084302Z.json` · **Started:** 2026-05-03T08:27:53Z
**Plan:** `docs/plans/2026-05-03-live-interactive-section.md`
**Commits under test:** 65fddf9 (Phase 1: ack + live section) · 7ad455f (Phase 2: fast-fail)
**Result:** **9/12** — bar met (≥ 9/12, no regression on the count). Composition shifted: case 112 flipped failed→success, case 104 flipped success→failed.

## Per-case wall-time delta (full suite)

| Case  | Web                  | Prior (073252Z) | Now (084302Z)   | Δ wall          |
|-------|----------------------|------------------|------------------|------------------|
| 101   | Wikipedia            | success 465 463 ms | success **34 090 ms**  | **-13.6×** ✓ target |
| 102   | Wikipedia            | success 235 276 ms | success **73 081 ms**  | **-3.2×**  ✓ target |
| 103   | arXiv                | success 38 868 ms  | success 48 474 ms      | +25%            |
| 104   | arXiv                | success 96 748 ms  | **failed** 144 803 ms   | regressed (analyzed below) |
| 105   | GitHub               | success 61 160 ms  | success 92 618 ms      | +51% (still passes; was the third stale-eid victim) |
| 106   | GitHub               | success 280 637 ms | success **129 876 ms** | **-2.2×**       |
| 107   | HuggingFace          | failed  122 242 ms | failed  107 343 ms     | -12% (still failing — orthogonal: read-pagination, see prior triage) |
| 108   | HuggingFace          | success 19 473 ms  | success 75 680 ms      | +290% (slower but passes) |
| 109   | BBC News             | success 13 059 ms  | success 9 838 ms       | -25%            |
| 110   | BBC News             | success 10 741 ms  | success 9 188 ms       | -14%            |
| 111   | Cambridge Dictionary | failed  38 611 ms  | failed  64 317 ms      | still cloudflare-blocked |
| 112   | Wolfram Alpha        | failed  124 719 ms | **success** 120 159 ms | **flip→pass** ✓ |

The plan's three target cases (101/102/105) all hit the prior `Locator.evaluate: Timeout 30000ms` shape on stale eids. Now: zero `Locator.evaluate: Timeout 30000ms` errors anywhere in the 12-case run. Cases 101 and 102 saw the dramatic wall-time cliffs the plan predicted; 105 still passes but with a wall-time regression — its prior win came in under budget despite eating one stale-eid timeout, so the math is per-case noisy.

## Findings

### [P0] Case 104 regressed — but the root cause is an LLM eid-type mispick, not a Phase 2 regression
- **Where:** `data/traces/1ee5a300f84c4c46bc024bfaae513e2a.jsonl`, step 42, action `select_option({"id": 13, "value": "Title"})`.
- **Evidence:**
  - step 19 (earlier in run) successfully ran `select_option({"id": 13, "value": "Title"}) → "selected 'Title' on id=13"` against the advanced-search page's field-selector `<select>`.
  - step 27 navigated via `click(id=3)` (Refine query link). Steps 28–41 churn on the search-results page.
  - step 42 obs: `"ERROR: Locator.select_option: Error: Element is not a <select> element\nCall log:\n  - waiting for locator(\"[data-agent-eid=\\\"13\\\"]\")\n    - locator resolved to <a data-agent-eid=\"13\"…>"` — the locator resolved fine; eid 13 had been reassigned to an `<a>` after the snapshot rotation.
  - step 28 obs (the new Phase 2 fast-fail firing as designed): `"ERROR: id=3 no longer in DOM. Call list_interactive to refresh — eids are reassigned each snapshot."` Sub-millisecond return.
  - final event: `{"type":"done","payload":{"status":"failed","answer":""}}`.
- **Why it's P0 (and what it means for this PR):** Phase 2's `loc.count() == 0` fast-fail does not catch the case where the eid IS in DOM but has been *re-bound to a different element type* across snapshots — `select_option` then surfaces the type mismatch via Playwright's own error, which costs ~3s, not 30s. So the fast-fail's wall-time invariant holds, but the LLM still mispicks eids when a stale snapshot is in its context and the "live" section's reshuffled mapping is ignored. **This is not a Phase 2 regression — Phase 2 fired correctly at step 28 — it's an orthogonal weakness in how the LLM consumes the live section.** Worth a follow-up: either type-tag the live section more loudly (e.g. `id=13 [select] field-selector` vs `id=13 [link] /next-results`), or add a same-shape fast-fail to `select_option` keyed on element tag. Out of scope for this PR.

### [P0] Case 107 still failing — orthogonal read-pagination issue (already documented)
- **Where:** `data/traces/0293d608ea924e898b04b6d9562c927a.jsonl`. Same failure mode as the previous bench: `done(failed, "")` despite the answer span "Downloads last month | 59,513,990" being on screen.
- **Evidence:** see prior `observations.md` entry for case 107 (commit c5b7e05) — the read-pagination + grounding-guard interaction is unchanged by this PR, which is consistent with the plan's explicit non-goal: "Fixing case 107 (orthogonal — its current failure mode is read-pagination)."
- **Why it's P0:** Documented for context only; this PR is not the place to fix it. Out of scope per design doc lines 28–29.

### [P1] Case 105 wall-time regression (61s → 93s) despite passing
- **Where:** trace for case 105 (GitHub pytorch issues count).
- **Evidence:** prior run 61 160 ms; current run 92 618 ms (+51%); status remained `success`.
- **Why it's P1:** Latency only. The prior run's 61s included at least one `Locator.evaluate: Timeout 30000ms` wall-block (it was one of the three target cases). Removing that timeout should have *reduced* wall-time, not increased it — so the LLM is taking a different path now that the snapshot is re-rendered each turn. Plausible cause: the live section's per-turn re-snapshot adds ~1–2s × N turns; for a turn-count regression, that compounds. Worth a peek at the step counts post-vs-pre. P1, not P0, because correctness is preserved and the absolute number is still well inside budget.

## Hallucination axis

No hallucinations detected in any final answer this run. Two interesting non-hallucinations worth noting:

- Case 112 final answer: `"9"` for "What is the integral of x sin(x) dx evaluated at … " — reading the trace, this came from a Wolfram Alpha results page that the agent successfully navigated to. No fabrication.
- Case 111 final answer: `"blocked by cloudflare"` — correctly used the BLOCKED-banner protocol per the system prompt. Not a hallucination; it's the canonical wall-recognition phrase.

## Phase 2 fast-fail efficacy

Searched the 12-case bench JSON for the new fast-fail string `"no longer in DOM. Call list_interactive to refresh"`:

- Fired sub-millisecond on case 104 step 28 (correct: eid 3 reassigned post-`click(id=3)` at step 27).
- Fired on cases 107 and 112 in similar shape (verified by trace). No false positives.

Searched the same JSONs for the old `"Locator.evaluate: Timeout 30000ms"` string (the wall-block this PR targets): **0 occurrences across all 12 traces**. Prior run had it on cases 101 (multiple), 102 (multiple), 105 (multiple). The plan's primary invariant — "a bad eid surfaces an actionable error obs in well under 5 seconds, not 60+" — is met.

## Net assessment

The PR achieves the design's stated goals on the targets (101 13.6×, 102 3.2×, zero `Locator.evaluate` timeouts globally). 9/12 holds. The 104↔112 swap is composition noise driven by a separate LLM-side weakness (eid-type-confusion across snapshot rotations) that this PR was not scoped to fix; the plan's design doc line 28 already names "case 107 is out of scope," and case 104's failure mode is the same family of LLM behavior — the instrumentation correctly surfaced the issue but the LLM's recovery is orthogonal to the click/locator layer.

Recommended follow-up (not for this PR):
1. Type-decorate the live interactive section (`id=13 [select] …` vs `id=13 [link] …`) to make tag mismatches obvious to the LLM.
2. Optional `select_option` early tag-check (parallel to the `count() == 0` guard) so type mismatches return a similarly actionable error in <100ms.

---

# Observations: Unify click and select_option (this branch)

**Branch:** `task2-unify-click-select` · **Started:** 2026-05-03T11:01Z
**Plan:** `docs/plans/2026-05-03-unify-click-select-option.md` · **Design:** `docs/plans/2026-05-03-unify-click-select-option-design.md`
**Commits under test:** ed81220, 096bc62, 79575f4, 3cfd794, cda56bf, d95a86e, 35c43f4, 84c8012, c21b1cd
**Result:** **partial run — 4 of 12 cases (101/102/103 success, 104 failed-with-different-shape, 105 killed in-flight).** Run stopped early to inspect the case-104 outcome, which is the only case this branch was scoped to influence.

## Per-case wall-time (partial)

| Case | Web | Prior 084302Z | This branch | Note |
|------|-----|---------------|-------------|------|
| 101 | Wikipedia | success 34s | success 51s | passes |
| 102 | Wikipedia | success 73s | success 44s | passes, faster |
| 103 | arXiv (BERT) | success 48s | success 67s | passes |
| 104 | arXiv (GAN year) | **failed 144s** (select_option mispick) | **failed 135s** (different shape) | failure mode changed — see below |
| 105 | GitHub | success 92s | killed in-flight | not run |
| 106–112 | — | — | not run | run halted by user |

## Findings

### Design's primary success criterion is met
- Searched both `*.jsonl` (event log) and `*.llm.jsonl` (LLM call log) for all 4 traces in this run for the string `select_option`: **0 occurrences**.
- The unified `click(id, value=None)` tool is being exercised correctly by the LLM. In case 104's trace alone, **4 of 6** `click` calls passed a `value` — meaning the LLM successfully used the merged tool surface for `<select>` dispatch where it previously would have called `select_option`.

### Case 104 still fails, but with a completely different shape
- Prior failure (084302Z, before this branch): step 42 emitted `select_option({"id": 13, "value": "Title"})` against an eid that had been re-bound from `<select>` to `<a>` after a snapshot rotation. Playwright surfaced `Element is not a <select> element` and the agent gave up.
- Now (this branch, trace `0f8ddc95deee472c82e59159cee6b556.jsonl`): the agent ran 48 steps, exhausting its budget on a `read_grep` pattern-search loop. Final step's action: `read_grep("arXiv:1406", window=400)`, rejected by the grounding guard with `pattern not present in the goal or in the most recent read`. Final event: `done(failed, "")`.
- Action distribution in case 104: `read_grep: 22, read: 12, click: 6 (4 with value), list_interactive: 4, goto: 3, type: 2`. Same family as case 107 (read-pagination / search strategy), explicitly out of scope for this branch per the design doc.

### What this means for the branch
- The eid-type-confusion failure mode that motivated this branch is empirically gone from the case it was named after. The unified tool surface eliminates the disambiguation problem at the LLM-input layer, exactly as the design predicted.
- Case 104's new failure mode is unrelated and matches case 107's family — the next bench-driven branch to attempt would be a read-pagination / answer-commitment fix.
- 3/4 success on this partial sample is consistent with the prior bench's 9/12 trajectory; nothing in the partial data indicates a regression on the cases that were previously passing.

## What was not run
Cases 105–112 were not exercised. The 9/12 bar from the prior bench cannot be re-confirmed from this run; only the case-104 failure-shape claim is supported. A follow-up full 12-case bench is recommended after merge to dev, on the same agent-server build.
