"""Merge Phase 5b status-split segments back into a per-filing JSON.

Used by the 10k-extraction skill main thread after fanning out Phase 5b
Haiku agents. Replaces the previous ad-hoc /tmp/<filing>_merge.py scripts.

CLI:
    uv run python scripts/extract/_merge_5b.py <json_path> <segments_path>

Where <segments_path> is a JSON file mapping record-index (string or int)
to a list of segment dicts:

    {
      "0":  [{"status": "extracted", "starts_with": "...", "ends_with": "..."}],
      "16": [
        {"status": "extracted",                 "starts_with": "...", "ends_with": "..."},
        {"status": "incorporated_by_reference", "starts_with": "...", "ends_with": "..."},
        {"status": "extracted",                 "starts_with": "...", "ends_with": "..."}
      ]
    }

Records whose index is missing from <segments_path> are passed through
untouched. Indices map to the JSON's pre-merge order (the order written
by the per-filing extractor).

Library use:

    from _merge_5b import merge_records
    new_records = merge_records(records, results_dict)

`results_dict` keys may be int or str; values are the segment lists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_STATUSES = {
    "extracted",
    "incorporated_by_reference",
    "not_applicable",
    "reserved",
}

QUOTE_FOLD = str.maketrans({
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
})


def fold(s: str) -> str:
    return s.translate(QUOTE_FOLD)


def merge_segments(record: dict, segments: list[dict]) -> tuple[list[dict], str | None]:
    """Apply Phase 5b segments to one parent record.

    Returns (new_records, rejection_reason). On rejection, new_records is
    [record] (parent unchanged) and rejection_reason is a short string;
    on success, rejection_reason is None.
    """
    if not isinstance(segments, list) or not segments:
        return [record], "segments missing or empty"
    for seg in segments:
        if not isinstance(seg, dict):
            return [record], "segment is not a dict"
        if seg.get("status") not in VALID_STATUSES:
            return [record], f"unknown status {seg.get('status')!r}"
        if not isinstance(seg.get("starts_with"), str) or not seg["starts_with"]:
            return [record], "segment missing starts_with"

    body = record["content_text"]
    body_folded = fold(body)
    abs_start = record["char_range"][0]

    spans: list[tuple[int, str]] = []
    for seg in segments:
        snip = fold(seg["starts_with"])
        count = body_folded.count(snip)
        if count == 0:
            return [record], f"starts_with not found: {seg['starts_with'][:60]!r}"
        if count > 1:
            return [record], f"starts_with ambiguous ({count}x): {seg['starts_with'][:60]!r}"
        bs = body_folded.find(snip)
        spans.append((bs, seg["status"]))

    if spans[0][0] > 0:
        if spans[0][1] == "extracted":
            spans[0] = (0, "extracted")
        else:
            spans.insert(0, (0, "extracted"))

    for i in range(1, len(spans)):
        if spans[i][0] <= spans[i - 1][0]:
            return [record], "non-monotonic spans after head fix"

    merged: list[tuple[int, str]] = [spans[0]]
    for bs, st in spans[1:]:
        if st == merged[-1][1]:
            continue
        merged.append((bs, st))

    out: list[dict] = []
    for i, (bs, st) in enumerate(merged):
        be = merged[i + 1][0] if i + 1 < len(merged) else len(body)
        out.append({
            "part": record["part"],
            "item_number": record["item_number"],
            "item_title": record["item_title"],
            "content_text": body[bs:be],
            "char_range": [abs_start + bs, abs_start + be],
            "status": st,
        })
    return out, None


def merge_records(records: list[dict], results: dict) -> tuple[list[dict], list[str]]:
    """Apply a {index -> segments} dict to a list of records. Returns
    (new_records sorted by char_range[0], list of rejection messages)."""
    normalised: dict[int, list[dict]] = {int(k): v for k, v in results.items()}
    new_records: list[dict] = []
    rejections: list[str] = []
    for i, r in enumerate(records):
        if i in normalised:
            merged, reject = merge_segments(r, normalised[i])
            if reject:
                rejections.append(f"index {i} item {r['item_number']}: {reject}")
            new_records.extend(merged)
        else:
            new_records.append(r)
    new_records.sort(key=lambda x: x["char_range"][0])
    return new_records, rejections


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json_path", type=Path, help="Per-filing JSON to rewrite in place.")
    ap.add_argument("segments_path", type=Path, help="JSON file: {index: [segments]}.")
    args = ap.parse_args()

    records = json.loads(args.json_path.read_text())
    results = json.loads(args.segments_path.read_text())

    new_records, rejections = merge_records(records, results)
    args.json_path.write_text(json.dumps(new_records, ensure_ascii=False, indent=2))

    print(f"wrote {len(new_records)} records to {args.json_path}")
    for r in new_records:
        print(
            f"  Part {r['part']} Item {r['item_number']} [{r['status']}] "
            f"-- {r['item_title'][:60]} ({len(r['content_text'])} chars)"
        )
    if rejections:
        print("\nRejections (parent records kept unchanged):", file=sys.stderr)
        for msg in rejections:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
