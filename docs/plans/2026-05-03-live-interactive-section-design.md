# Live Interactive Section + Click Fast-Fail — Design

**Date:** 2026-05-03
**Scope:** Task 2 (Generalized Browser Automation Agent)
**Motivation:** The 12-case WebVoyager bench just landed at 9/12 (matching baseline)
but three passing cases burned 30–180 s each on `Locator.evaluate: Timeout 30000ms`
errors caused by stale `data-agent-eid` attributes. Cases 101, 102, 105 collectively
wasted ~5 minutes on this. The root cause is twofold: (1) eids issued by an older
`list_interactive` snapshot get reused by the LLM after the underlying snapshot has
been wiped and renumbered by a subsequent snapshot or DOM mutation; (2) the click
tool's fallback chain (`evaluate(tagName, default 30s)` → `click(3s)` →
`evaluate("el.click()", default 30s)`) burns ~63 s before returning the error
that lets the agent recover.

## Goals

1. Eids the LLM sees in the user message are always live — they map to a
   `data-agent-eid` attribute currently present in the DOM.
2. A bad eid surfaces an actionable error obs in well under 5 seconds, not 60+.
3. Tape narrative stays unbounded and unmutated; we only change what flows into
   the user message's runtime sections.

## Non-goals

- Adjusting the system prompt. The live-section + fast-fail error obs together
  teach the LLM what's live; no prompt rule needed.
- Touching narrative rendering (`## Action history` lines stay as-is).
- Changing the tape's structure or persistence to disk.
- Fixing case 107 (orthogonal — its current failure mode is read-pagination).

## Architecture

Three orthogonal pieces in one PR.

### Piece 1 — `list_interactive` returns a short ack

`tools/browser.py::list_interactive` still calls `session.snapshot(offset, limit)`
(eids must be tagged on the live DOM right now in case the LLM clicks an eid in
this same turn — though that doesn't happen with native tool-calling, the
mechanism stays so a same-turn click is correct). The tool's return value
becomes a short ack string:

```
snapshot taken: 47 elements (offset=0, limit=50); see '## Interactive elements (live)' in user message
```

The tape's `obs` field for this step holds the ack string, not the JSON snapshot.
The trace JSONL also records the ack — the full snapshot is reconstructible from
the page state at any time and is captured in the LLM trace through what the model
saw, so trace fidelity is preserved.

### Piece 2 — Live interactive section in the user message

In `loop.py`'s build-context path, immediately before calling `build_messages`:

1. Walk `tape[-3:]`. Find the most recent entry with `action == "list_interactive"`.
2. If found: call `session.snapshot(**that_step.args)` *now*. This re-tags the
   live DOM with fresh `data-agent-eid` attributes and returns the current
   element list.
3. Pass the JSON-serialized list to `build_messages` as a new `interactive_elements`
   kwarg.

`build_messages` (`context.py::build_messages`) gains an optional
`interactive_elements: str | None = None` kwarg. When non-None, append a
`## Interactive elements (live)` section to the user message after the
last-3-obs block.

If no `list_interactive` is in `tape[-3:]`, no snapshot is taken and the section
is not rendered. After 3 actions without `list_interactive`, eid context drops
out of the prompt entirely — the LLM has to call `list_interactive` again to
get fresh ids.

### Piece 3 — Click / type / select_option / press_key fast-fail

`tools/browser.py::click` currently does:

```python
loc = session.locator(id)
try:
    tag = await loc.evaluate("el => el.tagName")  # 30s default timeout
except Exception:
    tag = ""
# … select-handling …
try:
    await loc.click(timeout=3000)
except Exception:
    await loc.evaluate("el => el.click()")        # 30s default timeout — escapes as the visible error
```

Stale-eid worst case: 30 + 3 + 30 = 63 s.

Replace with an upfront existence check:

```python
loc = session.locator(id)
if await loc.count() == 0:
    return f"ERROR: id={id} no longer in DOM. Call list_interactive to refresh; eids are reassigned each snapshot."
# … select-handling …
try:
    await loc.click(timeout=3000)
except Exception:
    await loc.evaluate("el => el.click()", timeout=3000)
```

`Locator.count()` is a non-actionable query — instant when no match. Worst case is
now ~3 s for actually-present-but-unclickable elements, ~50 ms for stale eids.

Same fast-fail in `type_`, `select_option`, `press_key` — all dereference an eid
the same way.

## Cost trade

Re-snapshot per turn while `list_interactive` is in the last 3 actions costs ~1–2 s
of CDP work (`getFullAXTree` + `DOM.resolveNode` + `Runtime.callFunctionOn` per
interactive element). For a session that does `list_interactive → click → read`
(3 turns of relevance), that's 2 extra snapshots beyond the originally-requested
one — ~3 s total. Each saved Locator timeout is ~30 s. Net positive on any case
that previously hit even one stale-eid timeout.

## Tests (TDD red-first)

**Unit — list_interactive ack:** `tests/unit/test_list_interactive_ack.py`. Drive
a fake `browser_session`; assert the tool returns a short ack containing
`"snapshot taken"` and the count, not JSON. Assert `session.snapshot()` was
called once with the requested args.

**Unit — live section rendering:** `tests/unit/test_live_interactive_section.py`.
Drive scenarios with a fake session that records every `snapshot()` call:
- list_interactive at step 0 then 2 unrelated steps → assert one extra snapshot
  call at message build time, user message contains `## Interactive elements (live)`.
- list_interactive at step 0 then 4 unrelated steps → assert no extra snapshot,
  no section in user message.
- Two list_interactive calls (steps 0 and 2) → assert build-time call uses step 2's
  args.

**Unit — click fast-fail:** `tests/unit/test_click_fast_fail.py`. Mock locator's
`count()` returning 0; assert tool returns the stale-eid error in under 1 s
(use `pytest-timeout` or wall-time assertion). Mock `count()` returning 1; assert
the existing click path runs. Same shape for `type`, `select_option`, `press_key`.

**Regression:** existing `test_narrative_history.py` (last-3-obs raw rendering)
must stay green — the change is additive on `build_messages` (new kwarg, default
None) and does not alter narrative or last-3-obs paths for any action other than
`list_interactive`.

## Rollout

Single feature branch off current `task2-web-agent` HEAD. Three commits:

1. `feat(task2): list_interactive returns ack; live interactive section appended on demand`
2. `feat(task2): click/type/select/press_key fast-fail on stale eid`
3. `test(task2): bench re-validation` — `observations.md` update only.

Each commit TDD red-first, ruff clean, pytest -x green between commits.

## Verification

After commit 2, run the full 12-case bench:

```bash
cd task2 && uv run python scripts/bench_webvoyager.py --limit 12
```

Bar: ≥ 9/12 (no regression). Expected: cases 101, 102, 105 wall-time drops
noticeably (no 30 s timeouts). Case 107 may or may not flip — orthogonal.

## Out of scope

- Case 107's read-pagination strategy.
- System prompt edits about eid lifetime.
- Tape narrative mutation.
- Changing the AX-tree enrichment shape (links' href, textbox placeholder, etc).
