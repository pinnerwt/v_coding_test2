# Task 2 — Web Browsing Agent (Design)

Date: 2026-05-02
Scope: Task 2 of `AI-Coding-Test-EN.md` — Generalized Browser Automation Agent.

## Goal

A web-deployed ReAct agent that drives a headless browser to accomplish open-ended user goals. The user enters a goal in a web UI, watches the agent's (thought, action, observation) tape stream live, answers clarifying questions when the agent asks, and can replay past sessions for debugging and eval iteration.

## Architecture

Single-process FastAPI server. One headless Playwright (Chromium) browser per server instance, single concurrent session (additional users queue). Two LLM endpoints, both configurable via base URL and model name (no hardcoded providers, per `CLAUDE.md`):

- **Agent model** (strong): drives the ReAct loop. Default → local Qwen3.5 27B at `http://localhost:8090`.
- **Summarizer model** (small): writes implicit URL notes after errors / `goto` / `done(failed)`. Default → smaller local model on a configurable endpoint.

Both LLM calls run with **reasoning disabled** (Qwen non-thinking mode, or the equivalent flag for whatever model is configured). The agent's `thought` argument inside each tool call is the only reasoning surface — visible in the trace, replayable, not duplicated by hidden CoT tokens.

## ReAct loop

Single agent (no planner/navigator split). Per step:

1. Build prompt (see Context layout).
2. Call agent model with native tool-calling. The tool-call args carry both the structured action and a `thought` string.
3. Execute the chosen tool → produce observation.
4. Append `(thought, action, observation)` to the tape.
5. Possibly trigger the implicit summarizer (on `goto`, error observation, or `done(failed)`).
6. Loop control:
   - If action is `done` → exit.
   - **Stuck-detector → replan**: if the same `(URL, action, observation)` repeats 3× consecutively, the next turn injects a system message: *"You've repeated the same action 3 times with no change. The current URL's notes are re-surfaced below. You must produce a different action this turn — try a different element, navigate elsewhere, call `note()` to record what's failing, or call `ask_user_question`."* Current-URL notes are re-pinned high in the prompt for that turn. If the *next* action is still identical, escalate to forced `ask_user_question`; if that loops too, force `done(failed)`.
   - Hard cap: 50 steps per session (configurable via `MAX_STEPS`).

## Tools (native function-calling schemas)

| Tool | Args | Notes |
|---|---|---|
| `goto` | `url: str` | Navigate; wait for network idle (10s timeout). |
| `back` | — | Browser back. |
| `list_interactive` | `offset?: int`, `limit?: int` | A11y-tree snapshot of interactive elements; returns `[{id, role, name, value?}, ...]`. Paginated. |
| `read` | `offset?: int` | Visible text, 2000-char window from `offset`. |
| `read_grep` | `pattern: str`, `window?: int` | Case-insensitive first occurrence; returns `±window` chars centered on match. |
| `click` | `id: int` | Element ID from `list_interactive`. Tries Playwright `.click()` then JS-dispatch fallback internally. |
| `type` | `id: int`, `text: str`, `submit?: bool` | Fill input; if `submit=true`, press Enter after. |
| `select_option` | `id: int`, `value: str` | For `<select>`. |
| `press_key` | `key: str` | Escape, Tab, arrow keys, etc. |
| `note` | `text: str` | Agent-written URL note (hybrid memory). |
| `ask_user_question` | `question: str` | Blocks loop; UI prompts user; resumes when answered. |
| `done` | `status: "success" \| "failed" \| "needs_user"`, `answer: str` | Exits loop. |

Element addressing is by integer ID returned from `list_interactive` — never by CSS selector or natural-language description.

## Implicit summarizer

Triggered automatically (no agent tool call) on:
- Any error observation.
- `goto` (summarizes the prior URL's session before the URL changes).
- `done(failed)`.

The summarizer model receives the recent tape slice scoped to the current URL plus existing notes for that URL, and returns 1–3 short bullet lines to append. Runs with reasoning disabled.

## Context layout (every turn)

In order:

1. **System** — persona, tool schemas (auto-derived from function defs), one or two few-shot examples.
2. **User goal** — original task, pinned.
3. **User Q&A** — accumulated `ask_user_question` exchanges, pinned directly under the goal (kept together because they jointly define intent).
4. **URL notes for current URL** — bullets retrieved from store; empty block on first visit.
5. **Recent tape** — last K=8 `(thought, action, observation)` triples in full. Older steps collapsed to `step N: action(args) → 1-line outcome`.
6. **Current page header** — URL, title, # interactive elements. The full snapshot only appears when the agent calls `list_interactive` / `read`.

When stuck-detector fires the replan, the system message and re-pinned URL notes are appended at the end of the system block for that turn only.

## URL notes storage

SQLite file at `data/url_notes.db`, single table:

```sql
CREATE TABLE url_notes (
  url TEXT PRIMARY KEY,
  notes TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

- Key: full URL with the query string stripped (configurable). Stripping query strings keeps CSRF tokens and session IDs from polluting keys.
- `notes` is a newline-joined log, capped at ~2KB per URL — oldest lines dropped when exceeded.
- Persists across sessions and across deploys **iff** a Zeabur volume is mounted at `data/`. Without a volume, notes reset on redeploy. README will call this out.
- No TTL in v1. Defer staleness handling until evals show it matters.

## UI (FastAPI + WebSocket + HTMX/vanilla)

Two pages:

- **`/`** — Goal entry textarea + "Run" button. On submit, opens a WebSocket. Live trace pane streams `(step, thought, action, observation)` as cards. When the agent calls `ask_user_question`, the input area swaps to a Q&A box; submitting resumes the loop. Final `done` shows the answer + status.
- **`/replay`** — Sidebar list of saved sessions (newest first). Selecting one renders the same trace card layout, statically, from the saved JSONL.

WebSocket protocol: server pushes `{type, payload}` events (`step`, `question`, `done`, `error`). Client posts answers back over the same socket.

## Trace storage

JSONL per session at `data/traces/<session_id>.jsonl`. One line per event (`step`, `question`, `answer`, `done`). The replay page reads these directly. Same files are the eval substrate: `tests/evals/` defines `(goal, fixture site, expected outcome)` cases that run the loop end-to-end and compare against the recorded `done`.

## Deployment

Single Dockerfile, single Zeabur service. Container installs Playwright + Chromium. Environment variables:

- `AGENT_MODEL_BASE_URL`, `AGENT_MODEL_NAME`
- `SUMMARIZER_MODEL_BASE_URL`, `SUMMARIZER_MODEL_NAME`
- `MAX_STEPS` (default 50)
- `URL_NOTE_QUERY_STRIP` (bool, default true)

Persistent volume mounted at `/app/data` for SQLite + traces.

## TDD scope (per CLAUDE.md)

- Unit tests per tool with a mocked Playwright `Page`.
- Loop tests: stuck-detector → replan path, escalation chain, max-step cap, `ask_user_question` blocking semantics.
- Context-builder tests: ordering, URL-note injection on `goto`, tape compression after K=8.
- LLM client tests: reasoning-disabled flag is set on every call by default.
- Eval set under `tests/evals/`: small set of `(goal, site fixture, expected done)` cases. Mock the network layer to the LLM (HTTP-level), not the LLM contract. Page fixtures served by a tiny local HTTP server during tests so Playwright drives real DOM.

## Out of scope (v1)

Screenshots, hover, file upload/download, iframes, multi-tab, multi-tenant browser sessions, login persistence beyond what cookies give for free, URL-note TTL, planner/navigator agent split.
