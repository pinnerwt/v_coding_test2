import re

ITEM_RE = re.compile(
    r"^\s*ITEM\s+(1[0-6]|[1-9])([A-C])?\s*[\.\:]\s*(.*?)$",
    re.IGNORECASE | re.MULTILINE,
)

ITEM_TO_PART = {
    "1": "I",
    "1A": "I",
    "1B": "I",
    "1C": "I",
    "2": "I",
    "3": "I",
    "4": "I",
    "5": "II",
    "6": "II",
    "7": "II",
    "7A": "II",
    "8": "II",
    "9": "II",
    "9A": "II",
    "9B": "II",
    "9C": "II",
    "10": "III",
    "11": "III",
    "12": "III",
    "13": "III",
    "14": "III",
    "15": "IV",
    "16": "IV",
}


def find_anchors(text: str, *, regex: re.Pattern | None = None) -> list[dict]:
    pat = regex or ITEM_RE
    out = []
    for m in pat.finditer(text):
        num = m.group(1)
        letter = (m.group(2) or "").upper()
        item_id = num + letter
        if item_id not in ITEM_TO_PART:
            continue
        out.append(
            {
                "item_number": item_id,
                "title": (m.group(3) or "").strip(),
                "match_start": m.start(),
                "match_end": m.end(),
            }
        )
    return out


def dedupe_anchors(anchors: list[dict], *, toc_region_end: int = 8000) -> list[dict]:
    """For each item_number: if any anchor has match_start > toc_region_end,
    drop all anchors with match_start <= toc_region_end for that item.
    Keep all surviving anchors (slicing decides longest later)."""
    by_num: dict[str, list[dict]] = {}
    for a in anchors:
        by_num.setdefault(a["item_number"], []).append(a)
    out: list[dict] = []
    for _num, group in by_num.items():
        has_body = any(a["match_start"] > toc_region_end for a in group)
        pool = [a for a in group if a["match_start"] > toc_region_end] if has_body else group
        out.extend(pool)
    out.sort(key=lambda a: a["match_start"])
    return out
