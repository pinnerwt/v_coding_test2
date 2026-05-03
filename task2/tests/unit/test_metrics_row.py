"""TDD spec for scripts/metrics_row.py — the permanent home of the
metrics-history aggregation that `/bench-sweep` previously did via
heredoc.

Latency contract under test: `latency_ms.sum` is the sum of per-case
`elapsed_ms` ("total wall time added task by task"), NOT the bench
script's wall-clock runtime. Under concurrency > 1 the latter
under-values cumulative work, so we keep the former as the canonical
latency metric. The schema here is a contract with the downstream
charting script — additions need a deliberate change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from metrics_row import (
    append_row,
    compute_deltas,
    compute_metrics_row,
    read_last_row,
)


def _write_sidecar(path: Path, *, calls: list[dict]) -> None:
    """Write a minimal `<sid>.llm.jsonl` with the usage shape DeepSeek
    returns (prompt_cache_hit_tokens / prompt_cache_miss_tokens)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for call in calls:
            f.write(json.dumps({"response": {"usage": call}}) + "\n")


def _make_bench_json(
    tmp_path: Path,
    *,
    results: list[dict],
    started: str = "2026-05-03T14:00:00+00:00",
) -> Path:
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    p = bench_dir / "webvoyager_TEST.json"
    p.write_text(
        json.dumps(
            {
                "started": started,
                "base_url": "http://127.0.0.1:8001",
                "results": results,
            }
        )
    )
    return p


def test_latency_sum_is_per_case_wall_time_added(tmp_path: Path) -> None:
    """The headline `latency_ms.sum` must equal Σ per-case `elapsed_ms`,
    even under parallel runs where the bench script wall-clock would be
    much shorter. This is the user-stated semantic and the contract with
    the charting script."""
    sid_a, sid_b, sid_c = "aaa", "bbb", "ccc"
    traces = tmp_path / "traces"
    for sid in (sid_a, sid_b, sid_c):
        _write_sidecar(traces / f"{sid}.llm.jsonl", calls=[])

    bench = _make_bench_json(
        tmp_path,
        results=[
            {"id": "101", "web": "X", "status": "success", "elapsed_ms": 100_000, "sid": sid_a},
            {"id": "102", "web": "Y", "status": "success", "elapsed_ms": 200_000, "sid": sid_b},
            {"id": "103", "web": "Z", "status": "success", "elapsed_ms": 300_000, "sid": sid_c},
        ],
    )
    row = compute_metrics_row(bench_path=bench, traces_dir=traces)

    assert row["latency_ms"]["sum"] == 600_000
    assert row["latency_ms"]["avg"] == 200_000
    assert row["latency_ms"]["p50"] == 200_000


def test_aggregates_tokens_from_sidecars(tmp_path: Path) -> None:
    sid_a, sid_b = "aaa", "bbb"
    traces = tmp_path / "traces"
    _write_sidecar(
        traces / f"{sid_a}.llm.jsonl",
        calls=[
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_cache_hit_tokens": 600,
                "prompt_cache_miss_tokens": 400,
            },
            {
                "prompt_tokens": 500,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 300,
                "prompt_cache_miss_tokens": 200,
            },
        ],
    )
    _write_sidecar(
        traces / f"{sid_b}.llm.jsonl",
        calls=[
            {
                "prompt_tokens": 200,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 100,
                "prompt_cache_miss_tokens": 100,
            },
        ],
    )

    bench = _make_bench_json(
        tmp_path,
        results=[
            {"id": "101", "web": "X", "status": "success", "elapsed_ms": 50, "sid": sid_a},
            {"id": "102", "web": "Y", "status": "failed", "elapsed_ms": 30, "sid": sid_b},
        ],
    )
    row = compute_metrics_row(bench_path=bench, traces_dir=traces)

    assert row["tokens"]["input_total"] == 1700
    assert row["tokens"]["input_cached"] == 1000
    assert row["tokens"]["input_uncached"] == 700
    assert row["tokens"]["output"] == 170
    assert row["tokens"]["calls"] == 3

    by_id = {c["id"]: c for c in row["per_case"]}
    assert by_id["101"]["input_cached"] == 900
    assert by_id["101"]["output"] == 150
    assert by_id["101"]["calls"] == 2
    assert by_id["102"]["input_cached"] == 100
    assert by_id["102"]["calls"] == 1


