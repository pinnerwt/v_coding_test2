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


def _extract_groups(m: re.Match, use_named: bool) -> tuple[str, str, str]:
    """Return (num, letter, title) from a match.

    `use_named=True` reads the `item_number`/`item_letter`/`item_title` named
    groups; otherwise reads positional groups 1/2/3 (canonical ITEM_RE shape).
    """
    if use_named:
        gd = m.groupdict()
        num = gd.get("item_number") or ""
        letter = (gd.get("item_letter") or "").upper()
        title = (gd.get("item_title") or "").strip()
        return num, letter, title
    num = m.group(1)
    letter = (m.group(2) or "").upper()
    title = (m.group(3) or "").strip()
    return num, letter, title


def find_anchors(text: str, *, regex: re.Pattern | None = None) -> list[dict]:
    pat = regex or ITEM_RE
    use_named = "item_number" in pat.groupindex
    if not use_named and pat.groups < 1:
        raise ValueError(
            "regex must expose `item_number` as a named group (preferred), or "
            "use the canonical ITEM_RE positional-group contract: "
            "group(1)=item_number, group(2)=item_letter, group(3)=item_title."
        )
    out = []
    for m in pat.finditer(text):
        try:
            num, letter, title = _extract_groups(m, use_named)
        except IndexError as exc:
            raise ValueError(
                "regex group contract violated: expected named group "
                "`item_number` (preferred) or numbered groups 1/2/3 matching "
                "ITEM_RE's (item_number)(item_letter)(item_title) layout. "
                f"Underlying error: {exc}"
            ) from exc
        item_id = num + letter
        if item_id not in ITEM_TO_PART:
            continue
        out.append(
            {
                "item_number": item_id,
                "title": title,
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
