SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_record",
        "description": (
            "Override one record in state.records. Patch is a subset of "
            "{part, item_number, item_title, content_text, char_range, status}. "
            "If char_range is patched, also pass text_id so content_text can be "
            "recomputed from the cleaned text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "patch": {"type": "object"},
                "text_id": {"type": "string"},
            },
            "required": ["index", "patch"],
        },
    },
}

_ALLOWED_KEYS = {"part", "item_number", "item_title", "content_text", "char_range", "status"}


def run(state, args: dict) -> dict:
    if not state.records:
        return {"error": "no records in state"}
    records = state.records
    i = args["index"]
    if i < 0 or i >= len(records):
        return {"error": f"index {i} out of range (n_records={len(records)})"}
    patch = args["patch"]
    unknown = set(patch.keys()) - _ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown patch keys: {sorted(unknown)}"}

    text_id = args.get("text_id")
    if "char_range" in patch and not text_id:
        return {"error": "char_range patch requires text_id to recompute content_text"}

    rec = records[i]
    rec.update(patch)
    if "char_range" in patch:
        if text_id not in state.text_store:
            return {"error": f"unknown text_id: {text_id}"}
        text = state.get_text(text_id)
        cr = patch["char_range"]
        rec["content_text"] = text[cr[0] : cr[1]]

    record_meta = {
        "part": rec.get("part"),
        "item_number": rec.get("item_number"),
        "item_title": rec.get("item_title"),
        "char_range": rec.get("char_range"),
        "status": rec.get("status"),
        "content_text_length": len(rec.get("content_text", "")),
    }
    return {"updated": True, "record": record_meta}
