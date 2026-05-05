from . import clean_and_load

REGISTRY = {
    "clean_and_load": clean_and_load,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
