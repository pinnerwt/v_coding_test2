# Task 2 — Generalized Browser Automation Agent

A web-deployed ReAct agent that drives a headless Chromium (via Playwright) to accomplish open-ended user goals. The user enters a goal in a single-page web UI, watches the agent's `(thought, action, observation)` tape stream live over a WebSocket, answers clarifying questions when the agent asks, and can revisit past sessions from a sidebar.

- **Design docs:** [`2026-05-02-task2-web-agent-design.md`](../docs/plans/2026-05-02-task2-web-agent-design.md), [`2026-05-02-task2-spa-ui-design.md`](../docs/plans/2026-05-02-task2-spa-ui-design.md)
- **Implementation plans:** [`2026-05-02-task2-web-agent.md`](../docs/plans/2026-05-02-task2-web-agent.md), [`2026-05-02-task2-spa-ui.md`](../docs/plans/2026-05-02-task2-spa-ui.md)
- **Brainstorming prompt:** [`prompts/task2.md`](../prompts/task2.md)

## Architecture

Single FastAPI process. One Playwright browser per server, one concurrent session (others queue on a semaphore). Two LLM endpoints, both reasoning-disabled by default:

- **Agent** (strong): drives the ReAct loop via native OpenAI tool calling. The agent's `thought` argument inside each tool call is the only reasoning surface — visible in the trace, replayable, no hidden CoT.
- **Summarizer** (small): writes implicit URL notes to a SQLite store on errors / `goto` / `done(failed)`.

Element addressing is via integer IDs from `list_interactive` (a11y-tree snapshot). The agent never invents CSS selectors or natural-language locators.

A stuck-detector forces a *replan* turn (re-pinned URL notes + system hint) when the same `(URL, action, observation)` repeats 3×, then escalates to `ask_user_question`, then `done(failed)`.

## Local development

```bash
cd task2
uv sync
uv run playwright install chromium    # one-time
uv run pytest -v                       # unit + integration + eval
uv run ruff check .                    # lint
uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000/`. The single-page UI has three regions:

- **Left sidebar** — collapsible list of past sessions (one entry per goal). Click to open; `+ New chat` to start fresh.
- **Main panel** — chat-like transcript of the active session. Each step is a one-line collapsed card; click to expand thought + full args + full observation. A dashed "thinking…" card appears between steps while the LLM is in flight.
- **Bottom monitor strip** — server / LLM-server health LEDs (5s poll), running token totals (prompt / completion) and step count for the current session.

Deep links work: `?s=<session_id>` opens that session directly.

### JSON endpoints (used by the SPA, callable directly)

| Endpoint | Returns |
|---|---|
| `GET /api/sessions` | List of `{sid, goal, started_at, status}` |
| `GET /api/trace/{sid}` | Full ordered list of trace events for a session |
| `GET /api/llm_health` | `{llm: "up"\|"down", latency_ms}` (probes `AGENT_MODEL_BASE_URL/models` with a 2s timeout) |
| `POST /api/run_sync` | Body `{goal}` → blocks until the loop finishes; for scripted runs / tests |
| `WS /ws` | Send `{type:"goal", goal}`; receive `session_started` / `llm_call_start` / `step` / `usage` / `question` / `done` events |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODEL_BASE_URL` | `http://localhost:8090/v1` | OpenAI-compatible base URL for the agent LLM |
| `AGENT_MODEL_NAME` | `qwen3.5-27b` | Model name passed to chat completions |
| `SUMMARIZER_MODEL_BASE_URL` | same as agent | Base URL for the small summarizer LLM |
| `SUMMARIZER_MODEL_NAME` | same as agent | Summarizer model name |
| `MAX_STEPS` | `50` | Hard cap on ReAct loop iterations |
| `URL_NOTE_QUERY_STRIP` | `true` | Strip query strings before keying URL notes |

## Storage

- `data/url_notes.db` — SQLite key/value of URL → notes. Persists across sessions.
- `data/traces/<session_id>.jsonl` — one event per line. Source of truth for the sidebar and `/api/trace/{sid}`. The first event is always `session_started` (carrying the goal), so the sidebar can label entries without a separate index.

Mount a persistent volume at `/app/data` for cross-deploy survival.

## Docker

```bash
cd task2
docker build -t task2-agent .
docker run --rm -p 8000:8000 \
  -e AGENT_MODEL_BASE_URL=http://host.docker.internal:8090/v1 \
  -v $(pwd)/data:/app/data \
  task2-agent
```

The base image (`mcr.microsoft.com/playwright/python:v1.59.0-jammy`) ships Chromium pre-installed.

## Zeabur

1. Connect this repo and point the service at `task2/`.
2. Set the env vars above (at minimum `AGENT_MODEL_BASE_URL` and `AGENT_MODEL_NAME`).
3. Attach a persistent volume mounted at `/app/data` (URL notes and traces will be lost on redeploy without it).
4. Deploy. The service exposes port 8000.

> **Deploy URL:** _(to be filled in after first successful deploy)_

## AI assistance

This task was scoped, designed, and largely implemented through pair-programming with Claude. The brainstorming transcript shaped the design doc; the design doc seeded the 19-task TDD implementation plan; each task was implemented red-then-green with a failing test before any production code. Notable contributions:

- Catching a Playwright strict-mode violation in `BrowserSession.snapshot` when two interactive elements shared the same `(role, name)` — fixed with `Counter`-based `.nth()` indexing.
- Rejecting the original "tighten the timeout to escape stuck loops" idea in favor of a state machine that *replans* before escalating, so the agent gets a real chance to recover.
- Defaulting `LLMClient` to reasoning-disabled (`enable_thinking=False`) so the agent's `thought` field is the single source of truth in the trace.
