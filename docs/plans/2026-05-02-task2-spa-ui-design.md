# Task 2 — SPA UI Redesign (Design)

**Date:** 2026-05-02
**Branch (target):** `task2-web-agent` (continues prior work)
**Predecessor design:** `2026-05-02-task2-web-agent-design.md`

## Problem

The current UI ships the agent loop end-to-end but offers no signal that work is happening between Run-click and the first step (which can be many seconds while Chromium spins up). It also splits "run" and "replay" across two server-rendered pages, and exposes nothing about LLM availability or token cost.

## Goals

1. **Visible liveness.** The user always knows whether the agent is connecting, thinking, executing a tool, or done.
2. **Single ChatGPT-style page.** Collapsible left sidebar of past sessions; main panel shows the active or selected session as a chat-like transcript.
3. **Bottom monitor strip.** Server health, LLM-server health, current-session token usage, step counter.
4. **Trace cards collapse.** Each step is one line by default; click to expand thought + full args + full obs.

## Non-goals

- Multi-turn within one session (one goal per session, per agreed Q1).
- Live page screenshot / DOM preview.
- Auth, multi-user, multi-tab coordination.
- Renaming or deleting sessions from the UI.
- LLM-generated session titles.

## Architecture

True SPA. One Jinja-rendered shell (`index.html`); all dynamic content built by `app.js` against JSON endpoints + WebSocket. `history.pushState` keeps deep links working (`/?s=<sid>`).

```
┌─────────────┬───────────────────────────────────┐
│ Sidebar     │  Main panel                       │
│ + New chat  │  Transcript: collapsed step cards │
│ Sessions    │  Goal input + Run                 │
├─────────────┴───────────────────────────────────┤
│ ● server  ● llm  · tokens 1234/567  · 4/50 step │
└─────────────────────────────────────────────────┘
```

### Session model

- One session = one goal. Created on first WS `{type:"goal"}`. Server already mints `session_id` and writes `data/traces/<sid>.jsonl` — we surface that ID over the WS as the first event so the client can pin the URL and the sidebar entry.
- Sidebar label: goal text truncated to 60 chars + relative timestamp ("2m ago"). Status pill: running / success / failed.
- Running sessions are tracked in `app.state.sessions: dict[sid, SessionMeta]` (in-memory, lost on server restart). Finished sessions are read from disk by listing `data/traces/*.jsonl` and parsing the first event for the goal.

### Liveness — three layers

1. **Confirmation (instant):** Run button → disabled + spinner; sidebar gets a "(running)" entry at the top within ~50ms; status bar flips to `● running`.
2. **Liveness (between steps):** while an LLM call is in flight, render an ephemeral "thinking…" card at the bottom. Replaced by the actual step card when the next `step` event arrives.
3. **Semantic (what's it doing):** each step is a one-line collapsed card: `▸ click(id=3) → ok`. Chevron expands thought + full args + full obs.

### New WebSocket events

- `{type:"session_started", payload:{sid, goal, started_at}}` — sent right after the WS receives `{type:"goal"}`. Lets the client pin the URL and add the sidebar entry immediately.
- `{type:"llm_call_start"}` — sent inside the loop right before `await llm.chat(...)`. Client renders the "thinking…" card.
- `{type:"usage", payload:{prompt, completion}}` — sent after each LLM call when `usage` is present in the response. Client increments the running per-session totals.

Existing `step` / `question` / `done` keep their shape.

### New HTTP endpoints

- `GET /api/sessions` → JSON list of `{sid, goal, started_at, status}`. Merges in-memory running sessions with on-disk finished ones.
- `GET /api/trace/{sid}` → JSON list of trace events (the same shape the client receives over WS). Used when the user clicks a past session in the sidebar.
- `GET /api/llm_health` → server-side hits the configured `AGENT_MODEL_BASE_URL/models` with a 2s timeout. Returns `{"llm": "up"|"down", "latency_ms": N}`.

`GET /api/health` is implicit — if any HTTP call returns 200 the server is up. The client can hit `/api/sessions` as its health probe.

### Removed

- `GET /replay` and `GET /replay/{sid}` Jinja pages (replaced by `/api/sessions` + `/api/trace/{sid}` + the SPA).
- `templates/replay.html` (the SPA renders past sessions inline).

### LLM client change

`LLMClient.chat()` currently discards `usage`. Change return shape to expose it (or attach it to the message dict). The loop forwards it as a `usage` WS event.

### Polling

The bottom monitor strip polls `/api/llm_health` every 5s. Token usage and step counter are pushed (no polling needed).

## Data flow

```
[Run click] → ws.send goal
           ← session_started   → URL = /?s=<sid>, sidebar add, status: running
           ← llm_call_start    → thinking… card
           ← usage             → tokens += {p,c}
           ← step              → replace thinking… with step card; steps += 1
           ← (loop)
           ← done              → status badge (success/failed), enable Run
```

```
[Sidebar click on past sid] → fetch /api/trace/{sid} → render cards (no WS)
[Bottom monitor tick (5s)]  → fetch /api/llm_health  → update LED
```

## Error handling

- LLM unreachable: `llm.chat()` raises → loop catches as ERROR observation (existing behavior); WS sends a normal `step` with `obs: "ERROR: ..."` and the agent continues. The bottom LED flips red on the next health poll.
- WS drops mid-run: server keeps running (loop is in a Task); on reconnect the client refetches `/api/trace/{sid}` to catch up. (Reconnect is YAGNI for v1 — leave it: the trace file is the source of truth, and the user can refresh.)
- Browser crash inside the loop: existing behavior (`obs = ERROR`); no change.

## Testing

- **WS contract** (`tests/integration/test_server_ws.py`, extend): assert `session_started` arrives first; assert `llm_call_start` precedes each `step`; assert `usage` arrives when the mocked LLM response carries it.
- **HTTP API** (`tests/integration/test_session_api.py`, new):
  - `/api/sessions` lists on-disk traces (seed `tmp_path/traces/*.jsonl` with known files).
  - `/api/trace/{sid}` returns the parsed events.
  - `/api/llm_health` returns `up` against a mocked 200 and `down` against a timeout (use `httpx.MockTransport`).
- **LLM client** (`tests/unit/test_llm_usage.py`, new): `LLMClient.chat` exposes `usage` from the upstream response.
- **No JS tests.** Vanilla, no toolchain.

## Out of scope (explicitly)

- pushState round-trips beyond `?s=` (no nested routes).
- Cancelling a running session from the UI (the `Semaphore(1)` already serializes — second goal queues).
- Persisting in-memory `app.state.sessions` across restarts (rebuilt from disk on first `/api/sessions` call).

## Risks

- **`LLMClient.chat()` shape change** ripples into the existing loop and tests. Mitigation: keep the existing return type but attach `usage` as an attribute or extra dict key — additive only.
- **Sidebar reading every JSONL on every list call** is fine for dozens of sessions, slow at thousands. Won't matter for this assignment; punt.
