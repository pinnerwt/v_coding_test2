import re


def classify_default(title: str, body: str) -> str:
    norm_title = title.strip().lower()
    head = body[:1500]
    head_short = body[:800]
    short = len(body) < 1500
    short_ibr = len(body) < 4000

    body_stripped = body.strip().lower().rstrip(".")
    if body_stripped in {"none", "n/a"}:
        return "not_applicable"

    if short_ibr and re.search(
        r"incorporat\w*(?:\s+\w+){0,5}\s+by\s+reference",
        head_short,
        re.IGNORECASE,
    ):
        return "incorporated_by_reference"

    if norm_title in {"[reserved]", "reserved"}:
        return "reserved"
    if short and re.search(r"\breserved\b", head[:200], re.IGNORECASE):
        return "reserved"

    if short and re.search(r"\bnot\s+applicable\b", head[:200], re.IGNORECASE):
        return "not_applicable"

    return "extracted"
