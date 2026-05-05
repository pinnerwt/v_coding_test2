from . import clean_and_load, find_anchors, slice_items, validate_records

REGISTRY = {
    "clean_and_load": clean_and_load,
    "find_anchors": find_anchors,
    "slice_items": slice_items,
    "validate_records": validate_records,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
