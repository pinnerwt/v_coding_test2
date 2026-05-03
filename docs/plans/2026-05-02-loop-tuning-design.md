---
title: Loop tuning — diff metric, no-progress threshold, goto allowlist
date: 2026-05-02
task: task2
status: approved
---

# Loop tuning — diff metric, no-progress threshold, goto allowlist

Five small, independent changes to `task2/src/agent/loop.py`, `page_diff.py`, `context.py`, `tools/browser.py`, and `server.py`. All grounded in observed bench behaviour. No new modules; no API changes outside the agent crate.

## 1. Diff injection: switch metric from chars to lines

**Today.** `should_inject_diff` decides via `diff_char_size` (sum of chars in added+removed lines, line-level unified diff). Threshold 500 chars. Cap on rendered lines: 10. Both wired in `loop.ReactLoop.__init__` (`small_diff_threshold=500`, `diff_inject_max_lines=10`).

**Change.** Make the threshold a *line count*, not a char count.

- `diff_char_size` → renamed `diff_line_size`, returns `count of added + removed lines` from the same unified-diff walk.
- `should_inject_diff` calls the renamed function; signature unchanged (`previous, current, threshold`).
- `_small_diff_threshold` default → **50** (lines).
- `_diff_inject_max_lines` default → **50**. With threshold == cap, when we *do* inject we never truncate.

**Why.** A 500-char threshold is opaque — a single sentence can blow it. Lines map to how the LLM actually reads the diff block. 50/50 is a meaningful "small page mutation, full detail" window without a hidden cap.

**Tests.**
- Update `tests/test_page_diff.py` (and any callers) to use line counts. Existing case structure stays; expected numbers shift.
- New case: 30-line diff → injects all 30 lines. 60-line diff → suppressed entirely.

## 2. URL notes scope — no change

Already correct. `NotesStore` defaults to `query_strip=True` (`notes_store.py:10-14`), keying on `scheme://netloc/path`. Both `notes.get(url)` (per-turn read in `loop.run`) and `notes.set(url, …)` (in `distill.distill_page_knowledge`) use the same `_current_url()`, so reads and writes hit the same per-path row. No site-wide `/` aggregation, no full-URL fragmentation. Documented here so future readers don't re-litigate.

## 3. Lower no-progress thresholds to match the visible context window

**Today.**
- `NO_PROGRESS_GIVEUP = 12` (`loop.py:48`) — force-done after 12 consecutive non-novel observations.
- `NOVELTY_WINDOW = 10` (`context.py:7`) — used by `_no_progress()` and the "Novel observations in last N steps: x/N" prompt line.
- `K_RECENT = 8` (`context.py:6`) — only the last 8 steps are rendered as full assistant/tool message pairs; older steps become one-line summaries.

**Change.**
- `NO_PROGRESS_GIVEUP` → **9**.
- `NOVELTY_WINDOW` → **8**.
- `K_RECENT` stays at 8.

**Why.** With `K_RECENT=8`, by the time the old `streak == 12` trips, four duplicates already live in the lossy summary form — the model's working memory only sees 8 of the dupes. Threshold 9 fires the moment all 8 visible recent steps are duplicates. Aligning `NOVELTY_WINDOW` to 8 makes the in-prompt "x/N novel" counter cover exactly the same span the model can re-read in detail; the giveup at 9 is "one past the visible window" — the natural off-by-one.

**Tests.**
- `_no_progress()` cases in `tests/test_loop_*.py`: shrink the constructed tape lengths from 11 (`NOVELTY_WINDOW + 1`) to 9. Add explicit constants pulled from `agent.context` so we don't hard-code numbers.
- Force-done by `no_progress` test: replace 12-tape with 9-tape.
- `_histogram_line` / `_novelty_line` snapshot — re-record with the new window.

## 4. `restrict_goto` default → True everywhere

**Today.**
- `Config.restrict_goto` env-default already `True` (`config.py:34`).
- `build_browser_tools` and `build_browser_tool_list` both default to `restrict_goto=False`.

