SCHEMA = {
    "type": "function",
    "function": {
        "name": "done",
        "description": (
            "Signal that extraction is complete. The orchestration loop exits after this call."
        ),
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": [],
        },
    },
}


def run(state, args: dict) -> dict:
    return {"done": True, "message": args.get("message", "")}
