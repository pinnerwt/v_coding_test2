# Task 3 — SEC 10-K Item-level Structured Extraction

This directory holds Task 3 of the AI Coding Test. Bootstrap stage: a fetch toolbox + survey of how the SEC actually serves 10-Ks. Parser comes in a follow-up.

## Run

```bash
cd task3
uv sync
uv run python -m sec_toolbox --help
```

## Survey

```bash
uv run python -m sec_toolbox survey
```

Outputs: `data/survey/report.md`, `data/survey/report.csv`.
