"""Cost + token-attribution report from per-session LLM sidecars.

Usage:
    uv run python scripts/cost_report.py --session <sid>
    uv run python scripts/cost_report.py --all
    uv run python scripts/cost_report.py --session <sid> --prices prices.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Per 1M tokens. Verify against current DeepSeek pricing before quoting numbers
# externally — these are best-effort defaults for the analyzer fallback.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek/deepseek-chat": {
        "input_miss": 0.27,
        "input_hit": 0.07,
        "output": 1.10,
    },
}


def price_usage(usage: dict[str, Any], prices: dict[str, float]) -> float:
    """USD for one call's `usage` dict. Honors `prompt_cache_hit_tokens` /
    `prompt_cache_miss_tokens` when present; otherwise treats the full
    `prompt_tokens` as miss."""
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    # Contract: provider returns both fields or neither. If only one
    # is present we trust it as-is and bill the other as 0.
    if hit is None and miss is None:
        hit_n, miss_n = 0, prompt
    else:
        hit_n = int(hit or 0)
        miss_n = int(miss or 0)
    return (
        miss_n * prices["input_miss"] + hit_n * prices["input_hit"] + completion * prices["output"]
    ) / 1_000_000


def _content_len(content: Any) -> int:
    """Return the original character length of a message's content,
    accounting for the truncation marker shape."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, dict) and "original_chars" in content:
        return int(content["original_chars"])
    if isinstance(content, list):
        return sum(_content_len(part.get("text", "")) for part in content if isinstance(part, dict))
    return 0


def _prices_for(model: str | None, base_url: str | None, table: dict) -> dict[str, float]:
    """Look up prices by `provider/model`. Provider derived from base_url
    host (first token before the first '.'). Falls back to deepseek-chat
    defaults if the lookup misses."""
    provider = "deepseek"
    if base_url:
        host = base_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if host:
            provider = host.split(".")[0]
    key = f"{provider}/{model}" if model else None
    if key and key in table:
        return table[key]
    return table.get("deepseek/deepseek-chat", {"input_miss": 0.0, "input_hit": 0.0, "output": 0.0})


def analyze_sidecar(path: Path, *, prices_table: dict) -> dict[str, Any]:
    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    hit_tokens = 0
    miss_tokens = 0
    usd = 0.0
    malformed = 0
    role_chars: dict[str, int] = {"system": 0, "user": 0, "assistant": 0, "tool": 0}

    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        calls += 1
        usage = rec.get("response", {}).get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        hit_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        miss_tokens += int(usage.get("prompt_cache_miss_tokens", 0) or 0)
        prices = _prices_for(
            rec.get("request", {}).get("model"),
            rec.get("request", {}).get("base_url"),
            prices_table,
        )
        usd += price_usage(usage, prices)
        for m in rec.get("request", {}).get("messages", []) or []:
            role = m.get("role") or "unknown"
            if role not in role_chars:
                role_chars[role] = 0
            role_chars[role] += _content_len(m.get("content"))

    total_chars = sum(role_chars.values()) or 1
    role_pct = {r: c / total_chars for r, c in role_chars.items()}

    return {
        "calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": hit_tokens,
        "cache_miss_tokens": miss_tokens,
        "usd": usd,
        "malformed_lines": malformed,
        "role_pct": role_pct,
    }


def _format_report(sid: str, report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Session {sid}")
    lines.append(
        f"  {report['calls']} calls · "
        f"{report['prompt_tokens']:,} prompt / {report['completion_tokens']:,} completion · "
        f"${report['usd']:.4f}"
    )
    p = report["prompt_tokens"] or 1
    hit_rate = report["cache_hit_tokens"] / p
    lines.append(
        f"  Cache hit: {hit_rate * 100:.1f}% ({report['cache_hit_tokens']:,} / {p:,} prompt tokens)"
    )
    if report.get("malformed_lines"):
        lines.append(f"  Malformed lines (skipped): {report['malformed_lines']}")
    lines.append("  Role attribution (by message-content chars):")
    for role in sorted(report["role_pct"], key=lambda r: -report["role_pct"][r]):
        pct = report["role_pct"][role] * 100
        lines.append(f"    {role:<12}: {pct:5.1f}%")
    return "\n".join(lines)


def _load_prices(path: str | None) -> dict:
    if not path:
        return DEFAULT_PRICES
    user_table = json.loads(Path(path).read_text())
    return {**DEFAULT_PRICES, **user_table}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="Session id (filename stem)")
    g.add_argument("--all", action="store_true", help="Report on every sidecar in data/traces/")
    ap.add_argument("--prices", help="Path to prices.json (overrides defaults per key)")
    ap.add_argument(
        "--traces-dir",
        default="data/traces",
        help="Directory containing <sid>.llm.jsonl files (default: data/traces)",
    )
    args = ap.parse_args()

    prices = _load_prices(args.prices)
    traces_dir = Path(args.traces_dir)

    if args.session:
        path = traces_dir / f"{args.session}.llm.jsonl"
        if not path.exists():
            print(f"No sidecar at {path}")
            return 1
        print(_format_report(args.session, analyze_sidecar(path, prices_table=prices)))
        return 0

    found = sorted(traces_dir.glob("*.llm.jsonl"))
    if not found:
        print(f"No sidecars in {traces_dir}")
        return 1
    for p in found:
        sid = p.name.removesuffix(".llm.jsonl")
        print(_format_report(sid, analyze_sidecar(p, prices_table=prices)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
