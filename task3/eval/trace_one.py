"""Run extract_agent on a single famous filing and dump the tool-call sequence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_TASK3 = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_TASK3 / "data"
LIST_PATH = REPO_TASK3 / "eval" / "famous_10ks.json"


def _ensure_archive(cik: str, accession: str, filename: str) -> Path:
    from sec_toolbox.cache import DiskCache
    from sec_toolbox.client import SECClient
    from sec_toolbox.fetch import Fetcher

    ua = os.environ.get("SEC_USER_AGENT", "Research test@example.com")
    client = SECClient(user_agent=ua)
    cache = DiskCache(root=DATA_ROOT)
    fetcher = Fetcher(client=client, cache=cache)
    return fetcher.archive(cik=cik, accession=accession, filename=filename).path


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("label")
    p.add_argument("--max-steps", type=int, default=50)
    args = p.parse_args(argv)

    entries = json.loads(LIST_PATH.read_text())
    matches = [e for e in entries if args.label.lower() in e["label"].lower()]
    if not matches:
        print(f"no entry matches {args.label!r}", file=sys.stderr)
        return 1
    e = matches[0]
    print(f"running {e['label']} (max_steps={args.max_steps})")

    html_path = _ensure_archive(e["cik"], e["accession"], e["filename"])

    from extract_agent.config import Config
    from extract_agent.llm import LLMClient
    from extract_agent.loop import run_loop

    os.environ["MAX_STEPS"] = str(args.max_steps)
    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            result = await run_loop(
                html_path=str(html_path),
                out_path=str(out),
                cfg=cfg,
                big=big,
                small=small,
            )
            messages = result["messages"]
            print(f"\nstatus: {result.get('status')}  steps: {result['state'].steps}  "
                  f"cost: ${result['state'].cost_usd:.4f}\n")
            print("=== tool-call trace ===")
            for i, m in enumerate(messages):
                role = m.get("role")
                if role == "assistant":
                    tcs = m.get("tool_calls") or []
                    if tcs:
                        for tc in tcs:
                            name = tc.get("function", {}).get("name", "?")
                            raw_args = tc.get("function", {}).get("arguments", "")
                            try:
                                a = json.loads(raw_args) if raw_args else {}
                                short = {
                                    k: (
                                        v
                                        if not isinstance(v, str) or len(v) < 80
                                        else v[:80] + "..."
                                    )
                                    for k, v in a.items()
                                }
                            except Exception:
                                short = raw_args[:80]
                            print(f"  [{i:>3}] CALL {name}({short})")
                elif role == "tool":
                    content = m.get("content", "")
                    try:
                        c = json.loads(content)
                        if isinstance(c, dict) and "error" in c:
                            print(f"  [{i:>3}] RESULT error: {c['error']}")
                        else:
                            keys = list(c.keys()) if isinstance(c, dict) else type(c).__name__
                            preview = json.dumps(c, ensure_ascii=False)[:200]
                            print(f"  [{i:>3}] RESULT {keys}: {preview}")
                    except Exception:
                        print(f"  [{i:>3}] RESULT (raw): {content[:200]}")
    finally:
        await big.aclose()
        await small.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
