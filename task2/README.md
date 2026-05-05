# Task 2 — Generalized Browser Automation Agent

## Evaluation

The agent is evaluated along three axes:

1. **Task success rate**
   - Whether the agent completes the task correctly end-to-end
   - Measured using a small but diverse benchmark set (multi-site, multi-step tasks)

2. **Failure modes**
   - Categorized into:
     - hallucination (answer before observing)
     - incorrect tool usage
     - stuck / looping behavior
     - selector / UI mismatch
   - Benchmarks are designed to intentionally trigger these cases. Used webvoyager for simplicity and clear Q&A form.

3. **Efficiency**
   - Token usage per task
   - Latency (number of steps / tool calls)

### Method

- All benchmark runs are logged with traces
- Failures are manually triaged and recorded in `observations.md`
- Fixes are validated by re-running failed cases

This forms a lightweight but iterative eval loop:
benchmark → failure triage → fix → re-run

## Failure Analysis

Common failure patterns observed:

- **Hallucination**
  - Agent answers before observing the page
  - Mitigated by gating answer/tool usage based on observed content

- **Tool misuse**
  - Incorrect sequence of actions (e.g., clicking before locating)
  - Reduced via stricter tool abstraction and loop design

- **Looping**
  - Agent repeatedly calls similar tools without progress
  - Partial mitigation via loop detection and retry strategies

- **Overuse of read()**
  - Triggered when the model is uncertain
  - Addressed via caching + offset-based context reuse

These failures are tracked and iterated through benchmark triage.

## Key Design Tradeoffs

### 1. DOM-based interaction vs Vision-based interaction

- DOM-based (current approach)
  - Pros: lower token cost, structured interaction
  - Cons: brittle to UI changes

- Vision-based (explored)
  - Pros: more robust, simpler action space
  - Cons: extremely high token cost

→ Chose DOM-based for cost efficiency

---

### 2. Full-page context vs Chunked (Agentic RAG)

- Chunked approach reduces token usage
- But loses positional information (e.g., "third headline")

→ Abandoned chunking for tasks requiring ordering

---

### 3. Handling anti-bot / login walls

- Not handled in current version
- Would require:
  - clean IP
  - real browser (xvfb)
  - human-like interaction

→ Explicitly out of scope due to complexity and cost

---

### 4. Architecture simplification

- Removed planner/loop separation from first attempt
- Merged into a single loop for:
  - lower complexity
  - better iteration speed

→ Tradeoff: less modular, but more practical

---

### 5. Other design decisions

**Loop / control**
- Mandatory `reason` field on every tool call; unbounded narrative history rendered in the system prompt — guards (goto allowlist, anti-loop) ground decisions in stated reasons.
- Separate `reason()` scratchpad tool; agent-callable `note()` was removed.
- Unified force-done via LLM at three triggers (max_steps, no_progress, asked-state giveup).
- Tried-and-killed: plateau interrupt machinery (reverted as net-negative).

**Tools / observation**
- `read_grep`: paginated with offset markers, per-line hash dedup, hidden after 3 duplicate outputs.
- Auto-advance for `read` and `list_interactive` on cache hits; tools hidden once exhausted.
- `GlobalTextCache` + `OffsetCache` + small page-diff injection on DOM mutation.
- AX-tree snapshot enriched with placeholder, `expanded`/`disabled`/`checked`/`selected`, `href`, listbox-option admission.
- `click` and `select_option` unified into `click(id, value=None)`; fast-fail on stale `eid`.

**Hallucination defenses**
- `done(success)` requires verbatim evidence validated against the live tape; ungrounded success is downgraded.
- `goto` constrained to URLs grounded in observed content (not args/reasons); SSRF block on unsafe URLs.
- Distilled `url_notes` keyed by site root and framed as untrusted page-derived data for the next session.

**Infra / observability**
- Per-call LLM sidecar logs (`*.llm.jsonl`) consumed by `cost_report.py` with role attribution.
- Persistent `metrics_history.jsonl` with Δ-vs-prev; `bench-sweep` and `bench-failure-triage` skills institutionalize the eval loop.
- Parallel agent sessions with queue-aware concurrency matched to the server semaphore.
- SPA UI + JSON endpoints (replaced `/replay` HTML); bearer-token auth + SPA passcode gate.
- Deployment: SSH-based Docker CD via `pinner.top` (joins `deploy_default` network), not Zeabur.

**LLM choice**
- DeepSeek `deepseek-chat` default, reasoning OFF; kept as a one-env-var swap from the original Qwen target.

## How to run
### Server
```bash
# exports the env vars from .env (notably DEEPSEEK_API_KEY) so the uvicorn process inherits them 
cd task2/
set -a && . ./.env && set +a && AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001
```

Then connect to http://127.0.0.1:8001/

### Cost analysis
```bash
uv run python scripts/cost_report.py --all
```

## Where AI helped me
1. Implement the TDD/e2e tests
2. Implement all the codes. 0 codes were written by me.
3. Created a skill to 
  - restart the server (update the code module after fixes)
  - run latest failed benchmark results
  - identify any hallucination first. However this parts often failed without human in the loop.
  - identify loops in agent.
  - Write down the observations. The fix are often wrongly identified in last try and thus we need to plan further and add more human insight for the design part.
4. Brainstorming on different topics, but felt that it spotted the wrong error in most of time.
5. Also asked AI to search on internet on certain design decisions, for the hallucination part. It suggests gating "goto" or "answer" with what we didn't see and it works fine in benchmarks.


## Introduction
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
- `data/traces/<session_id>.llm.jsonl` — one JSON line per LLM call (request, response, usage, latency). Developer-only; not read by the UI. Use `scripts/cost_report.py` to summarize cost and per-role token attribution: `uv run python scripts/cost_report.py --session <sid>` or `--all`.

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

> **Deploy URL:** _pinner.top/
