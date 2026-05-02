# Observations: Case 107 (HuggingFace)

**Run:** webvoyager_20260502T143856Z.json · **Trace:** data/traces/dce9cc67d69140dcbc007028b486a061.jsonl · **Status:** failed
**Goal:** Go to https://huggingface.co and How many total downloads does the bert-base-uncased model have on Hugging Face?
**Final answer:** max steps
**Steps:** 50

## Findings

### [P0] Reached max_steps without `done()` despite answer visible six times
- **Where:** the answer span "Downloads last month | 59,513,990" appeared in obs at steps 12, 13, 19, 31, 35, and 41; final event is `done(failed, "max steps")`.
- **Evidence:**
  - step 12 obs (read_grep "download"): `"… } |  | Downloads last month | 59,513,990 | Safetensors | Model size | 0.1B params | Tensor type | F32 |  | Files info |  | Inference Providers | NEW | HF Inference API …"`
  - step 13 obs (read offset=8000): `"… } |  | Downloads last month | 59,513,990 | Safetensors | Model size | 0.1B params …"`
  - step 19 obs (read_grep "downloads"): same span verbatim with `Downloads last month | 59,513,990`.
  - step 35 obs (read_grep "Downloads"): same span verbatim.
  - step 41 obs (read offset=8000): same span verbatim.
  - Final event: `{"type": "done", "payload": {"status": "failed", "answer": "max steps"}}`.
- **Why it's P0:** The HF model card surfaces only "Downloads last month" — there is no separate "total downloads" widget. A defensible answer was on screen six times (either commit `done(success, "59,513,990 downloads last month; HF does not display a cumulative all-time figure")` or `done(failed, "page only shows monthly")`). The agent instead kept searching for a non-existent "total" string and burned the full 50-step budget. Same wrong-process shape as the canirun.ai run — found the data, refused to commit.

### [P0] read_grep grounding guard rejects values the agent observed in earlier reads
- **Where:** step 38, action `read_grep({"pattern": "downloads last month", "window": 500})`; step 40, action `read_grep({"pattern": "59,513", "window": 400})`.
- **Evidence:**
  - step 38 obs: `"ERROR: read_grep('downloads last month') — pattern not present in the goal or in the most recent read. Do not search for values you have only inferred. …"` — yet "Downloads last month" appeared verbatim in the obs of steps 12, 13, 19, 31, and 35.
  - step 40 obs: `"ERROR: read_grep('59,513') — pattern not present in the goal or in the most recent read. …"` — yet "59,513,990" appeared verbatim in the same five prior obs.
- **Why it's P0:** The guard's "most recent read" window is too narrow. Step 39 (the prior `read`) was at offset 0 (header); step 33 (the prior `read` before that) was at offset 4800 (code section); neither contains "Downloads last month" or "59,513". So the guard fired even though the values were grounded in earlier `read` obs and in five `read_grep` obs. The guard treats `read_grep` results as not counting toward grounding, which made the agent unable to drill back into a value it had legitimately seen.

### [P0] End-of-page synthetic obs ignored — agent did not `done()` after exhaustion
- **Where:** steps 47, 48 — both `read({"offset": 0})` returned the new EOF nudge; step 49 went to `list_interactive` instead of `done`.
- **Evidence:**
  - step 47 obs: `"(end of page; tried offsets up to 11200, page length 10846). Try read_grep or done()."`
  - step 48 obs: same string.
  - step 49 action: `list_interactive({"offset": 50})` — sidestepped the nudge by switching tools.
- **Why it's P0:** The new anti-loop EOF-hide for `read` (commit 6fd70ac) fired correctly, but the agent's escape route is to switch to `list_interactive` instead of committing. The nudge text mentions `done()` but does not force exhaustion of all read-shaped tools, so the soft hint is bypassable in one step.

### [P0] click(id=30) timed out 30s with no recovery from ID staleness
- **Where:** step 24, action `click({"id": 30})`.
- **Evidence:** step 24 obs: `"ERROR: Locator.evaluate: Timeout 30000ms exceeded. | Call log: |   - waiting for locator(\"[data-agent-eid=\\\"30\\\"]\") | "`.
- **Why it's P0:** A 30-second wall-block on a single tool call is ~6% of the per-case budget and an order of magnitude over normal tool latency. The element with eid=30 was likely re-rendered after the prior `click(id=31)` at step 21 invalidated the snapshot, and `click` should have failed fast (≤3s, mirroring the existing browser tool's `loc.click(timeout=3000)`) rather than letting `loc.evaluate(...)` run a 30s default. The 3-second click path was followed by a 30-second `evaluate` fallback.

### [P0] read_grep dedup synthetic obs reflect missed extraction, not loop avoidance
- **Where:** step 43 `read_grep({"pattern": "total"})`; step 44 `read_grep({"pattern": "Downloads"})`.
- **Evidence:**
  - step 43 obs: `"(pattern \"total\" already searched at step 34, no new matches.)"`
  - step 44 obs: `"(pattern \"Downloads\" already searched at step 35, no new matches.)"`
- **Why it's P0:** The dedup synthetic obs (commit a8b19e0) is technically correct, but here it fires because the agent re-grepped for terms whose earlier obs already contained the answer. The dedup masks the actual problem (failure to extract from the original `read_grep` obs at step 35) by making the second call cheap rather than highlighting that the agent already had the data.

### [P1] Redundant offset re-walk after click(id=14) reset the page
- **Where:** steps 28-33 — `read({"offset": 0})`, `read({"offset": 1600})`, `read({"offset": 3200})`, `read_grep({"pattern": "download"})`, `read({"offset": 6400})`, `read({"offset": 4800})`.
- **Evidence:** offsets 0/1600/3200/4800/6400 were all visited in steps 7-11; the agent re-walked the same window after step 27's click. The auto-advance cache was correctly invalidated by the click's page mutation, so each re-read served fresh content — but the underlying scan strategy is the same as the first pass.
- **Why it's P1:** Latency, not wrong-process — six redundant steps (~12% of budget). Agent could have used `read_grep` on a remembered keyword (e.g. "Downloads") immediately after the click instead of re-scanning sequentially.

## Hallucination axis

No hallucinated final answer (status `failed`, answer `"max steps"` is meta-state). The pattern `read_grep("59,513")` at step 40 looks superficially like fabrication — the guard's error text accuses the agent of searching for "values you have only inferred" — but in fact "59,513,990" was present verbatim in five prior obs. The agent's grep was grounded; the grounding guard's narrow window mis-flagged it. No URL hallucinations: only `https://huggingface.co/` (goal-supplied) and one `click(id=84)` that resolved to the bert-base-uncased model card.

## Note (not a finding, for context)

The new anti-loop machinery from this branch is observable in the trace: step 47/48's EOF synthetic obs, step 43/44's `read_grep` dedup, and step 49's auto-advanced `list_interactive` (`[auto-advanced 0→50: 0 unchanged since prior list_interactive]`). All three mechanisms fired correctly. The remaining failure mode is upstream (commit-to-answer + grounding guard scope), not the looping shape the anti-loop work targeted.
