# Legacy per-filing extractors

Frozen reference corpus. Each `<cik>-<accession>.py` here was a hand-tuned
single-filing extractor produced during Phase 5 of the original `10k-extraction`
skill, before the work was generalized into `task3/src/extract_agent/`. They are
gitignored (`scripts/extract_legacy/*-*.py`); the agent now subsumes their
behavior.

Their JSON outputs in `task3/data/extracted/` (also gitignored) serve as the
ground-truth fixtures for the agent's regression eval (`task3/eval/regression.py`).
Do not edit these scripts further — re-run the agent if you need to refresh outputs.
