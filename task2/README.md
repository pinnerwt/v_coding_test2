# Task 2 — Generalized Browser Automation Agent

A web-deployed ReAct agent that drives a headless Chromium (via Playwright) to accomplish open-ended user goals. The user enters a goal in a web UI, watches the agent's `(thought, action, observation)` tape stream live over a WebSocket, answers clarifying questions when the agent asks, and can replay past sessions for debugging.

- **Design doc:** [`docs/plans/2026-05-02-task2-web-agent-design.md`](../docs/plans/2026-05-02-task2-web-agent-design.md)
- **Implementation plan:** [`docs/plans/2026-05-02-task2-web-agent.md`](../docs/plans/2026-05-02-task2-web-agent.md)
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
uv run uvicorn agent.server:build_app --factory --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000/`. Enter a goal; trace cards stream as the agent acts. `/replay` lists past sessions saved as JSONL.

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
- `data/traces/<session_id>.jsonl` — one event per line; the `/replay` UI reads these.

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
