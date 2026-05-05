SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_chars",
        "description": (
            "Return text_id[start:end], capped at 8 KB. Use to inspect ambiguous "
            "regions (e.g. heading boundaries, suspected TOC vs body anchors)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text_id": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["text_id", "start", "end"],
        },
    },
}

_CAP = 8192


def run(state, args: dict) -> dict:
    text_id = args["text_id"]
    if text_id not in state.text_store:
        return {"error": f"unknown text_id: {text_id}"}
    text = state.get_text(text_id)
    start = args["start"]
    end = args["end"]
    chunk = text[start:end]
    truncated = False
    if len(chunk) > _CAP:
        chunk = chunk[:_CAP]
        truncated = True
    return {"text": chunk, "length": len(chunk), "truncated": truncated}
