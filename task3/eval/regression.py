"""Run extract_agent against each filing whose legacy JSON exists in data/extracted/, diff per-item layout, and report drift."""  # noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_TASK3 = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_TASK3 / "data"
EXTRACTED_DIR = DATA_ROOT / "extracted"
INDEX_PATH = DATA_ROOT / "index.json"
OVERRIDES_PATH = REPO_TASK3 / "eval" / "legacy_overrides.json"

SOFT_LEN_THRESHOLD = 0.05


def _load_overrides() -> dict[str, dict]:
    if not OVERRIDES_PATH.exists():
        return {}
    return json.loads(OVERRIDES_PATH.read_text()).get("filings", {})


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
    raise SystemExit(f"no archive entry in index.json for cik={cik} accession={accession}")


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
    legacy: list[dict],
    current: list[dict],
    override: dict | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Return (hard_failures, soft_failures, expected_drift) as strings.

    `override` is a per-filing entry from legacy_overrides.json. Diffs that
    match a tolerated kind are demoted to `expected` instead of `hard`.
    """
    hard: list[str] = []
    soft: list[str] = []
    expected: list[str] = []

    items_override = (override or {}).get("items") or {}

    def _tol(item_no: str, kind: str) -> str | None:
        entry = items_override.get(item_no) or {}
        if kind in (entry.get("tolerate") or []):
            return entry.get("reason", "")
        return None

    legacy_by = _index_by_item(legacy)
    current_by = _index_by_item(current)

    if len(current) != len(legacy):
        msg = f"item count drift: legacy={len(legacy)} current={len(current)}"
        # If any tolerated item allows count_drift, treat the count diff as expected.
        if any("count_drift" in (v.get("tolerate") or []) for v in items_override.values()):
            expected.append(f"{msg} [tolerated by item-level count_drift override]")
        else:
            hard.append(msg)

    for item_no, lrec in legacy_by.items():
        crec = current_by.get(item_no)
        if crec is None:
            msg = f"missing item {item_no}"
            reason = _tol(item_no, "missing")
            if reason is not None:
                expected.append(f"{msg} [tolerated: {reason}]")
            else:
                hard.append(msg)
            continue
        l_status = lrec.get("status")
        c_status = crec.get("status")
        if l_status != c_status:
            msg = f"item {item_no} status flip: legacy={l_status} current={c_status}"
            reason = _tol(item_no, "status_flip")
            if reason is not None:
                expected.append(f"{msg} [tolerated: {reason}]")
            else:
                hard.append(msg)
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
            soft.append(f"item {item_no} char_range hausdorff={haus} (body unchanged)")

    for item_no in current_by:
        if item_no not in legacy_by:
            msg = f"unexpected item {item_no} not in legacy"
            reason = _tol(item_no, "extra")
            if reason is not None:
                expected.append(f"{msg} [tolerated: {reason}]")
            else:
                hard.append(msg)

    return hard, soft, expected


async def _run_agent(html_path: Path, out_path: Path) -> dict:
    from extract_agent.config import Config
    from extract_agent.llm import LLMClient
    from extract_agent.loop import run_loop

    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key)
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


async def _evaluate_one(
    cik: str,
    accession: str,
    legacy_path: Path,
    override: dict | None = None,
) -> tuple[str, list[str], list[str], list[str], float]:
    """Returns (verdict, hard, soft, expected, elapsed_seconds).

    Verdicts: 'ok' (no issues), 'expected' (only tolerated drift),
    'soft' (only soft warnings), 'hard' (real failure).
    """
    start = time.perf_counter()

    if override and override.get("out_of_scope"):
        return (
            "expected",
            [],
            [],
            [f"out-of-scope: {override['out_of_scope']}"],
            time.perf_counter() - start,
        )

    html_path = _resolve_html_path(cik, accession)
    legacy = json.loads(legacy_path.read_text())

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / f"{cik}-{accession}.json"
        result = await _run_agent(html_path, out_path)
        if result.get("status") != "done":
            return (
                "hard",
                [f"agent did not finish: status={result.get('status')}"],
                [],
                [],
                time.perf_counter() - start,
            )
        current = json.loads(out_path.read_text())

    hard, soft, expected = _diff_filing(legacy, current, override)
    elapsed = time.perf_counter() - start
    if hard:
        return "hard", hard, soft, expected, elapsed
    if soft:
        return "soft", hard, soft, expected, elapsed
    if expected:
        return "expected", hard, soft, expected, elapsed
    return "ok", hard, soft, expected, elapsed


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
    p.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="Max concurrent filings (default 4). Set 1 for sequential.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    filings = _legacy_filings(args.cik)
    if not filings:
        print("no legacy filings matched", file=sys.stderr)
        return 1

    overrides = _load_overrides()
    counts = {"ok": 0, "expected": 0, "soft": 0, "hard": 0}
    rows: list[tuple[str, str, str, float]] = []  # (label, verdict, summary, elapsed)

    sem = asyncio.Semaphore(max(1, args.parallel))

    async def _one(cik: str, accession: str, legacy_path: Path):
        async with sem:
            try:
                return await _evaluate_one(
                    cik, accession, legacy_path, overrides.get(f"{cik}-{accession}")
                )
            except Exception as exc:  # noqa: BLE001
                return ("hard", [f"exception: {exc}"], [], [], 0.0)

    async def _run_all():
        return await asyncio.gather(
            *[_one(c, a, p) for c, a, p in filings]
        )

    results = asyncio.run(_run_all())

    for (cik, accession, _legacy_path), (verdict, hard, soft, expected, elapsed) in zip(
        filings, results, strict=True
    ):
        label = f"{cik}-{accession}"
        counts[verdict] += 1
        for msg in expected:
            print(f"[{label}] expected: {msg}", file=sys.stderr)
        ts = f"{elapsed:6.1f}s"
        if verdict == "ok":
            print(f"[{label}] ok ({ts})")
            rows.append((label, "ok", "", elapsed))
        elif verdict == "expected":
            print(f"[{label}] expected ({ts}, tolerated drift only)")
            rows.append((label, "expected", "; ".join(expected), elapsed))
        elif verdict == "soft":
            for msg in soft:
                print(f"[{label}] soft: {msg}", file=sys.stderr)
            print(f"[{label}] soft ({ts})")
            rows.append((label, "soft", "; ".join(soft), elapsed))
        else:
            for msg in hard:
                print(f"[{label}] hard: {msg}", file=sys.stderr)
            for msg in soft:
                print(f"[{label}] soft: {msg}", file=sys.stderr)
            print(f"[{label}] hard ({ts})")
            rows.append((label, "hard", "; ".join(hard), elapsed))

    print()
    print("=== summary ===")
    print(f"filings:  {len(filings)}")
    print(f"ok:       {counts['ok']}")
    print(f"expected: {counts['expected']}")
    print(f"soft:     {counts['soft']}")
    print(f"hard:     {counts['hard']}")
    if rows:
        per_case = [r[3] for r in rows]
        print(
            f"per-case: min={min(per_case):.1f}s "
            f"median={sorted(per_case)[len(per_case)//2]:.1f}s "
            f"max={max(per_case):.1f}s"
        )

    return 0 if counts["hard"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
