# Unify `click` and `select_option` into one tool — design

## Motivation

Bench `webvoyager_20260503T084302Z.json` case 104 (arXiv) failed because the LLM emitted `select_option({"id": 13, "value": "Title"})` against an eid that had been re-bound from `<select>` to `<a>` after a snapshot rotation. The existing `click → SELECT` redirect (`browser.py:147–155`) and the Phase 2 `count() == 0` fast-fail both fired correctly elsewhere in the same trace — the failure mode here is **eid-type confusion across snapshots**, which neither guard catches because the eid was in the DOM, just bound to the wrong element type.

The recovery-side fix (mirror the click guard onto `select_option`) was considered and rejected: it saves ~3 s wall and improves error wording, but does not change pass-rate, costs an extra round-trip on every `select_option` call, and leaves the underlying disambiguation problem with the LLM.

The design here removes the disambiguation problem entirely by eliminating the choice between `click` and `select_option` from the LLM's tool surface.

## Approach

Collapse `click` and `select_option` into a single tool the LLM sees as `click(id, value=None)`. The tool inspects the element's tag at runtime and dispatches:

- `tag == "SELECT"` → `loc.select_option(value)`
- otherwise → `loc.click()` (with the existing `loc.evaluate("el => el.click()")` fallback)

`select_option` is removed from the LLM-visible tool registry. `type_` stays separate (filling text is a distinct conceptual op, and there is no failure-mode evidence motivating its merge).

## New `click` shape

```python
async def click(id: int, value: str | None = None) -> str:
    try:
        loc = session.locator(id)
        if await loc.count() == 0:
            return (
                f"ERROR: id={id} no longer in DOM. Call list_interactive "
                "to refresh — eids are reassigned each snapshot."
            )
        try:
            tag = await loc.evaluate("el => el.tagName", timeout=3000)
        except Exception:
            tag = ""
        is_select = tag == "SELECT"
        if is_select and value is None:
            return (
                f"ERROR: id={id} is a <select>; pass value=<one of the "
                "`options` entries from list_interactive>."
            )
        if not is_select and value is not None:
            return (
                f"ERROR: id={id} is not a <select>; `value` is only for "
                "<select>. Call list_interactive to refresh — eids are "
                "reassigned each snapshot."
            )
        if is_select:
            await loc.select_option(value, timeout=5_000)
            return f"selected {value!r} on id={id}"
        try:
            await loc.click(timeout=3000)
        except Exception:
            await loc.evaluate("el => el.click()", timeout=3000)
        return f"clicked id={id}"
    except Exception as e:
        return f"ERROR: {e}"
```

Two type-mismatch guards preserve the type-confusion detection that the separate-tool design got for free:

