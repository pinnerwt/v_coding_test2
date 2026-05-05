from . import clean_and_load, find_anchors, slice_items

REGISTRY = {
    "clean_and_load": clean_and_load,
    "find_anchors": find_anchors,
    "slice_items": slice_items,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
