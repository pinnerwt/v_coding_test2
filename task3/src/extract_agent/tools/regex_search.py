import re

SCHEMA = {
    "type": "function",
    "function": {
        "name": "regex_search",
        "description": (
            "Run a regex over the cleaned text. Pattern compiled with "
            "IGNORECASE|MULTILINE. Returns up to max_matches (default 50) "
            "{start, end, groups[]} entries. groups[0] is the whole match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text_id": {"type": "string"},
                "pattern": {"type": "string"},
                "max_matches": {"type": "integer"},
            },
            "required": ["text_id", "pattern"],
        },
    },
}


def run(state, args: dict) -> dict:
    text_id = args["text_id"]
    if text_id not in state.text_store:
        return {"error": f"unknown text_id: {text_id}"}
    text = state.get_text(text_id)
    try:
        pattern = re.compile(args["pattern"], re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        return {"error": f"invalid regex: {e}"}
    max_matches = args.get("max_matches", 50)
    matches: list[dict] = []
    truncated = False
    for m in pattern.finditer(text):
        if len(matches) >= max_matches:
            truncated = True
            break
        matches.append(
            {
                "start": m.start(),
                "end": m.end(),
                "groups": [m.group(0), *m.groups()],
            }
        )
    return {"matches": matches, "count": len(matches), "truncated": truncated}
