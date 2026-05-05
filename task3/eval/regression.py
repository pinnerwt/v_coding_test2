"""Run extract_agent against each filing whose legacy JSON exists in data/extracted/, diff per-item layout, and report drift."""  # noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_TASK3 = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_TASK3 / "data"
EXTRACTED_DIR = DATA_ROOT / "extracted"
INDEX_PATH = DATA_ROOT / "index.json"

SOFT_LEN_THRESHOLD = 0.05


def _normalize_accession(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _resolve_html_path(cik: str, accession: str) -> Path:
    """Look up an HTML path by CIK + accession via data/index.json."""
    index = json.loads(INDEX_PATH.read_text())
    target_acc = _normalize_accession(accession)
    target_cik = str(int(cik))
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
        return DATA_ROOT / entry["path_relative"]
    raise SystemExit(
        f"no archive entry in index.json for cik={cik} accession={accession}"
    )


def _hausdorff(a: list[int] | None, b: list[int] | None) -> int | None:
    """Symmetric edge-pair Hausdorff for two integer ranges [a0,a1] vs [b0,b1]."""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _legacy_filings(only_cik: str | None) -> list[tuple[str, str, Path]]:
    """Return [(cik, accession_undashed, legacy_json_path), ...]."""
    out: list[tuple[str, str, Path]] = []
    for p in sorted(EXTRACTED_DIR.glob("*.json")):
        stem = p.stem  # "<cik>-<accession>"
        if "-" not in stem:
            continue
        cik, accession = stem.split("-", 1)
        if only_cik and str(int(cik)) != str(int(only_cik)):
            continue
        out.append((cik, accession, p))
    return out


def _index_by_item(records: list[dict]) -> dict[str, dict]:
    return {str(r.get("item_number")): r for r in records if r.get("item_number")}


def _diff_filing(
    legacy: list[dict], current: list[dict]
) -> tuple[list[str], list[str]]:
    """Return (hard_failures, soft_failures) as human-readable strings."""
    hard: list[str] = []
    soft: list[str] = []

    legacy_by = _index_by_item(legacy)
    current_by = _index_by_item(current)

    if len(current) != len(legacy):
        hard.append(
            f"item count drift: legacy={len(legacy)} current={len(current)}"
        )

    for item_no, lrec in legacy_by.items():
        crec = current_by.get(item_no)
        if crec is None:
            hard.append(f"missing item {item_no}")
            continue
        l_status = lrec.get("status")
        c_status = crec.get("status")
        if l_status != c_status:
            hard.append(
                f"item {item_no} status flip: legacy={l_status} current={c_status}"
            )
            continue
        l_body = lrec.get("content_text") or ""
        c_body = crec.get("content_text") or ""
        l_len = len(l_body)
        c_len = len(c_body)
        delta = abs(c_len - l_len)
        denom = max(l_len, 1)
        ratio = delta / denom
        haus = _hausdorff(lrec.get("char_range"), crec.get("char_range"))
        if ratio >= SOFT_LEN_THRESHOLD:
            soft.append(
                f"item {item_no} body-len drift: legacy={l_len} current={c_len} "
                f"|Δ|/legacy={ratio:.3f} hausdorff={haus}"
            )
        elif haus is not None and haus > 0:
            soft.append(
                f"item {item_no} char_range hausdorff={haus} (body unchanged)"
            )

    for item_no in current_by:
        if item_no not in legacy_by:
            hard.append(f"unexpected item {item_no} not in legacy")

    return hard, soft


async def _run_agent(html_path: Path, out_path: Path) -> dict:
    from extract_agent.config import Config
    from extract_agent.llm import LLMClient
    from extract_agent.loop import run_loop

    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(
        base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key
    )
    try:
        return await run_loop(
            html_path=str(html_path),
            out_path=str(out_path),
            cfg=cfg,
            big=big,
            small=small,
        )
    finally:
        await big.aclose()
        await small.aclose()


def _evaluate_one(
    cik: str, accession: str, legacy_path: Path
) -> tuple[str, list[str], list[str]]:
    """Returns (verdict, hard, soft) where verdict is 'ok' / 'soft' / 'hard'."""
    html_path = _resolve_html_path(cik, accession)
    legacy = json.loads(legacy_path.read_text())

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / f"{cik}-{accession}.json"
        result = asyncio.run(_run_agent(html_path, out_path))
        if result.get("status") != "done":
            return (
                "hard",
                [f"agent did not finish: status={result.get('status')}"],
                [],
            )
        current = json.loads(out_path.read_text())

    hard, soft = _diff_filing(legacy, current)
    if hard:
        return "hard", hard, soft
    if soft:
        return "soft", hard, soft
    return "ok", hard, soft


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval/regression.py",
        description=(
            "Run extract_agent against legacy ground-truth JSONs in data/extracted/ "
            "and report per-item drift."
        ),
    )
    p.add_argument(
        "--cik",
        help="Restrict to one filing by CIK (matched against legacy filenames).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    filings = _legacy_filings(args.cik)
    if not filings:
        print("no legacy filings matched", file=sys.stderr)
        return 1

    counts = {"ok": 0, "soft": 0, "hard": 0}
    rows: list[tuple[str, str, str]] = []  # (label, verdict, summary)

    for cik, accession, legacy_path in filings:
        label = f"{cik}-{accession}"
        try:
            verdict, hard, soft = _evaluate_one(cik, accession, legacy_path)
        except Exception as exc:  # noqa: BLE001
            counts["hard"] += 1
            print(f"[{label}] hard: exception: {exc}", file=sys.stderr)
            rows.append((label, "hard", f"exception: {exc}"))
            continue

        counts[verdict] += 1
        if verdict == "ok":
            print(f"[{label}] ok")
            rows.append((label, "ok", ""))
        elif verdict == "soft":
            for msg in soft:
                print(f"[{label}] soft: {msg}", file=sys.stderr)
            rows.append((label, "soft", "; ".join(soft)))
        else:
            for msg in hard:
                print(f"[{label}] hard: {msg}", file=sys.stderr)
            for msg in soft:
                print(f"[{label}] soft: {msg}", file=sys.stderr)
            rows.append((label, "hard", "; ".join(hard)))

    print()
    print("=== summary ===")
    print(f"filings: {len(filings)}")
    print(f"ok:   {counts['ok']}")
    print(f"soft: {counts['soft']}")
    print(f"hard: {counts['hard']}")

    return 0 if counts["hard"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
