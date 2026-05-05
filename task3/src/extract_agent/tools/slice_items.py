from ..default_classify import classify_default
from ..slicer import slice_items as _slice_items_fn

SCHEMA = {
    "type": "function",
    "function": {
        "name": "slice_items",
        "description": (
            "Slice each item's body between adjacent anchors and apply the "
            "default rule-based status classifier. Stores result on "
            "state.records. Requires find_anchors to have run."
        ),
        "parameters": {
            "type": "object",
            "properties": {"text_id": {"type": "string"}},
            "required": ["text_id"],
        },
    },
}


def run(state, args: dict) -> dict:
    if not state.anchors:
        return {"error": "no anchors in state; call find_anchors first"}
    text = state.get_text(args["text_id"])
    records = _slice_items_fn(text, state.anchors)
    for r in records:
        r["status"] = classify_default(r["item_title"], r["content_text"])
    state.records = records
    summary = [
        f"Item {r['item_number']} [{r['status']}] ({len(r['content_text'])} chars)" for r in records
    ]
    return {"count": len(records), "summary": summary}
