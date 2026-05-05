from ..validate import validate

SCHEMA = {
    "type": "function",
    "function": {
        "name": "validate_records",
        "description": (
            "Run the deterministic validator over state.records. Returns the "
            "list of findings (empty if records pass) and an `ok` boolean. "
            "Call after slice_items / classify_statuses."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def run(state, args: dict) -> dict:
    if state.records is None:
        return {"error": "no records in state"}
    findings = validate(state.records)
    return {"findings": findings, "ok": not findings}
