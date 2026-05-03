"""Aggregate one bench run into a `metrics_history.jsonl` row and
append it. Used by `bench_webvoyager.py` at the end of a sweep and
addressable as a CLI for back-filling history from older bench JSONs.

Latency contract: `latency_ms.sum` is the **sum of per-case
`elapsed_ms`** — "total wall time added task by task". Under
concurrency > 1 this is intentionally NOT the bench script's
wall-clock runtime, which would under-value the cumulative work. The
charting downstream of `metrics_history.jsonl` plots the per-case-sum
so two runs at different concurrency settings remain comparable.

Schema kept stable on purpose; downstream chart scripts read these
fields by name.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def _read_sidecar(path: Path) -> dict[str, Any] | None:
    """Sum DeepSeek `usage` across every call in a sidecar. Returns
    None if the sidecar is absent so callers can mark per-case token
    fields as null without dropping the row."""
    if not path.exists():
        return None
    input_total = input_cached = input_uncached = output = calls = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (rec.get("response") or {}).get("usage") or {}
            input_total += int(usage.get("prompt_tokens", 0) or 0)
            input_cached += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
            input_uncached += int(usage.get("prompt_cache_miss_tokens", 0) or 0)
            output += int(usage.get("completion_tokens", 0) or 0)
            calls += 1
    return {
        "input_total": input_total,
        "input_cached": input_cached,
        "input_uncached": input_uncached,
        "output": output,
        "calls": calls,
    }


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    s = sorted(values)
    # Nearest-rank, matching the heredoc's prior behavior closely
    # enough for plotting; exact method swap would shift history.
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return int(s[k])


def compute_metrics_row(*, bench_path: Path, traces_dir: Path) -> dict[str, Any]:
    """Build one history row from a finished bench JSON + its sidecars.

    `bench_path` points at a `webvoyager_*.json` written by
    `bench_webvoyager.py`. `traces_dir` is where `<sid>.llm.jsonl`
    lives (typically `task2/data/traces`)."""
    bench_path = Path(bench_path)
    traces_dir = Path(traces_dir)
    bench = json.loads(bench_path.read_text())
    results = bench.get("results", [])

    per_case: list[dict[str, Any]] = []
    elapsed_values: list[int] = []
    totals = {"input_total": 0, "input_cached": 0, "input_uncached": 0, "output": 0, "calls": 0}

    for r in results:
        elapsed = int(r.get("elapsed_ms") or 0)
        elapsed_values.append(elapsed)
        sid = r.get("sid")
        tokens: dict[str, Any] | None = None
        if sid:
            tokens = _read_sidecar(traces_dir / f"{sid}.llm.jsonl")
        case_row: dict[str, Any] = {
            "id": r.get("id"),
            "web": r.get("web"),
            "status": r.get("status"),
            "elapsed_ms": elapsed,
            "queued_for_ms": r.get("queued_for_ms"),
            "sid": sid,
        }
        if tokens is None:
            case_row.update(
                {
                    "input_total": None,
                    "input_cached": None,
                    "input_uncached": None,
                    "output": None,
                    "calls": None,
                }
            )
        else:
            case_row.update(tokens)
            for k in totals:
                totals[k] += tokens[k]
        per_case.append(case_row)

    n_total = len(results)
    n_success = sum(1 for r in results if r.get("status") == "success")
    success_rate = round(n_success / n_total, 4) if n_total else 0.0

    avg = int(statistics.mean(elapsed_values)) if elapsed_values else 0
    p50 = _percentile(elapsed_values, 0.5)
    p95 = _percentile(elapsed_values, 0.95)
    sum_ms = sum(elapsed_values)

    return {
        "ts": bench.get("started"),
        "bench_file": bench_path.name,
        "case_ids": [r.get("id") for r in results],
        "n_total": n_total,
        "n_success": n_success,
        "success_rate": success_rate,
        "latency_ms": {"avg": avg, "p50": p50, "p95": p95, "sum": sum_ms},
        "tokens": totals,
        "per_case": per_case,
    }


def append_row(row: dict[str, Any], history_path: Path) -> None:
    """Append one JSON row (single-line) to `metrics_history.jsonl`.
    Append-only: existing rows are never read or rewritten."""
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def read_last_row(history_path: Path) -> dict[str, Any] | None:
    """Last non-blank JSON row from `metrics_history.jsonl`, or None
    if the file is missing/empty. Used to seed Δ vs previous run."""
    history_path = Path(history_path)
    if not history_path.exists():
        return None
    last: str | None = None
    with history_path.open() as f:
        for line in f:
            s = line.strip()
            if s:
                last = s
    if last is None:
        return None
    return json.loads(last)


# Fields tracked in Step-6 deltas. Dotted path = nested lookup. Order
# is the order the chart / observations table renders them in.
_DELTA_FIELDS = (
    "success_rate",
    "latency_ms.avg",
    "latency_ms.p95",
    "tokens.input_cached",
    "tokens.input_uncached",
    "tokens.output",
    "tokens.calls",
)


def _lookup(row: dict[str, Any] | None, path: str) -> Any | None:
    if row is None:
        return None
    cur: Any = row
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def compute_deltas(
    new_row: dict[str, Any], prev_row: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """Per-field Δ vs previous row. Each entry: `{new, prev, abs_delta,
    pct_delta}`. `prev_row=None` (first run) and missing-field cases
    yield prev/abs_delta/pct_delta = None. `pct_delta` is None when
    prev is 0 (avoid div-by-zero) or when either side is None."""
    out: dict[str, dict[str, Any]] = {}
    for path in _DELTA_FIELDS:
        new_v = _lookup(new_row, path)
        prev_v = _lookup(prev_row, path)
        entry: dict[str, Any] = {
            "new": new_v,
            "prev": prev_v,
            "abs_delta": None,
            "pct_delta": None,
        }
        if new_v is not None and prev_v is not None:
            entry["abs_delta"] = new_v - prev_v
            if prev_v != 0:
                entry["pct_delta"] = round((new_v - prev_v) / prev_v * 100, 2)
        out[path] = entry
    return out


def format_deltas(deltas: dict[str, dict[str, Any]]) -> str:
    """One-line-per-field human-readable Δ block. Used by the bench
    script's stdout summary; the skill reads `last_deltas.json` for
    the structured form."""
    lines = []
    for path, e in deltas.items():
        new_v = e["new"]
        new_s = f"{new_v:,.4f}" if isinstance(new_v, float) else f"{new_v:,}"
        if e["abs_delta"] is None:
            lines.append(f"  {path}: {new_s} (Δ n/a)")
            continue
        ad = e["abs_delta"]
        sign = "+" if ad >= 0 else ""
        ad_s = f"{sign}{ad:,.4f}" if isinstance(ad, float) else f"{sign}{ad:,}"
        if e["pct_delta"] is None:
            lines.append(f"  {path}: {new_s} (Δ {ad_s})")
        else:
            pd = e["pct_delta"]
            psign = "+" if pd >= 0 else ""
            lines.append(f"  {path}: {new_s} (Δ {ad_s}, {psign}{pd:.1f}%)")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bench_json", help="Path to webvoyager_*.json")
    p.add_argument(
        "--traces-dir",
        default="data/traces",
        help="Directory with <sid>.llm.jsonl sidecars",
    )
    p.add_argument(
        "--history",
        default="data/bench/metrics_history.jsonl",
        help="Append target",
    )
    args = p.parse_args()

    row = compute_metrics_row(bench_path=Path(args.bench_json), traces_dir=Path(args.traces_dir))
    append_row(row, Path(args.history))
    print(
        f"Appended row: {row['n_success']}/{row['n_total']} succeeded, "
        f"latency_sum={row['latency_ms']['sum']}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
