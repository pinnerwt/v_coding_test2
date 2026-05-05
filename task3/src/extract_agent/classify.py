"""Phase 5b status-splitting classifier.

Eligibility filter + small-model fan-out + merge of returned segments.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from .merge_5b import merge_records

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_small.md"

_IBR_RE = re.compile(
    r"incorporat\w*(?:\s+\w+){0,8}\s+by\s+reference",
    re.IGNORECASE,
)


def eligible_indices(records: list[dict]) -> list[int]:
    """Return indices of records that should be sent to the small model.

    A record is eligible iff:
    - status == "extracted" (key must be present),
    - len(content_text) >= 200,
    - NOT (item_number == "15" AND len(content_text) >= 500_000),
    - the IBR phrase regex matches the body.
    """
    out: list[int] = []
    for i, r in enumerate(records):
        if r.get("status") != "extracted":
            continue
        body = r.get("content_text", "")
        if len(body) < 200:
            continue
        if r.get("item_number") == "15" and len(body) >= 500_000:
            continue
        if not _IBR_RE.search(body):
            continue
        out.append(i)
    return out


async def classify_records(
    records: list[dict],
    *,
    client: Any,
    model: str,
) -> tuple[list[dict], list[str], int]:
    """Fan out one small-model call per eligible record, merge segments.

    Returns (new_records, rejections, n_calls).
    """
    idx = eligible_indices(records)
    if not idx:
        return records, [], 0

    system_prompt = _SYSTEM_PROMPT_PATH.read_text()

    async def _one(i: int) -> tuple[int, dict[str, Any], str | None]:
        r = records[i]
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"item_number: {r['item_number']}\n"
                    f"item_title: {r['item_title']}\n"
                    f"body:\n{r['content_text']}"
                ),
            },
        ]
        msg, _usage = await client.chat(messages, temperature=0.0)
        return i, msg, None

    tasks = [_one(i) for i in idx]
    raw = await asyncio.gather(*tasks)

    results: dict[int, list[dict]] = {}
    parse_rejections: list[str] = []
    for i, msg, _ in raw:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            parse_rejections.append(
                f"index {i} item {records[i]['item_number']}: "
                f"bad small-model JSON: response missing content"
            )
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            parse_rejections.append(
                f"index {i} item {records[i]['item_number']}: bad small-model JSON: {e.msg}"
            )
            continue
        if not isinstance(parsed, dict) or "segments" not in parsed:
            parse_rejections.append(
                f"index {i} item {records[i]['item_number']}: "
                f"bad small-model JSON: missing 'segments' key"
            )
            continue
        results[i] = parsed["segments"]

    new_records, merge_rejections = merge_records(records, results)
    return new_records, parse_rejections + merge_rejections, len(idx)
