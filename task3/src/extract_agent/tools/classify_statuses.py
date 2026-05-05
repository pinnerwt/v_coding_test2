from ..classify import classify_records

SCHEMA = {
    "type": "function",
    "function": {
        "name": "classify_statuses",
        "description": (
            "Run Phase 5b status splitting: for each record whose body might mix "
            "extracted prose with incorporated-by-reference sentences, fan out a "
            "small-model classification call. Replaces eligible records with "
            "sub-records (one per status segment). Records that don't meet the "
            "eligibility filter are passed through unchanged."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


async def run(state, args, *, client, model):
    if state.records is None:
        return {"error": "no records in state"}
    new_records, rejections, n_calls = await classify_records(
        state.records, client=client, model=model
    )
    state.records = new_records
    return {
        "calls": n_calls,
        "new_record_count": len(new_records),
        "rejections": rejections,
    }
