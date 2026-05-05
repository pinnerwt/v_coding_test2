SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_record",
        "description": (
            "Inspect one record from state.records by list index. Returns "
            "metadata, a truncated body preview (first 4KB + last 2KB if body "
            "exceeds 6KB), and neighbor stubs above/below to help the model "
            "judge boundary issues."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
            },
            "required": ["index"],
        },
    },
}

_HEAD = 4096
_TAIL = 2048
_THRESHOLD = _HEAD + _TAIL  # 6144
_SENTINEL = "...[truncated]..."


def _neighbor(records, idx):
    if idx < 0 or idx >= len(records):
        return None
    r = records[idx]
    return {"item_number": r.get("item_number"), "char_range": r.get("char_range")}


def run(state, args: dict) -> dict:
    if not state.records:
        return {"error": "no records in state"}
    records = state.records
    i = args["index"]
    if i < 0 or i >= len(records):
        return {"error": f"index {i} out of range (n_records={len(records)})"}
    r = records[i]
    body = r.get("content_text", "")
    is_long = len(body) > _THRESHOLD
    preview = body[:_HEAD] + _SENTINEL + body[-_TAIL:] if is_long else body
    record_meta = {
        "part": r.get("part"),
        "item_number": r.get("item_number"),
        "item_title": r.get("item_title"),
        "char_range": r.get("char_range"),
        "status": r.get("status"),
        "content_text_length": len(body),
    }
    return {
        "record": record_meta,
        "body_preview": preview,
        "truncated": is_long,
        "neighbor_above": _neighbor(records, i - 1),
        "neighbor_below": _neighbor(records, i + 1),
    }
