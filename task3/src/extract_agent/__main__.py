"""CLI for the extract_agent: direct path / lookup / queue-drain modes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="extract_agent",
        description="Extract per-item JSON from a SEC 10-K HTML filing.",
    )
    p.add_argument(
        "html_path",
        nargs="?",
        help="Path to a 10-K HTML file (direct mode).",
    )
    p.add_argument("--cik", help="CIK for lookup mode (with --accession).")
    p.add_argument(
        "--accession",
        help="Accession number for lookup mode (with --cik).",
    )
    p.add_argument(
        "--queue",
        help="Path to a markdown queue file (queue-drain mode).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="In queue mode, drain all pending rows.",
    )
    p.add_argument("--out", help="Output JSON path (direct/lookup mode).")
    p.add_argument(
        "--out-dir",
        help="Output directory for queue mode.",
    )
    return p


def _data_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _normalize_accession(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _resolve_html_path(cik: str, accession: str) -> Path:
    """Look up an HTML path by CIK + accession via data/index.json."""
    index_path = _data_root() / "index.json"
    index = json.loads(index_path.read_text())
    target_acc = _normalize_accession(accession)
    target_cik = str(int(cik))  # strip leading zeros
    for entry in index.values():
        if entry.get("endpoint") != "archive":
            continue
        args = entry.get("args") or {}
        e_cik = args.get("cik")
        e_acc = args.get("accession")
        if not e_cik or not e_acc:
            continue
        if str(int(e_cik)) != target_cik:
            continue
        if _normalize_accession(e_acc) != target_acc:
            continue
        return _data_root() / entry["path_relative"]
    raise SystemExit(
        f"no archive entry in index.json for cik={cik} accession={accession}"
    )


def _print_summary(result: dict) -> None:
    from .validate import summary_lines

    state = result["state"]
    records = state.records or []
    for line in summary_lines(records):
        print(line)
    parts = sorted({r.get("part") for r in records if r.get("part")})
    print(f"items={len(records)} parts={parts}")
    print(
        f"cost=${state.cost_usd:.2f} steps={state.steps} status={result['status']}"
    )


def _exit_code_for(status: str) -> int:
    return 0 if status == "done" else 1


async def _run_one(html_path: str, out_path: str) -> dict:
    from .config import Config
    from .llm import LLMClient
    from .loop import run_loop

    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key)
    try:
        return await run_loop(
            html_path=html_path, out_path=out_path, cfg=cfg, big=big, small=small
        )
    finally:
        await big.aclose()
        await small.aclose()


def _run_direct(html_path: str, out_path: str) -> int:
    result = asyncio.run(_run_one(html_path, out_path))
    _print_summary(result)
    return _exit_code_for(result["status"])


def _run_queue(queue_path: str, out_dir: str, drain_all: bool) -> int:
    from . import queue as queue_mod

    qpath = Path(queue_path)
    odir = Path(out_dir)
    odir.mkdir(parents=True, exist_ok=True)

    last_exit = 0
    while True:
        row = queue_mod.next_pending(qpath)
        if row is None:
            return last_exit
        cik = row["cik"]
        accession = row["accession"]
        html_path = _resolve_html_path(cik, accession)
        out_path = odir / f"{cik}-{accession}.json"
        print(f"[queue] cik={cik} accession={accession} -> {out_path}")
        result = asyncio.run(_run_one(str(html_path), str(out_path)))
        _print_summary(result)
        if result["status"] == "done":
            queue_mod.mark_done(qpath, cik=cik, accession=accession)
        else:
            return _exit_code_for(result["status"])
        if not drain_all:
            return 0
    # unreachable
    return last_exit


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Pair check first: catch lone --cik or lone --accession before mode count.
    if bool(args.cik) ^ bool(args.accession):
        parser.error("--cik and --accession must be given together")

    has_pair = bool(args.cik) and bool(args.accession)
    has_queue = bool(args.queue)
    has_direct = bool(args.html_path)

    modes = sum([has_direct, has_pair, has_queue])
    if modes != 1:
        parser.error(
            "choose exactly one mode: <html_path>, --cik+--accession, or --queue"
        )

    if has_pair:
        if not args.out:
            parser.error("--out is required for --cik/--accession mode")
        html_path = _resolve_html_path(args.cik, args.accession)
        return _run_direct(str(html_path), args.out)

    if has_direct:
        if not args.out:
            parser.error("--out is required when running with an html_path")
        return _run_direct(args.html_path, args.out)

    # has_queue
    if not args.out_dir:
        parser.error("--out-dir is required for --queue mode")
    return _run_queue(args.queue, args.out_dir, args.all)


if __name__ == "__main__":
    sys.exit(main())
