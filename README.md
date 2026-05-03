# Coding test - web agent

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

## Key design decisions
1. React structure: including differnet tool calls (goto, read, click, type, ask user question, done, list_interactives). Conclude with `done` once the agent feels that the information is enough.
2. Benchmark testing: look at the trace and found that the agent 
 - does not have enough ability on certain tasks
 - Or hallucinate the answer before it even see it on the page
 - Return wrong reasoning/tool calls in certain cases
3. read() function: was called very often when the LLM does not know what to do. So I created a caching system with hash that auto-add the offset if the old context has been seen.
4. note() to write down notes for future LLM usage.
5. Wanted to try agentic RAG, chunked the webpage so that we can skip different read/read_grep calls. However, once we use this, we will lose the context on the order of the content. For example, we will not be able to answer "what is the third headline on BBC news". Gave up in the end.
6. Do not handle captcha/anti bot/login walls for now. If we do want to bypass this, we would need a cleaner IP (instead of zeabur common cluster) and a X server in the docker (xvfb), and use a real browser with a MCP or mouse/keyboard control. Did it with my Openclaw but it took a lots of time and lots of tokens (see 7.).
7. Vision: use the vision directly. This includes screenshots different webpage and scroll/click at different coordinates. This is for me the easiest way as a web agent: tool calls are clear and much less. However, the cons in this method is that it simply costs too many tokens. I think this is why Claude was shipped with this.
8. Summary of different step, reducing prompt token usage with full context.
9. Use Deepseek api saves my life after my decision to restart the project.

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

## Lesson from [first trial](https://github.com/pinnerwt/v_coding_test)
1. Planner/loop separation is a huge waste of time.
2. Openspec is the most token consuming during developement cycle.
3. Local qwen3.5-27b was slow and benchmarks took much longer than expected.
4. ci/CLAUDE.md/function calls reused easily.
5. Design pattern reused, while merging planner/loop into the same loop instance.

# vici — AI Coding Test

[![Python](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-0.9.3-261230?logo=python&logoColor=white)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/badge/ruff-0.14.14-D7FF64?logo=ruff&logoColor=000)](https://github.com/astral-sh/ruff)
[![task2 CI](https://github.com/pinnerwt/v_coding_test2/actions/workflows/task2-ci.yml/badge.svg?branch=main)](https://github.com/pinnerwt/v_coding_test2/actions/workflows/task2-ci.yml)
[![Dependabot](https://img.shields.io/badge/dependabot-not%20configured-lightgrey)]()
[![Coverage (task2)](https://img.shields.io/badge/coverage-92%25%20line-brightgreen)](#test-coverage)

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

[`task2-ci.yml`](.github/workflows/task2-ci.yml) runs on pushes to `main` and PRs targeting `main` that touch `task2/**` or the workflow itself. It installs `uv`, syncs deps with `--frozen`, installs Chromium for Playwright, then runs `ruff check`, `ruff format --check`, and `pytest --cov=agent --cov-report=xml`. The XML coverage report is uploaded as the `task2-coverage` artifact. Concurrency is keyed by ref so superseded runs cancel themselves.

## Dependabot

Not configured. The first trial had weekly `uv` + `github-actions` updates under `.github/dependabot.yml`; reintroduce when CI lands.

## Test coverage

Latest local run: **92% line coverage** across `task2/src/agent` (242 tests). Regenerate with:

```bash
cd task2
uv run pytest --cov=agent --cov-report=term --cov-report=xml
```

CI also produces `coverage.xml` on every PR — download it from the run's `task2-coverage` artifact.

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