def test_success_rate_and_status_pass_through(tmp_path: Path) -> None:
    sids = ["a", "b", "c", "d"]
    traces = tmp_path / "traces"
    for sid in sids:
        _write_sidecar(traces / f"{sid}.llm.jsonl", calls=[])
    bench = _make_bench_json(
        tmp_path,
        results=[
            {"id": "101", "web": "X", "status": "success", "elapsed_ms": 10, "sid": "a"},
            {"id": "102", "web": "X", "status": "failed", "elapsed_ms": 20, "sid": "b"},
            {"id": "103", "web": "X", "status": "success", "elapsed_ms": 30, "sid": "c"},
            {"id": "104", "web": "X", "status": "timeout", "elapsed_ms": 40, "sid": "d"},
        ],
    )
    row = compute_metrics_row(bench_path=bench, traces_dir=traces)

    assert row["n_total"] == 4
    assert row["n_success"] == 2
    assert row["success_rate"] == pytest.approx(0.5)
    assert row["case_ids"] == ["101", "102", "103", "104"]
    statuses = {c["id"]: c["status"] for c in row["per_case"]}
    assert statuses == {"101": "success", "102": "failed", "103": "success", "104": "timeout"}


def test_missing_sidecar_yields_null_token_fields(tmp_path: Path) -> None:
    """If a case has a sid but the sidecar is missing, per-case token
    fields must be null and excluded from totals — the row still
    appends, with a noted gap."""
    traces = tmp_path / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    # Only sid "have" gets a sidecar; "missing" does not.
    _write_sidecar(
        traces / "have.llm.jsonl",
        calls=[
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_cache_hit_tokens": 60,
                "prompt_cache_miss_tokens": 40,
            }
        ],
    )

    bench = _make_bench_json(
        tmp_path,
        results=[
            {"id": "101", "web": "X", "status": "success", "elapsed_ms": 10, "sid": "have"},
            {"id": "102", "web": "X", "status": "success", "elapsed_ms": 20, "sid": "missing"},
        ],
    )
    row = compute_metrics_row(bench_path=bench, traces_dir=traces)

    by_id = {c["id"]: c for c in row["per_case"]}
    assert by_id["102"]["input_total"] is None
    assert by_id["102"]["calls"] is None
    # Totals only include cases that had a sidecar.
    assert row["tokens"]["input_total"] == 100
    assert row["tokens"]["calls"] == 1


def test_append_row_is_append_only(tmp_path: Path) -> None:
    """`append_row` must never rewrite prior rows. One JSON object per
    line; existing lines preserved verbatim."""
    history = tmp_path / "metrics_history.jsonl"
    history.write_text(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "n_total": 1}) + "\n")

    append_row({"ts": "2026-05-03T14:00:00+00:00", "n_total": 13}, history)

    lines = history.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ts"] == "2026-01-01T00:00:00+00:00"
    assert json.loads(lines[1])["n_total"] == 13


def test_append_row_creates_file_if_missing(tmp_path: Path) -> None:
    history = tmp_path / "does_not_exist_yet.jsonl"
    append_row({"ts": "2026-05-03T14:00:00+00:00", "n_total": 13}, history)
    assert history.exists()
    [line] = history.read_text().splitlines()
    assert json.loads(line)["n_total"] == 13


def test_row_carries_ts_and_bench_filename(tmp_path: Path) -> None:
    """The ts comes from the bench JSON's `started` field; bench_file
    is the basename of the bench JSON. These let the chart axis read
    real timestamps."""
    traces = tmp_path / "traces"
    _write_sidecar(traces / "x.llm.jsonl", calls=[])
    bench = _make_bench_json(
        tmp_path,
        results=[{"id": "101", "web": "X", "status": "success", "elapsed_ms": 10, "sid": "x"}],
        started="2026-05-03T14:00:00+00:00",
    )
    row = compute_metrics_row(bench_path=bench, traces_dir=traces)
    assert row["ts"] == "2026-05-03T14:00:00+00:00"
    assert row["bench_file"] == "webvoyager_TEST.json"


# ---- Step-6 deltas (Δ vs previous row in metrics_history.jsonl) -----


