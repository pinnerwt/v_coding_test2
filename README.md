# Coding test - web agent

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

# vici — AI Coding Test

[![Python](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-0.9.3-261230?logo=python&logoColor=white)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/badge/ruff-0.14.14-D7FF64?logo=ruff&logoColor=000)](https://github.com/astral-sh/ruff)
[![task2 CI](https://github.com/pinnerwt/v_coding_test2/actions/workflows/task2-ci.yml/badge.svg?branch=main)](https://github.com/pinnerwt/v_coding_test2/actions/workflows/task2-ci.yml)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-025E8C?logo=dependabot&logoColor=white)](.github/dependabot.yml)
[![Coverage (task2)](https://img.shields.io/badge/coverage-92%25%20line-brightgreen)](artifacts/coverage.xml)

This repo is a second-pass attempt at the three tasks defined in [`AI-Coding-Test-EN.md`](AI-Coding-Test-EN.md) (Chinese: [`AI-Coding-Test-ZH.md`](AI-Coding-Test-ZH.md)). It deliberately drops the openspec/CI scaffolding from the [first trial](https://github.com/pinnerwt/v_coding_test) and focuses on success rate / latency / token usage of the task 2 agent. The whole repo is **test-driven**; see [`CLAUDE.md`](CLAUDE.md) for operating rules.

## Tasks

| #  | Title                                          | Status         | Where                  |
|----|------------------------------------------------|----------------|------------------------|
| 1  | GitHub CI/CD as Claude Skills                  | not started    | —                      |
| 2  | Generalized Browser Automation Agent           | in progress    | [`task2/`](task2/)     |
| 3  | SEC 10-K Item-level Structured Extraction      | not started    | —                      |

Zeabur URL lands in `task2/README.md` once deployed.

## Repository layout

```
.
├── AI-Coding-Test-EN.md      # the brief
├── CLAUDE.md                  # repo-wide operating rules (TDD, uv, ruff)
├── README.md
├── observations.md            # running notes from /bench-failure-triage
├── docs/                      # design docs and plans
├── prompts/                   # key prompts used to drive development
└── task2/                     # browser automation agent (uv project)
```

## Quick start (task 2)

```bash
cd task2
uv sync
uv run playwright install chromium    # one-time post-install browser fetch
uv run pytest                          # tests
uv run ruff check . && uv run ruff format --check .
set -a && . ./.env && set +a && \
  AGENT_RESTRICT_GOTO=true \
  uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001
```

See [`task2/README.md`](task2/README.md) for env vars, Docker, Zeabur, and architecture notes.

## Development workflow

- **TDD is non-negotiable.** Red → green → refactor. Bug fixes start with a regression test. Eval sets count as tests for tasks 2 and 3. See [`CLAUDE.md`](CLAUDE.md).
- **No openspec / no planner-loop split** — lessons from the first trial (see "Lesson from first trial" above). Plans live as plain markdown under `docs/plans/` and `prompts/`.
- **Conventional commits** scoped by task: `feat(task2): ...`, `fix(task2): ...`, `test(task2): ...`, `docs(task2): ...`.
- **Python:** `uv` for env / deps (`uv sync`, `uv add`, `uv run`), `ruff` for lint and format. Don't use `pip`, `poetry`, `venv`, `black`, or `flake8`.
- **No `--no-verify`**, no mocking the LLM in LLM-contract tests, no weakening tests to pass.

## CI/CD

[`task2-ci.yml`](.github/workflows/task2-ci.yml) runs on pushes to `main` and PRs targeting `main` that touch `task2/**` or the workflow itself. It installs `uv`, syncs deps with `--frozen`, installs Chromium for Playwright, then runs `ruff check`, `ruff format --check`, and `pytest --cov=agent --cov-report=xml:../artifacts/coverage.xml`. The XML report is committed at [`artifacts/coverage.xml`](artifacts/coverage.xml) (also uploaded as the `task2-coverage` workflow artifact). Concurrency is keyed by ref so superseded runs cancel themselves.

## Dependabot

Configured in [`.github/dependabot.yml`](.github/dependabot.yml):

| Ecosystem        | Directory   | Cadence | Commit prefix    | Labels                     |
|------------------|-------------|---------|------------------|----------------------------|
| `uv`             | `/task2`    | weekly  | `chore(task2)`   | `dependencies`, `task2`    |
| `github-actions` | `/`         | weekly  | `chore(ci)`      | `dependencies`, `ci`       |

Open PRs are capped at 5 per ecosystem and run through the same CI gate as human PRs.

## Test coverage

Latest run: **92% line coverage** across `task2/src/agent` (242 tests). Snapshot lives at [`artifacts/coverage.xml`](artifacts/coverage.xml). Regenerate with:

```bash
cd task2
uv run pytest --cov=agent --cov-report=term --cov-report=xml:../artifacts/coverage.xml
```

CI overwrites the same file on every PR and also uploads it as the `task2-coverage` workflow artifact.

## Prompts

The `prompts/` directory is the AI-collaboration record reviewers read:

- [`prompts/task2.md`](prompts/task2.md) — the task 2 seed prompt and the resulting plan.

## Deployment

Each task is deployed as a public service on [Zeabur](https://zeabur.com/) per the brief.

- task 2: see [`task2/README.md`](task2/README.md#zeabur). Build config in `task2/Dockerfile`. Persistent volume must be mounted at `/app/data` (URL notes + traces). Deploy URL: _to be filled in._

## Contributing / collaborating

This is a personal coding-test submission, not an open-source project — issues and PRs from outside collaborators aren't expected.

## License

No license file is included; the work is submitted for evaluation per the test brief. Treat all rights as reserved unless otherwise stated.
