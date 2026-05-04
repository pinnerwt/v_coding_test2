"""SEC 10-K Item schedule, year-aware.

The set of Items required by Form 10-K has shifted over the years. Two changes
matter for the survey set:

* **Item 6** ("Selected Financial Data") was eliminated by SEC in 2021; for FY
  filings on or after 2021 it is a placeholder labelled ``[Reserved]``.
* **Item 1C** ("Cybersecurity") was added by the SEC's July-2023 final rule and
  is required for filings on or after fiscal years ending December 15, 2023.

We model both as a dated schedule and expose ``items_for_year(year)`` returning
the canonical list of Items expected on a 10-K filed for that fiscal year.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    part: str
    item_number: str
    canonical_title: str
    is_reserved_default: bool = False


# Core schedule. Each entry: (part, item_number, title pre-2021, title post-2020 or None)
# Where post-2020 differs (Item 6) we mark is_reserved_default in the returned list.
# 1C is conditionally added based on year.

_BASE = [
    ("I", "1", "Business"),
    ("I", "1A", "Risk Factors"),
    ("I", "1B", "Unresolved Staff Comments"),
    # 1C inserted later when applicable
    ("I", "2", "Properties"),
    ("I", "3", "Legal Proceedings"),
    ("I", "4", "Mine Safety Disclosures"),
    (
        "II",
        "5",
        "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",  # noqa: E501
    ),
    ("II", "6", "Selected Financial Data"),
    (
        "II",
        "7",
        "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    ),
    ("II", "7A", "Quantitative and Qualitative Disclosures About Market Risk"),
    ("II", "8", "Financial Statements and Supplementary Data"),
    (
        "II",
        "9",
        "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    ),
    ("II", "9A", "Controls and Procedures"),
    ("II", "9B", "Other Information"),
    # 9C added 2023 (HFCAA-related foreign jurisdictions disclosure)
    ("III", "10", "Directors, Executive Officers and Corporate Governance"),
    ("III", "11", "Executive Compensation"),
    (
        "III",
        "12",
        "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",  # noqa: E501
    ),
    ("III", "13", "Certain Relationships and Related Transactions, and Director Independence"),
    ("III", "14", "Principal Accountant Fees and Services"),
    ("IV", "15", "Exhibits, Financial Statement Schedules"),
    ("IV", "16", "Form 10-K Summary"),
]


def items_for_year(year: int) -> list[Item]:
    """Return the canonical Item schedule for a 10-K with the given fiscal year.

    ``year`` is the fiscal year covered by the filing (e.g. 2023 for Apple's
    FY23 10-K dated 2023-09-30). Item 1C is included from 2023 onward; Item 6
    is reported as reserved by default for 2021 onward.
    """
    items: list[Item] = []
    for part, num, title in _BASE:
        if num == "6" and year >= 2021:
            items.append(
                Item(
                    part=part,
                    item_number="6",
                    canonical_title="[Reserved]",
                    is_reserved_default=True,
                )
            )
        else:
            items.append(Item(part=part, item_number=num, canonical_title=title))
        if num == "1B" and year >= 2023:
            items.append(Item(part="I", item_number="1C", canonical_title="Cybersecurity"))
        if num == "9B" and year >= 2023:
            items.append(
                Item(
                    part="II",
                    item_number="9C",
                    canonical_title="Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",  # noqa: E501
                )
            )
    return items