def _row(
    success=11,
    n=13,
    avg=100_000,
    p95=200_000,
    sum_=1_300_000,
    in_cached=400_000,
    in_uncached=900_000,
    out=50_000,
    calls=300,
):
    return {
        "ts": "2026-05-03T14:00:00+00:00",
        "n_total": n,
        "n_success": success,
        "success_rate": round(success / n, 4),
        "latency_ms": {"avg": avg, "p50": avg, "p95": p95, "sum": sum_},
        "tokens": {
            "input_total": in_cached + in_uncached,
            "input_cached": in_cached,
            "input_uncached": in_uncached,
            "output": out,
            "calls": calls,
        },
    }


def test_deltas_compute_abs_and_pct_for_each_tracked_field() -> None:
    new = _row(
        success=11,
        avg=95_000,
        p95=200_000,
        in_cached=98_765,
        in_uncached=24_691,
        out=6_789,
        calls=234,
    )
    prev = _row(
        success=10,
        avg=102_000,
        p95=210_000,
        in_cached=80_000,
        in_uncached=30_000,
        out=5_977,
        calls=220,
    )

    d = compute_deltas(new, prev)

    # success_rate: 0.8462 - 0.7692 = +0.0770 (+10.0%)
    assert d["success_rate"]["new"] == pytest.approx(0.8462, abs=1e-4)
    assert d["success_rate"]["prev"] == pytest.approx(0.7692, abs=1e-4)
    assert d["success_rate"]["abs_delta"] == pytest.approx(0.077, abs=1e-3)
    assert d["success_rate"]["pct_delta"] == pytest.approx(10.0, abs=0.2)

    # latency_ms.avg: -6.9%
    assert d["latency_ms.avg"]["abs_delta"] == -7_000
    assert d["latency_ms.avg"]["pct_delta"] == pytest.approx(-6.86, abs=0.1)

    # tokens.output: +812 (+13.6%)
    assert d["tokens.output"]["abs_delta"] == 812
    assert d["tokens.output"]["pct_delta"] == pytest.approx(13.6, abs=0.1)

    # tokens.calls: +14 (+6.4%)
    assert d["tokens.calls"]["abs_delta"] == 14
    assert d["tokens.calls"]["pct_delta"] == pytest.approx(6.36, abs=0.1)


def test_deltas_when_no_previous_row_marks_first_run() -> None:
    new = _row()
    d = compute_deltas(new, None)
    for field in (
        "success_rate",
        "latency_ms.avg",
        "latency_ms.p95",
        "tokens.input_cached",
        "tokens.input_uncached",
        "tokens.output",
        "tokens.calls",
    ):
        assert d[field]["prev"] is None
        assert d[field]["abs_delta"] is None
        assert d[field]["pct_delta"] is None
        assert d[field]["new"] is not None


def test_deltas_handle_zero_previous_without_div_by_zero() -> None:
    new = _row(out=100)
    prev = _row(out=0)  # baseline at zero
    d = compute_deltas(new, prev)
    # abs_delta is well-defined; pct_delta is None because prev=0.
    assert d["tokens.output"]["abs_delta"] == 100
    assert d["tokens.output"]["pct_delta"] is None


def test_deltas_handle_missing_field_in_prev_row() -> None:
    """Old history rows may lack a field that newer rows include —
    treat it as if prev had no data for that field."""
    new = _row()
    prev = _row()
    # Drop one field from prev to simulate an older schema.
    prev["tokens"] = {k: v for k, v in prev["tokens"].items() if k != "calls"}
    d = compute_deltas(new, prev)
    assert d["tokens.calls"]["prev"] is None
    assert d["tokens.calls"]["abs_delta"] is None
    assert d["tokens.calls"]["pct_delta"] is None


def test_read_last_row_returns_none_for_empty_or_missing_history(tmp_path: Path) -> None:
    missing = tmp_path / "no_such.jsonl"
    assert read_last_row(missing) is None
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert read_last_row(empty) is None


def test_read_last_row_skips_blank_lines(tmp_path: Path) -> None:
    """Trailing whitespace or blank line at EOF must not cause a parse
    error — we want the last NON-BLANK JSON object."""
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({"ts": "2026-01-01", "n_total": 1})
        + "\n"
        + json.dumps({"ts": "2026-02-01", "n_total": 2})
        + "\n"
        + "\n"
    )
    last = read_last_row(history)
    assert last is not None
    assert last["n_total"] == 2
