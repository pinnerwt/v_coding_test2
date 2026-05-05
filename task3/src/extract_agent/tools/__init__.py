from . import clean_and_load, find_anchors

REGISTRY = {
    "clean_and_load": clean_and_load,
    "find_anchors": find_anchors,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
