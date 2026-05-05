import re

from .anchors import ITEM_TO_PART

TRAILING_PAGENUM_RE = re.compile(r"\n+\s*\d{1,4}\s*$")


def _slice_body(text: str, heading_match_end: int, next_start: int) -> tuple[int, int]:
    nl = text.find("\n", heading_match_end)
    body_start = heading_match_end if (nl == -1 or nl > next_start) else nl + 1
    return body_start, next_start


def slice_items(text: str, anchors: list[dict]) -> list[dict]:
    item_starts = sorted(a["match_start"] for a in anchors)
    by_item: dict[str, list[dict]] = {}
    for a in anchors:
        by_item.setdefault(a["item_number"], []).append(a)

    chosen: list[dict] = []
    for item_id, alist in by_item.items():
        scored = []
        for a in alist:
            idx = item_starts.index(a["match_start"])
            next_start = item_starts[idx + 1] if idx + 1 < len(item_starts) else len(text)
            body_start, body_end = _slice_body(text, a["match_end"], next_start)
            scored.append((body_end - body_start, a, body_start, body_end))
        scored.sort(key=lambda t: t[0], reverse=True)
        _, a, body_start, body_end = scored[0]

        title = a["title"]
        if not title:
            tail = text[body_start : body_start + 200]
            for line in tail.split("\n"):
                line = line.strip()
                if line:
                    title = line
                    break

        body = text[body_start:body_end]
        if len(body) < 500:
            body = TRAILING_PAGENUM_RE.sub("", body).rstrip()
            body_end = body_start + len(body)

        chosen.append(
            {
                "part": ITEM_TO_PART[item_id],
                "item_number": item_id,
                "item_title": title,
                "content_text": body,
                "char_range": [body_start, body_end],
            }
        )
    chosen.sort(key=lambda r: r["char_range"][0])
    return chosen
