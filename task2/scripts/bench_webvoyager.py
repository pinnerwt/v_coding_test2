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

ROOT = Path(__file__).parent.parent
DATA = ROOT / "eval" / "webvoyager_tier1.json"
OUT_DIR = ROOT / "data" / "bench"


def _count_blocks(data_dir: Path, since_ts: float) -> int:
    traces_dir = data_dir / "traces"
    if not traces_dir.exists():
        return 0
    candidates = [p for p in traces_dir.glob("*.jsonl") if p.stat().st_mtime >= since_ts]
    if not candidates:
        return 0
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    n = 0
    for line in newest.read_text().splitlines():
        try:
            if json.loads(line).get("type") == "goto_blocked":
                n += 1
        except Exception:
            pass
    return n


async def run_one(client: httpx.AsyncClient, case: dict, timeout_s: float, data_dir: Path) -> dict:
    goal = f"Go to {case['web']} and {case['ques']}"
    t0_wall = time.time()
    t0 = time.monotonic()
    try:
        r = await client.post(
            "/api/run_sync",
            json={"goal": goal},
            timeout=httpx.Timeout(timeout_s, connect=5.0),
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        blocks = _count_blocks(data_dir, t0_wall)
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
            "elapsed_ms": elapsed_ms,
            "blocks": blocks,
        }
    except (httpx.ReadTimeout, httpx.ConnectTimeout):
        return {
            "id": case["id"],
            "web": case["web_name"],
            "status": "timeout",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "blocks": _count_blocks(data_dir, t0_wall),
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

    results = []
    async with httpx.AsyncClient(base_url=args.base_url) as c:
        for case in cases:
            print(f"[bench] {case['id']} {case['web_name']} …", flush=True)
            r = await run_one(c, case, args.timeout, ROOT / "data")
            results.append(r)
            steps = r.get("steps")
            steps_str = f"{steps:>3}" if isinstance(steps, int) else "  ?"
            blocks = r.get("blocks", 0)
            print(
                f"  -> {r['status']:>12}  steps={steps_str}  blocks={blocks}  "
                f"{r['elapsed_ms']:>6}ms  {(r.get('answer') or '')[:80]}",
                flush=True,
            )

    n = len(results)
    succ = sum(1 for r in results if r["status"] == "success")
    print(f"\n[bench] {succ}/{n} succeeded ({100 * succ / n:.0f}%)")

    out_path = OUT_DIR / f"webvoyager_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(
        json.dumps({"started": started, "base_url": args.base_url, "results": results}, indent=2)
    )
    print(f"[bench] wrote {out_path}")
    return 0 if succ == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
