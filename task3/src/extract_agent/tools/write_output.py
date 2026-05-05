import json
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_output",
        "description": (
            "Serialize state.records as JSON to the given absolute path "
            "(creating parent directories if needed). Returns the path and "
            "record count, or an error if records have not been produced."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "json_path": {
                    "type": "string",
                    "description": "Absolute output path.",
                },
            },
            "required": ["json_path"],
        },
    },
}


def run(state, args: dict) -> dict:
    if state.records is None:
        return {"error": "no records in state"}
    out = Path(args["json_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state.records, ensure_ascii=False, indent=2))
    return {"path": str(out), "count": len(state.records)}
