# AX-Tree Snapshot Enrichment + Plateau Interrupt — Design

**Date:** 2026-05-03
**Scope:** Task 2 (Generalized Browser Automation Agent)
**Motivation:** Two failure modes observed in WebVoyager bench `webvoyager_20260503T024201Z` (9/12):

- **Case 107 (HF bert downloads, failed)** — agent never reached the model card. Autocomplete row `google-bert/bert-base-uncased` was visible in the page text but had no clickable id (its `option` role is not in `_INTERACTIVE_ROLES`), and the URL never appeared in any `obs`, so the goto-grounding guard correctly blocked direct navigation. Agent looped: search → click wrong id → back → repeat, killed by stuck-detector at step 28.
- **Case 104 (arXiv GAN year, failed)** — secondary; mostly a query-refinement issue, but also illustrates the broader "agent thrashes without re-strategising" pattern.

## Goals

1. Surface enough of the AX tree that the agent can pick the right element on the first try (specifically: link URLs, autocomplete options, form/widget state).
2. Force the agent to verbalise its strategy when it's plateaued, before the stuck-detector terminates the run.

## Non-goals

- Site-specific knowledge in the prompt.
- Replacing the existing stuck-detector (it stays as the hard backstop at 9 consecutive non-novel obs).
- Revising the goto-grounding guard. The guard's URL-must-be-in-prior-obs check is correct; we fix the symptom (no URL in obs) by exposing `href` so URLs naturally land in obs.

## Architecture

Two parallel changes shipped as one PR. No new modules.

- **Snapshot enrichment** in `src/agent/browser_session.py::snapshot()` — additional fields on existing entries, plus one widening of the role filter.
- **Plateau interrupt** in `src/agent/loop.py` — counter on consecutive non-novel observations; at threshold, restrict the next turn's tool set and inject a one-shot system message.

Both layers preserve their public contracts: snapshot still returns a JSON list of entries; loop still emits `step` events with the same shape.

## Snapshot enrichment

Add to each entry, only when present (no `null` fields, keeps JSON tight):

| Field        | Roles                                       | Source                                                        |
|--------------|---------------------------------------------|---------------------------------------------------------------|
| `href`       | `link`                                      | DOM `el.getAttribute('href')`, resolved to absolute URL       |
| `placeholder`| `textbox`, `searchbox`                      | AX `placeholder` property, fallback to DOM attr               |
| `expanded`   | `combobox`, `menubutton`, `button` (if aria-expanded) | AX `expanded` property                              |
| `disabled`   | any                                         | AX `disabled` property                                        |
| `checked`    | `checkbox`, `radio`                         | AX `checked` property                                         |
| `selected`   | `option`, `tab`                             | AX `selected` property                                        |

Widen `_INTERACTIVE_ROLES` to include `option` **only when** the option's nearest ancestor with `role=listbox` (or its controlling combobox) has `expanded=true`. This is the case-107 fix — autocomplete results become clickable.

Token cost: ~5–15 chars per entry that gets a state field. Acceptable; snapshots already dominate prompt size, and these fields displace exploratory clicks that would have cost a full turn each.

## Plateau interrupt

**Threshold:** 4 consecutive non-novel observations. Stuck-detector stays at 9, leaving ~5 post-reason chances to break out before hard termination.

**Mechanism:** `loop.py` tracks `plateau_count`, incremented when the current normalised obs equals the prior obs. Reset only on a *novel non-`reason`* obs. When `plateau_count == 4`, that turn:

1. Inject a one-shot system message:

   > You've made 4 actions with no new information. Next action MUST be `reason` — write what you've tried, what's blocking, and one of: (a) a different URL/source to try, (b) `done(failed, ...)` with the partial answer, (c) `ask_user_question` if a human can break the tie.

2. Restrict the tool list passed to the LLM that turn to `{reason, done, ask_user_question}`.
3. After the `reason` step lands, lift the restriction. The plateau counter does **not** reset on `reason` itself — only a novel non-reason obs resets it. So a reason→reason→reason spiral cannot happen; the agent gets one forced reflection then must produce real action or the stuck-detector eats it normally at step 9.

**Cost:** one extra LLM call per plateau. Trades favourably against the 5+ wasted action steps it short-circuits.

## Tests (TDD red-first, in this order)

**Snapshot (integration tests in `tests/integration/test_browser_tools_interact.py`):**

- `test_snapshot_link_exposes_href` — synthetic page `<a href="/foo">x</a>`, assert entry has `href` resolved to absolute URL.
- `test_snapshot_textbox_placeholder` — `<input placeholder="search">`, assert `placeholder: "search"`.
- `test_snapshot_combobox_expanded_reflects_aria` — toggle `aria-expanded`, assert field flips.
- `test_snapshot_disabled_button` — assert `disabled: true`.
- `test_snapshot_checkbox_checked` — assert `checked: true/false`.
- `test_snapshot_listbox_options_appear_when_expanded` — collapsed listbox → no `option` entries; expanded → options enumerated and clickable by id.

**Loop (unit tests in `tests/unit/`):**

- `test_plateau_count_increments_on_repeat_obs` — feed 4 identical obs, assert counter == 4.
- `test_plateau_count_resets_on_novel_non_reason_obs` — 3 stale + 1 novel `read` obs → counter == 0.
- `test_plateau_count_does_not_reset_on_reason_obs` — 4 stale → forced reason → next turn still constrained if obs unchanged.
- `test_plateau_threshold_restricts_tools_to_reason_done_ask` — at the trigger turn, assert the tool list passed to the LLM is exactly `{reason, done, ask_user_question}`.
- `test_stuck_detector_still_terminates_at_9` — regression: ensure the existing 9-step stuck path still fires when `reason` doesn't break the plateau.

## Verification

After implementation:

1. `uv run pytest` from `task2/` — all green.
2. `uv run ruff check .` — clean.
3. Re-run cases 104 and 107 via `scripts/bench_webvoyager.py --ids 104,107`. Success on either is signal; success on both is the bar. Compare against current bench `webvoyager_20260503T024201Z.json`.

## Out of scope (deferred)

- **Per-URL action budget** (mechanism #1 from the brainstorm). Would catch same-page thrash at a different layer; revisit if the plateau interrupt alone doesn't move the needle.
- **Stricter stuck-detector signal** — `(url, action_type, target_role)` triple tracking. Bigger lift; defer.
- **Cambridge Dictionary case 111** — Cloudflare block, not an agent bug; no design needed.
- **Refine-budget for query refinement loops** — case 104. Watch whether the AX-tree changes alone improve it (richer snapshots may help the agent pick the field-restrict dropdown directly); revisit if not.
