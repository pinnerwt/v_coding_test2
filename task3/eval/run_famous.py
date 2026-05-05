"""Run the extract_agent against the 10 famous-and-hard 10-K filings in
eval/famous_10ks.json. No legacy ground truth — this is a smoke test for
wider-coverage support. Per-case timing, parallel fan-out, summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_TASK3 = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_TASK3 / "data"
ARCHIVE_ROOT = DATA_ROOT / "raw" / "archive"
DEFAULT_LIST_PATH = REPO_TASK3 / "eval" / "famous_10ks.json"


def _accession_no_dashes(s: str) -> str:
    return s.replace("-", "")


def _ensure_archive(cik: str, accession: str, filename: str) -> Path:
    """Use sec_toolbox.Fetcher to cache the archive locally."""
    from sec_toolbox.cache import DiskCache
    from sec_toolbox.client import SECClient
    from sec_toolbox.fetch import Fetcher

    cache_dir = DATA_ROOT
    target = ARCHIVE_ROOT / cik / _accession_no_dashes(accession) / filename
    if target.exists():
        return target

    ua = os.environ.get("SEC_USER_AGENT", "Research test@example.com")
    client = SECClient(user_agent=ua)
    cache = DiskCache(root=cache_dir)
    fetcher = Fetcher(client=client, cache=cache)
    entry = fetcher.archive(cik=cik, accession=accession, filename=filename)
    return entry.path


async def _run_one(label: str, html_path: Path, timeout_s: float) -> dict:
    from extract_agent.config import Config
    from extract_agent.llm import LLMClient
    from extract_agent.loop import run_loop

    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key)
    start = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            try:
                result = await asyncio.wait_for(
                    run_loop(
                        html_path=str(html_path),
                        out_path=str(out),
                        cfg=cfg,
                        big=big,
                        small=small,
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                return {
                    "label": label,
                    "status": "wall_timeout",
                    "error": f"exceeded {timeout_s}s wall-clock cap",
                    "elapsed": round(time.perf_counter() - start, 1),
                }
            records = json.loads(out.read_text()) if out.exists() else []
        return {
            "label": label,
            "status": result.get("status"),
            "steps": result["state"].steps,
            "cost_usd": round(result["state"].cost_usd, 4),
            "elapsed": round(time.perf_counter() - start, 1),
            "n_records": len(records),
            "items": [
                (r.get("item_number"), r.get("status"), len(r.get("content_text") or ""))
                for r in records
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed": round(time.perf_counter() - start, 1),
        }
    finally:
        await big.aclose()
        await small.aclose()


async def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--filter", help="Substring of label to restrict run")
    p.add_argument(
        "--per-case-timeout",
        type=float,
        default=240.0,
        help="Wall-clock cap per filing in seconds (default 240).",
    )
    p.add_argument(
        "--list",
        dest="list_path",
        default=str(DEFAULT_LIST_PATH),
        help="Path to a JSON list of {label, cik, accession, filename} entries.",
    )
    p.add_argument(
        "--out",
        dest="out_path",
        default=None,
        help="Where to write the results JSON. Defaults to <list-stem>_results.json.",
    )
    args = p.parse_args(argv)

    list_path = Path(args.list_path)
    out_path = Path(args.out_path) if args.out_path else list_path.with_name(
        list_path.stem + "_results.json"
    )
    entries = json.loads(list_path.read_text())
    if args.filter:
        entries = [e for e in entries if args.filter.lower() in e["label"].lower()]

    sem = asyncio.Semaphore(max(1, args.parallel))

    async def _wrapped(e: dict) -> dict:
        async with sem:
            try:
                html_path = _ensure_archive(e["cik"], e["accession"], e["filename"])
            except Exception as exc:  # noqa: BLE001
                return {
                    "label": e["label"],
                    "status": "fetch_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed": 0.0,
                }
            return await _run_one(e["label"], html_path, args.per_case_timeout)

    results = await asyncio.gather(*[_wrapped(e) for e in entries])

    print()
    print("=== famous-10K agent run summary ===")
    for r in results:
        line = (
            f"[{r['label']:>20s}] status={r.get('status'):<22s} "
            f"elapsed={r.get('elapsed'):>6}s"
        )
        if "steps" in r:
            line += (
                f"  steps={r['steps']:>3}  cost=${r.get('cost_usd', 0):.4f}  "
                f"records={r.get('n_records')}"
            )
        if "error" in r:
            line += f"  err={r['error']}"
        print(line)

    print()
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    for k, v in sorted(by_status.items()):
        print(f"  {k}: {v}")

    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
