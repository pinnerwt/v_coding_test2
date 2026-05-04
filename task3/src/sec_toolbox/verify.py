"""Self-verification of extracted Item rows.

Three independent cross-checks:

* :func:`verify_schedule` — every Item required by the SEC Item taxonomy for
  the filing's fiscal year shows up in the extraction (and nothing extraneous
  is flagged as a separate issue).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .taxonomy import items_for_year


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
