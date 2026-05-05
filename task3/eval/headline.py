"""Roll up the three eval sources into one headline accuracy table.

Reads `data/extracted/*.json` (regression-set full outputs),
`eval/famous_results.json`, and `eval/cat_e_1995_10ks_results.json`,
then prints a markdown-friendly summary of filings reaching `done`,
items emitted, and cost / latency per source plus an overall total.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_EXTRACTED = REPO / "data" / "extracted"
FAMOUS_RESULTS = REPO / "eval" / "famous_results.json"
CAT_E_RESULTS = REPO / "eval" / "cat_e_1995_10ks_results.json"


def _is_done(entry: dict) -> bool:
    return entry.get("status") == "done" or (
        entry.get("status") is None and entry.get("n_records", 0) > 0
    )


def _summarise(entries: list[dict]) -> dict:
    total = len(entries)
    done_entries = [e for e in entries if _is_done(e)]
    done = len(done_entries)
    items = sum(e.get("n_records", 0) for e in entries)
    costs = [e["cost_usd"] for e in done_entries if "cost_usd" in e]
    elapsed = [e["elapsed"] for e in done_entries if "elapsed" in e]
    return {
        "filings_total": total,
        "filings_done": done,
        "items_emitted": items,
        "cost_median_done": round(statistics.median(costs), 4) if costs else None,
        "latency_median_done": round(statistics.median(elapsed), 1) if elapsed else None,
    }


def aggregate(modern: list[dict], famous: list[dict], cat_e: list[dict]) -> dict:
    sources = {
        "regression": _summarise(modern),
        "famous": _summarise(famous),
        "cat_e": _summarise(cat_e),
    }
    overall = {
        "filings_total": sum(s["filings_total"] for s in sources.values()),
        "filings_done": sum(s["filings_done"] for s in sources.values()),
        "items_emitted": sum(s["items_emitted"] for s in sources.values()),
    }
    return {"sources": sources, "overall": overall}


def _load_modern() -> list[dict]:
    out: list[dict] = []
    for p in sorted(DATA_EXTRACTED.glob("*.json")):
        try:
            records = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(records, list):
            continue
        out.append({
            "label": p.stem,
            "status": "done",
            "n_records": len(records),
        })
    return out


def _load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else []


def main() -> int:
    modern = _load_modern()
    famous = _load_json_list(FAMOUS_RESULTS)
    cat_e = _load_json_list(CAT_E_RESULTS)
    result = aggregate(modern, famous, cat_e)

    src = result["sources"]
    o = result["overall"]
    pct = (o["filings_done"] / o["filings_total"]) if o["filings_total"] else 0.0

    print("=== headline (filings reaching `done`) ===")
    print(f"{'source':<14} {'done/total':<12} {'items':<8} {'med $':<10} {'med s':<8}")
    for name in ("regression", "famous", "cat_e"):
        s = src[name]
        cost = f"${s['cost_median_done']:.4f}" if s["cost_median_done"] is not None else "-"
        lat = f"{s['latency_median_done']:.1f}" if s["latency_median_done"] is not None else "-"
        print(
            f"{name:<14} "
            f"{s['filings_done']}/{s['filings_total']:<10} "
            f"{s['items_emitted']:<8} "
            f"{cost:<10} "
            f"{lat:<8}"
        )
    print(
        f"{'TOTAL':<14} "
        f"{o['filings_done']}/{o['filings_total']:<10} "
        f"{o['items_emitted']:<8} "
        f"({pct:.1%} filings)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
