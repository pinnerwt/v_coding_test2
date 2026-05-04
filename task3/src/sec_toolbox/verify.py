"""Self-verification of extracted Item rows.

Three independent cross-checks:

* :func:`verify_schedule` — every Item required by the SEC Item taxonomy for
  the filing's fiscal year shows up in the extraction (and nothing extraneous
  is flagged as a separate issue).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .render import render_html
from .taxonomy import items_for_year

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip().lower()


@dataclass(frozen=True)
class Issue:
    item_number: str | None
    message: str


def verify_schedule(items: list[dict[str, Any]], fiscal_year: int) -> list[Issue]:
    """Flag any Item required by the year's schedule that is absent from
    ``items``. ``items`` is the list of dicts produced by :func:`extract`.
    """
    expected = {i.item_number for i in items_for_year(fiscal_year)}
    present = {row.get("item_number") for row in items if row.get("item_number")}
    missing = sorted(expected - present, key=_item_sort_key)
    return [Issue(item_number=n, message=f"missing Item {n}") for n in missing]


def _item_sort_key(num: str) -> tuple[int, str]:
    """Sort '1','1A','1B','2','7A','9C','10' in human order."""
    head = "".join(ch for ch in num if ch.isdigit())
    tail = num[len(head) :]
    return (int(head) if head else 0, tail)


_ROUNDTRIP_PROBE_LEN = 200


def verify_char_ranges(items: list[dict[str, Any]], html: bytes) -> list[Issue]:
    """For each row with status ``extracted``, re-render the bytes within
    ``char_range`` and confirm the slice's ``content_text`` lines up with the
    re-rendered text. Roundtrip succeeds when a ~200-char prefix of either
    side is contained in the other (after whitespace normalization).
    """
    issues: list[Issue] = []
    for row in items:
        if row.get("status") != "extracted":
            continue
        char_range = row.get("char_range")
        if not char_range or len(char_range) != 2:
            continue
        start, end = char_range
        if not (0 <= start < end <= len(html)):
            issues.append(
                Issue(
                    item_number=row.get("item_number"),
                    message=f"char_range out of bounds: {char_range}",
                )
            )
            continue
        re_rendered = _normalize(render_html(html[start:end]).text)
        content = _normalize(row.get("content_text", ""))
        if not content:
            continue
        probe_a = content[:_ROUNDTRIP_PROBE_LEN]
        probe_b = re_rendered[:_ROUNDTRIP_PROBE_LEN]
        if probe_a and probe_a in re_rendered:
            continue
        if probe_b and probe_b in content:
            continue
        issues.append(
            Issue(
                item_number=row.get("item_number"),
                message="char_range roundtrip mismatch",
            )
        )
    return issues


def _all_fact_ends(facts: dict[str, Any]) -> set[str]:
    """Collect every ``end`` date appearing under any us-gaap or dei concept."""
    ends: set[str] = set()
    taxonomies = facts.get("facts", {})
    for concepts in taxonomies.values():
        for concept in concepts.values():
            for unit_rows in concept.get("units", {}).values():
                for row in unit_rows:
                    end = row.get("end")
                    if end:
                        ends.add(end)
    return ends


def verify_xbrl_metadata(extracted_meta: dict[str, Any], facts: dict[str, Any]) -> list[Issue]:
    """Cross-check filing metadata against the issuer's XBRL Company Facts.

    Confirms (a) the CIK in the extraction matches the CIK on the facts blob,
    and (b) the filing's fiscal-year end is observed as a reporting period in
    the facts (any ``end`` date under any concept is a hit).
    """
    issues: list[Issue] = []
    extracted_cik = str(extracted_meta.get("cik", "")).lstrip("0") or "0"
    facts_cik = str(facts.get("cik", "")).lstrip("0") or "0"
    if extracted_cik != facts_cik:
        issues.append(
            Issue(
                item_number=None,
                message=f"cik mismatch: extracted={extracted_cik!r} facts={facts_cik!r}",
            )
        )
    period_end = extracted_meta.get("period_end")
    if period_end and period_end not in _all_fact_ends(facts):
        issues.append(
            Issue(
                item_number=None,
                message=f"period_end {period_end!r} not present in XBRL facts",
            )
        )
    return issues
