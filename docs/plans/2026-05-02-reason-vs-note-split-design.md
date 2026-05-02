# Reason vs. Note: Split In-Session Memory From Page Knowledge

## Problem

The current `note(text)` tool is a per-URL persistent scratchpad keyed in
SQLite (`agent/notes_store.py`). It is the only persistence the agent
controls between turns and between sessions, and it has been collapsing
two distinct purposes into one row:

1. **Page knowledge** — durable, goal-agnostic facts about the page
   (selector ids, dropdown contents, dead-ends, walls). Useful to *any*
   future run on this URL.
2. **In-session task scratchpad** — the agent's working memory for the
   current goal that needs to outlive the K=8 rolling action window.

In trace `9206cf2d3e744d95b8c169311fcbd613` (case 113, CanIRun.ai), the
agent wrote two `note(...)` payloads:

- step 12: "the page has a GPU dropdown (combobox id=94)…" — half page
  fact, half task state ("RTX 3090").
- step 25: 600-character analysis of "models that fit within 24 GB" —
  pure task state.

Both got bound to the canirun.ai row. A future run with a different GPU
will load the prior RTX-3090 analysis on every turn and pay tokens for
it forever — and worse, get pulled toward the prior conclusion.

## Goal

Split the two purposes into two surfaces with different lifetimes,
authors, and storage tiers.

## Design

### Two surfaces

| | `reason(text)` | `note` (no longer agent-callable) |
|---|---|---|
| Caller | Agent, any turn | System, after `done(...)` only |
| Storage | In-memory list on `ReactLoop` | Existing `NotesStore` SQLite |
| Lifetime | Session | Persists across sessions, per URL |
| Injection | New `Reasoning so far:` block in user message | Existing `URL notes:` block (unchanged) |
| Size cap | ~4 KB FIFO | 2 KB FIFO (unchanged) |
| Tool description | "Record a thought you want to remember past the rolling action window. In-session only." | n/a — removed from registry |

### `reason` tool

- Implementation lives next to existing meta tools (`agent/tools/meta.py`
  or a new `reason.py`).
- Schema: `{ "text": string, "thought": string }`, `required: ["text"]`.
- Returns the literal obs `"noted"` (same as today's `note`, since the
  obs already gets flushed when context renders the scratchpad block).
- Loop owns a `self.reason_log: list[str]` cleared each session.
- Renderer: `Reasoning so far:` block injected immediately after
  `URL notes:` in `build_messages`. FIFO trim at ~4 KB before render.

### `note` removal from registry

- Delete `note` from `build_meta_tools` and `build_meta_tool_list`.
- `NotesStore` itself stays unchanged — only its writer changes.
- The `URL notes:` block in the prompt continues to read from
  `notes.get(url)` exactly as today.

### Distillation pipeline

New module `agent/distill.py`:

```python
async def distill_page_knowledge(
    *,
    llm: LLMClient,
    notes: NotesStore | None,
    url: str,
    goal: str,
    status: str,
    answer: str,
    tape: list[dict],
    reason_log: list[str],
    trace: Trace | None,
) -> None: ...
```

- **Trigger**: in `loop.py`, after `LoopDone` is caught and `result` is
  built, immediately before returning. Fires on every `done(...)` —
  both success and failed/needs_user.
- **No-op when**: `notes is None`.
- **Inputs to LLM**: `goal`, `url`, `status`, `answer`, full tape,
  `reason_log`, prior URL note (`notes.get(url)`).
- **LLM call**: free-form text out (`tool_choice="none"`),
  `reasoning=False`, low temperature.
- **Prompt contract**: extracts ≤10 bullets, page-state only — selectors,
  hidden requirements, dead-ends, walls. Rejects anything goal-specific
  (the value the user asked for, intermediate analyses). Includes the
  prior URL note in the input and asks the LLM to produce a unified
  deduplicated list — code does not dedupe.
- **Output**: replace the URL row with the LLM's text via
  `notes.append` after a `notes.delete` (or a new `notes.replace`); the
  existing 2 KB FIFO trim catches oversize output as a safety net.
- **Failure isolation**: any exception (HTTP error, `ToolNameNotAllowed`,
  JSON shape problems) is caught. Emit a trace event
  `{"type": "distill_failed", "payload": {"error": str(e)}}` and leave
  the prior note untouched. The user-visible `result` is unaffected.

### Tests

New `tests/unit/test_distill.py`:

- `distill_page_knowledge` called after `done(success)` writes new row.
- `distill_page_knowledge` called after `done(failed)` also writes —
  failure-mode page knowledge (walls, dead-ends) is the most valuable
  output.
- Prior URL note is fed into the prompt; output replaces the row.
- 2 KB cap honored when distiller returns oversize text.
- Distiller LLM raising propagates **nothing**: `result` is byte-identical
  to the no-distill baseline; trace gets a `distill_failed` event.
- `notes is None` short-circuits the call entirely.

New `tests/unit/test_reason.py` (or extend `test_meta_tools.py`):

- `reason(text="…")` appends to in-session log; obs is `"noted"`.
- Render path: scratchpad appears in user message under
  `Reasoning so far:`.
- FIFO trim at the cap.
- Cleared per session: a fresh `ReactLoop` starts with empty scratchpad.

Existing tests:

- Anywhere a test calls the agent-callable `note` tool, rename to
  `reason` (semantically equivalent for in-session use).
- Tests asserting per-URL persistence-via-tool become tests asserting
  per-URL persistence-via-distill.

### Migration

- One-line wipe of `task2/data/url_notes.db` as part of the migration
  commit. The existing rows are polluted with task-state from the
  agent-as-author era; carrying them forward via the distiller's
  prior-note input would propagate the pollution. A clean slate is
  cheaper than trying to launder the rows.
- No schema change to `NotesStore`.
- `prompts/task2.md`: rename `note` → `reason` with the new in-session
  framing; add one line that page-knowledge is captured automatically at
  `done()` — agent should not try to do it via `reason`.

## Open Questions Resolved

- **Trigger**: every `done(...)`, both success and failure. Failure
  traces produce the most useful negative knowledge (walls,
  dead-ends).
- **Author**: system, via separate LLM call. The agent never sees `note`
  as a tool, so it cannot pollute the page-knowledge surface with
  task-state.
- **Merge**: LLM does the merge (prior note + new distillation → unified
  list). Code does no dedup.
- **Migration**: wipe `url_notes.db`.

## Out of Scope

- Cross-URL knowledge (e.g., "this organization runs Cloudflare on every
  subdomain") — distillation is per-URL only.
- Sharing knowledge between sessions running in parallel — single SQLite
  writer is fine for the bench harness.
- Human-curated notes (an external operator editing rows). The current
  schema permits it; we just don't add tooling for it.
