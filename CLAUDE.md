# Repository Guide

## Purpose

This repository exists to solve the tasks in [`AI-Coding-Test-EN.md`](./AI-Coding-Test-EN.md) (ZH copy: [`AI-Coding-Test-ZH.md`](./AI-Coding-Test-ZH.md)). Every change should ladder up to one of:

- **Task 1** — GitHub CI/CD as Claude Skills
- **Task 2** — Generalized Browser Automation Agent
- **Task 3** — SEC 10-K Item-level Structured Extraction

If a change does not serve one of these tasks (or a shared common requirement: Zeabur deploy, `prompts/`, README, repo hygiene), do not make it.

## Test-Driven Development (non-negotiable)

The whole repo is TDD. The test is the spec; the code is the response to the test.

1. **Red first.** Before writing or modifying production code, write a failing test that captures the behavior. Run it, see it fail for the expected reason. No "I'll add tests after" — that is not TDD and is not accepted here.
2. **Green minimally.** Write the smallest change that makes the test pass. Resist scope creep. Don't add error handling, fallbacks, or abstractions the failing test did not demand.
3. **Refactor under green.** Only refactor while tests are passing. Re-run after each meaningful edit.
4. **Bug fixes start with a regression test.** A bug without a failing test reproducing it is not understood yet.
5. **Eval sets count as tests.** For Tasks 2 and 3 the eval set is part of the test surface — extend it when you discover a new failure mode, before fixing the underlying code.
6. **Don't disable or weaken tests to make them pass.** If a test is wrong, fix the test in its own commit with reasoning; do not bury the change inside an unrelated diff.

What this rules out: writing implementation first and retro-fitting tests; skipping `--no-verify` past a hook failure; mocking the very thing under test (e.g. mocking the LLM in a test that is supposed to validate prompt behavior — mock the network, not the contract).

## Common Requirements (from the brief)

- **Public Git repo** with commit history that reflects real development — no squash-the-world commits.
- **Zeabur deployment** for each task attempted; the URL goes in the README.
- **`prompts/`** at repo root with the key prompts used. The reviewers read these.
- **README** per task: how to run, key design decisions, where AI helped.
- Public or self-created material only.

## Workflow

- Per-task plans live under `prompts/<task>/` (see `prompts/task2.md` for the seed prompt and the plan it produces).
- Commit messages: conventional style (`feat:`, `fix:`, `docs:`, `chore:`, `test:`). Scope by task where useful: `feat(task2): ...`.

## Python tooling

- **Package manager: `uv`.** Don't use `pip`, `pip-tools`, `poetry`, `venv`, or `python -m venv` directly. Each Python task lives in its own directory with its own `pyproject.toml` + `uv.lock` (e.g. `task2/`).
  - Install / sync deps: `uv sync` (run from the task directory).
  - Add a dep: `uv add <pkg>` (runtime) or `uv add --dev <pkg>` (dev). Don't hand-edit `pyproject.toml` for deps when `uv add` will do it.
  - Run anything inside the env: `uv run <cmd>` (e.g. `uv run pytest`, `uv run python -m agent`). Don't activate `.venv` manually.
  - Commit `uv.lock`. Don't commit `.venv/`.
- **Linter / formatter: `ruff`.** It is the only linter and formatter; don't add `black`, `isort`, `flake8`, `pylint`, etc.
  - Lint: `uv run ruff check .` — Format: `uv run ruff format .` — Auto-fix: `uv run ruff check --fix .`.
  - Config lives in each task's `pyproject.toml` under `[tool.ruff]` / `[tool.ruff.lint]`. Don't introduce a separate `ruff.toml` unless a task genuinely needs to diverge.
  - Ruff must be clean before a change is considered done (alongside the TDD green bar).

## Known Constraints

- **Task 2 LLM**: currently DeepSeek (`https://api.deepseek.com`, model `deepseek-chat`), reached via the OpenAI-compatible `/chat/completions` endpoint. Auth key from `DEEPSEEK_API_KEY`; base URL / model overridable via `AGENT_MODEL_BASE_URL` / `AGENT_MODEL_NAME`. The brief originally specified a local Qwen3.5 27B at `http://localhost:8090`; the codebase must keep that swap a one-env-var change (no hardcoded provider, no DeepSeek-specific request shape). DeepSeek returns standard `usage` (`prompt_tokens` / `completion_tokens` / `total_tokens`) on every completion — rely on it for cost/observability rather than re-counting tokens client-side. There is no separate "summarizer" client anymore; all LLM calls go through the single agent client.
- **Task 3 SEC API**: `User-Agent` header required, 10 req/sec ceiling, no API key. Respect the rate limit in code, not just in prose.

## What Not To Do

- Don't add tasks/features beyond what the brief asks for.
- Don't write documentation files unless the brief or the user asks.
- Don't introduce abstractions for hypothetical second callers — the brief lists exactly the callers that exist.
- Don't claim a task is done because tests pass locally; the bar is "deployed on Zeabur and reachable."
