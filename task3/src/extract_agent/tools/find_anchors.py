import re

from ..anchors import dedupe_anchors
from ..anchors import find_anchors as _find_anchors_fn

SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_anchors",
        "description": (
            "Run ITEM_RE over the cleaned text and dedupe TOC vs body anchors. "
            "Stores the deduped anchor list on session state and returns: "
            "`count` (total surviving anchors), `by_item` (item -> longest-body "
            "anchor offset; not last-write-wins), `duplicates` (items that have "
            "more than one surviving anchor, e.g. body + tail cross-ref index). "
            "Call after clean_and_load. Pass an optional `regex` to override the "
            "default ITEM_RE for filings with non-standard heading shapes, or "
            "`toc_threshold` to override the TOC region cutoff (default 8000)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text_id": {"type": "string"},
                "regex": {
                    "type": "string",
                    "description": (
                        "Optional override regex (Python re syntax, compiled "
                        "with IGNORECASE|MULTILINE). Must expose either: "
                        "(a) named group `item_number` (preferred), with "
                        "optional `item_letter` and `item_title`; or "
                        "(b) the canonical ITEM_RE positional layout: "
                        "group(1)=item_number, group(2)=item_letter, "
                        "group(3)=item_title. A regex that does not satisfy "
                        "either contract returns a clear error — do not retry "
                        "the same shape; switch to named groups."
                    ),
                },
                "toc_threshold": {
                    "type": "integer",
                    "description": (
                        "Char offset; for items with an anchor past this offset, "
                        "earlier anchors for that item are dropped. Defaults to 8000."
                    ),
                },
            },
            "required": ["text_id"],
        },
    },
}


def run(state, args: dict) -> dict:
    text = state.get_text(args["text_id"])
    kwargs = {}
    if args.get("regex"):
        kwargs["regex"] = re.compile(args["regex"], re.IGNORECASE | re.MULTILINE)
    raw = _find_anchors_fn(text, **kwargs)
    anchors = dedupe_anchors(raw, toc_region_end=args.get("toc_threshold", 8000))
    state.anchors = anchors

    # Group surviving anchors by item; pick the candidate with the longest body
    # (gap to the next anchor's match_start) so by_item is not last-write-wins.
    sorted_starts = sorted(a["match_start"] for a in anchors)
    by_num: dict[str, list[dict]] = {}
    for a in anchors:
        by_num.setdefault(a["item_number"], []).append(a)

    by_item: dict[str, int] = {}
    duplicates: dict[str, int] = {}
    for item_id, alist in by_num.items():
        best = None
        best_body_len = -1
        for a in alist:
            idx = sorted_starts.index(a["match_start"])
            next_start = (
                sorted_starts[idx + 1] if idx + 1 < len(sorted_starts) else len(text)
            )
            body_len = next_start - a["match_end"]
            if body_len > best_body_len:
                best_body_len = body_len
                best = a
        by_item[item_id] = best["match_start"]
        if len(alist) > 1:
            duplicates[item_id] = len(alist)

    return {
        "count": len(anchors),
        "by_item": by_item,
        "duplicates": duplicates,
    }