**Change.** Flip both function defaults to `True`. Production server unchanged (it was already opt-in). Tests that constructed bare `build_browser_tool_list(session)` and relied on unrestricted goto must opt back in via `restrict_goto=False, allowlist_sources=None` or supply an allowlist.

**Why.** Defaults should match the safer production posture. Bench traces have shown the model occasionally invent URLs from prior knowledge; restrict-on by default closes that path.

**Tests.**
- Audit every `build_browser_tool_list(...)` and `build_browser_tools(...)` call in `tests/`. Each call site either:
  1. Already passes `restrict_goto=True` → no change.
  2. Tests goto round-trips → add `restrict_goto=False` explicitly to keep the old behaviour, OR pass an allowlist source that contains the URL under test.
  3. Doesn't touch goto → no behavioural change.

## 5. Allowlist auto-extension via visited-URL trail

**Today.** `server.py:76-83` builds `allowlist_sources` from `[goal] + [step.obs for step in tape] + [browser.page.url]`. URLs are extracted from each source by regex. The current page URL is always allowlisted. Older URLs only survive in the allowlist if they happened to appear in some `obs` string (e.g. surfaced by a `read`). The `click` obs is just `"clicked id=5"` — no URL — so a URL reached purely by clicking and then navigated away from is *not* re-`goto`-able even though we genuinely visited it.

**Change.** Maintain a `visited_urls: list[str]` in the server scope. Append to it every turn at the *top* of the loop iteration, after the page snapshot, deduping consecutive identical URLs. Add the trail into `allowlist_sources`:

```python
allowlist_sources = lambda: [goal] + tape_obs + [browser.page.url] + visited_urls
```

**Why this design over per-tool hooks.** Top-of-turn capture is one update site, catches every navigation regardless of cause (`goto`, `back`, `click`, form submit, `select_option`, `press_key` Enter, JS-driven nav inside any of those). No per-tool plumbing. Bounded growth: cap the trail at, say, 64 entries (FIFO). Every entry is a URL the browser actually reached, so the "no model-imagined URLs" invariant is preserved.

**Where the capture lives.** Cleanest spot: a small callback the loop calls each turn, supplied by the server. To avoid leaking server concerns into `ReactLoop`, simpler:

- Hold the trail in `server.py` next to `loop_holder`.
- `ReactLoop` already calls `_current_url()` once per turn; expose that URL via a `record_visit` callback wired in by the server (`ReactLoop.__init__(..., on_visit=visited_urls.append)` — default `None`). One call per iteration, deduped against `visited_urls[-1]` to avoid runaway growth on multi-turn same-page reads.

**Cap.** 64 entries, FIFO. A run that legitimately visits >64 distinct pages is already pathological; the dedup-against-last keeps normal runs at trail-length ≈ unique-pages-visited.

**Tests.**
- New unit: feed a fake `ReactLoop` a tape that navigates A → B → C → A; assert all four URLs are in the trail (dedup is consecutive-only) and that `_extract_urls` over the trail yields {A,B,C,A}.
- New integration in `tests/test_server_goto_allowlist.py` (or extend existing): click navigates from A → B → away; subsequent `goto(A)` succeeds because A is in the trail.
- FIFO cap: push 100 distinct URLs, assert trail length == 64 and oldest 36 dropped.

## Out of scope

- Per-tool nav hooks (we picked top-of-turn).
- Site-wide URL notes aggregation.
- Removing `K_RECENT` or making it configurable.
- Any change to how the diff block is rendered (header line, +/- prefix); only the gating metric changes.

## Rollout

All five changes are independent. Suggested commit order so each commit's tests stand alone:

1. `refactor(task2): rename diff_char_size → diff_line_size, switch metric to lines`
2. `feat(task2): set diff inject defaults to 50/50 lines`
3. `feat(task2): tighten no-progress giveup to 9 and novelty window to 8`
4. `feat(task2): default restrict_goto=True in browser tool builders`
5. `feat(task2): add visited-URL trail to goto allowlist`

Each commit: TDD — failing test first, minimal change to green, ruff clean.
