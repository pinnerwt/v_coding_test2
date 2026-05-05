from . import clean_and_load, find_anchors, slice_items, validate_records, write_output

REGISTRY = {
    "clean_and_load": clean_and_load,
    "find_anchors": find_anchors,
    "slice_items": slice_items,
    "validate_records": validate_records,
    "write_output": write_output,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
