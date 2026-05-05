from . import (
    classify_statuses,
    clean_and_load,
    done,
    find_anchors,
    inspect_record,
    read_chars,
    regex_search,
    slice_items,
    update_record,
    validate_records,
    write_output,
)

REGISTRY = {
    "clean_and_load": clean_and_load,
    "find_anchors": find_anchors,
    "slice_items": slice_items,
    "validate_records": validate_records,
    "write_output": write_output,
    "read_chars": read_chars,
    "regex_search": regex_search,
    "inspect_record": inspect_record,
    "update_record": update_record,
    "classify_statuses": classify_statuses,
    "done": done,
}


def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
