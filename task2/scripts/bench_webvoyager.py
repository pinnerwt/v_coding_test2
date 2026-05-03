"""Run a slice of the WebVoyager tier1 set through /api/run_sync, score it.

Reuses the dataset published with prior task2 work — each case is just
{id, web_name, ques, web}. The goal we hand the agent is `ques` prefixed
with `Go to {web} and …`. Status comes straight from the agent's done()
call (success | failed | impossible). We don't grade answers; this is a
liveness/coverage probe.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from metrics_row import (
    append_row,
    compute_deltas,
    compute_metrics_row,
    format_deltas,
    read_last_row,
)

ROOT = Path(__file__).parent.parent
DATA = ROOT / "eval" / "webvoyager_tier1.json"
OUT_DIR = ROOT / "data" / "bench"
HISTORY_PATH = OUT_DIR / "metrics_history.jsonl"
LAST_DELTAS_PATH = OUT_DIR / "last_deltas.json"


def _count_blocks(data_dir: Path, before: set[Path]) -> int:
    traces_dir = data_dir / "traces"
    if not traces_dir.exists():
        return 0
    new_files = [p for p in traces_dir.glob("*.jsonl") if p not in before]
    if not new_files:
        return 0
    newest = max(new_files, key=lambda p: p.stat().st_mtime)
    n = 0
    with newest.open() as f:
        for line in f:
            try:
                if json.loads(line).get("type") == "goto_blocked":
                    n += 1
            except json.JSONDecodeError:
                pass
    return n


async def run_one(client: httpx.AsyncClient, case: dict, timeout_s: float, data_dir: Path) -> dict:
    goal = f"Go to {case['web']} and {case['ques']}"
    traces_dir = data_dir / "traces"
    before = set(traces_dir.glob("*.jsonl")) if traces_dir.exists() else set()
    t0 = time.monotonic()
    try:
        r = await client.post(
            "/api/run_sync",
            json={"goal": goal},
            timeout=httpx.Timeout(timeout_s, connect=5.0),
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        blocks = _count_blocks(data_dir, before)
        if r.status_code != 200:
            return {
                "id": case["id"],
                "web": case["web_name"],
                "status": "http_error",
                "http": r.status_code,
                "elapsed_ms": elapsed_ms,
                "blocks": blocks,
            }
        body = r.json()
        return {
            "id": case["id"],
            "web": case["web_name"],
            "status": body.get("status"),
            "answer": (body.get("answer") or "")[:200],
            "steps": body.get("steps"),
            "sid": body.get("sid"),
            "queued_for_ms": body.get("queued_for_ms"),
            "elapsed_ms": elapsed_ms,
            "blocks": blocks,
        }
    except (httpx.ReadTimeout, httpx.ConnectTimeout):
        return {
            "id": case["id"],
            "web": case["web_name"],
            "status": "timeout",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "blocks": _count_blocks(data_dir, before),
        }


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8001")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="per-case seconds; local Qwen takes ~3-4 min per Wikipedia-grade case",
    )
    p.add_argument("--ids", help="comma-separated ids to run instead of --limit")
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "how many cases to run in parallel against the server. "
            "The server caps at 5 (asyncio.Semaphore on run_loop); "
            "higher values just queue."
        ),
    )
    args = p.parse_args()

    cases = json.loads(DATA.read_text())
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    else:
        cases = cases[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    print(f"[bench] {len(cases)} cases, base={args.base_url}, timeout={args.timeout}s/case")

    sem = asyncio.Semaphore(max(1, args.concurrency))
    results: list[dict] = []
    print(f"[bench] concurrency={args.concurrency}", flush=True)

    async with httpx.AsyncClient(base_url=args.base_url) as c:

        async def _bounded(case: dict) -> dict:
            async with sem:
                print(f"[bench] start {case['id']} {case['web_name']}", flush=True)
                r = await run_one(c, case, args.timeout, ROOT / "data")
                steps = r.get("steps")
                steps_str = f"{steps:>3}" if isinstance(steps, int) else "  ?"
                blocks = r.get("blocks", 0)
                print(
                    f"[bench] done  {case['id']} {case['web_name']}: "
                    f"{r['status']:>12}  steps={steps_str}  blocks={blocks}  "
                    f"{r['elapsed_ms']:>6}ms  {(r.get('answer') or '')[:80]}",
                    flush=True,
                )
                return r

        # Preserve original case order in `results` so case→trace mapping
        # via list index lines up with the input order.
        gathered = await asyncio.gather(*[_bounded(case) for case in cases])
        results.extend(gathered)

    n = len(results)
    succ = sum(1 for r in results if r["status"] == "success")
    print(f"\n[bench] {succ}/{n} succeeded ({100 * succ / n:.0f}%)")

    out_path = OUT_DIR / f"webvoyager_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(
        json.dumps({"started": started, "base_url": args.base_url, "results": results}, indent=2)
    )
    print(f"[bench] wrote {out_path}")

    # Append a metrics-history row. Latency we record is the sum of
    # per-case `elapsed_ms` ("total wall time added task by task") —
    # under concurrency > 1 this is intentionally NOT the script's
    # wall-clock runtime, which would under-value cumulative work.
    # Read the previous row BEFORE appending so deltas reflect last
    # vs this run, not this run vs itself.
    try:
        prev_row = read_last_row(HISTORY_PATH)
        row = compute_metrics_row(bench_path=out_path, traces_dir=ROOT / "data" / "traces")
        append_row(row, HISTORY_PATH)
        deltas = compute_deltas(row, prev_row)
        LAST_DELTAS_PATH.write_text(
            json.dumps(
                {
                    "ts": row["ts"],
                    "bench_file": row["bench_file"],
                    "prev_bench_file": prev_row.get("bench_file") if prev_row else None,
                    "deltas": deltas,
                },
                indent=2,
            )
        )
        print(
            f"[bench] appended metrics row to {HISTORY_PATH} "
            f"(latency_sum={row['latency_ms']['sum']}ms, "
            f"calls={row['tokens']['calls']})"
        )
        print(f"[bench] Δ vs previous run ({LAST_DELTAS_PATH}):")
        print(format_deltas(deltas))
    except Exception as e:  # pragma: no cover - defensive: bench JSON wrote, don't lose it
        print(f"[bench] WARN: failed to append metrics row: {e}", file=sys.stderr)

    return 0 if succ == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