1. SELECT + no `value` → asks for a value; cheap recovery (LLM retries with value from the live section's `options` field).
2. non-SELECT + `value` → flags stale eid; same actionable text as the existing `count() == 0` ERROR ("Call list_interactive — eids reassign each snapshot"). This is the case-104 failure shape: the LLM's stale memory says "id=13 is a select," the new snapshot says it's a link, the guard surfaces the contradiction.

## Tool-registry changes

`build_browser_tools` (`browser.py:199–209`):
- Drop `"select_option": select_option` entry.
- `select_option` function body is removed; its select-handling logic moves into `click`.

`build_browser_tool_list` (`browser.py:314–327`):
- Drop the `Tool("select_option", …)` entry.
- Update `click`'s `Tool(…)` schema: add `"value": {"type": "string"}` to `properties`. **`value` is optional, not in `required`** — the LLM only passes it when the target is a `<select>`.
- Update `click`'s description to: `"Click an element by ID, or — for <select> elements — pass `value` to select an option."`

System prompt: greppable confirmation done — no occurrences of `select_option` in `task2/src/agent/loop.py`, `task2/src/agent/context.py`, or `prompts/task2.md`. The tool description and the live-section `options` field are the LLM's only sources of truth here, and both are updated by this change.

## What changes

- `task2/src/agent/tools/browser.py` — merge logic; drop `select_option` from registry and tool list; remove the existing `tag == "SELECT"` redirect branch in `click` (it becomes the actual select dispatcher).
- `task2/src/agent/browser_session.py:212` — comment text mentions `select_option`; rewrite to `click(value=…)` semantics.
- `task2/tests/unit/test_click_fast_fail.py` — rename `test_select_option_*` cases to exercise `click(id, value="…")`. The `count() == 0` fast-fail test still holds via `click(id=3, value="x")`.
- `task2/tests/integration/test_browser_tools_interact.py` — rewrite `select_option` calls to `click(id, value=…)`. The "switches to select_option immediately" test (line 91) becomes "click on a `<select>` with value works directly." The `select_option_failure_fast` test (line 202) becomes a `click(id_of_select, value="NotAnOption")` test.
- `task2/tests/unit/test_distill.py:59` — fixture action `select_option` → `click`.
- `task2/tests/unit/test_server_visit_trail.py:208` — fake-page `select_option` mock kept (it's the Playwright surface, not the agent-tool surface) — verify no caller-side changes are needed there.

## Token / latency profile

- **Tool spec.** One `Tool(…)` entry removed → ~50 tokens saved on every API call's `tools` array. Net positive forever.
- **Live section.** Unchanged.
- **Per-call latency.** `click` already does the `loc.evaluate("el => el.tagName")` pre-check today (line 147), so no new round-trip on the click path. The merged `click` adds zero overhead on existing click traffic; on existing select traffic, it saves the `count()` round-trip that the old `select_option` used to do separately.
- **Failure paths.**
  - SELECT-without-value: returns in <50 ms instead of Playwright's ~5 s on a missing-value select.
  - non-SELECT-with-value (case 104 shape): returns in <50 ms instead of Playwright's ~3 s "Element is not a <select>".

## Tests (TDD red-first)

Write each test, see it fail (because today's tools have separate `click` and `select_option`), then make green by implementing the merged `click`.

1. **`click(id, value="Title")` on a `<select>` eid → returns `"selected 'Title' on id=…"`.** Replaces today's `select_option(id, "Title")` happy-path coverage.
2. **`click(id)` (no value) on a `<select>` eid → returns ERROR mentioning `pass value=` and `options` entries.** New: today this returns the existing `is a <select>; clicking does not open a DOM-visible dropdown` message; we update the wording.
3. **`click(id, value="Title")` on an `<a>` eid → returns ERROR mentioning `value` is only for `<select>` + `Call list_interactive`.** New: this is the case-104 prevention test. Today no equivalent guard exists.
4. **`click(id)` on an `<a>` eid → still returns `"clicked id=…"`.** Regression check.
5. **`click(id)` when eid not in DOM → still returns the existing `count() == 0` ERROR.** Regression check; pulled forward from `test_click_fast_fail.py`.
6. **Tool registry: `select_option` is not in `build_browser_tools(...)` keys; not in `build_browser_tool_list(...)` names.** Catches accidental re-add.
7. **`click(id_of_select, value="NotAnOption")` returns Playwright's mismatch error wrapped in `ERROR:` within ~5 s** (preserves the 5 s `select_option` timeout floor — option-text mismatches are still possible and we want them to surface, not hang).

## Out of scope (deliberate)

- `type_` stays separate.
- No live-section format change (the speculative "type-decoration" Option A from observations.md is dropped; this design supersedes it by eliminating the disambiguation entirely).
- No `<a>`-href direct-navigation shortcut — separate concern with SPA-routing risk; revisit only if the next bench surfaces an `<a>`-click failure.
- Case 107 (HuggingFace, read-pagination) and case 111 (Cambridge Dictionary, cloudflare) are unrelated and untouched.

## Success criteria

- TDD: all 7 tests above are written red and become green.
- Bench: re-run `webvoyager` on `task2-unify-click-select`, expect ≥ 9/12 (no regression). Case 104 may or may not flip — the design is *prevention-shaped*: the LLM with one less tool to disambiguate should not emit the case-104 mispick at all. If it still fails on case 104 with a different shape, that's a separate finding to log.
- No `select_option` strings in any new bench trace's `tool_calls`.
- `uv run ruff check .` clean. `uv run pytest` green.

## Risks

- **Existing-prompt drift in observation history.** When the LLM reads `## Recent observations (last 3)`, it may see prior-turn obs with text like `selected 'Title' on id=14` (the new merged-click select branch returns this same string — preserved deliberately) — that's fine. But if any prior-tape obs was emitted by the OLD `select_option` tool surface in mid-conversation, there's no migration concern (every conversation is bench-scoped, no cross-conversation tape).
- **Test coverage gap on edge tags.** `<button>`, `<input type="button">`, `<input type="submit">` all return non-SELECT tagName values; clicking them goes through `loc.click()`. No change in behavior, but worth one regression test to be safe (covered by test 4 if we parameterize across tag types).
