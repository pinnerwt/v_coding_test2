"""Per-slice status classification.

A 10-K Item slice can be in one of four states:

* ``extracted`` — substantive disclosure text lives in the slice.
* ``incorporated_by_reference`` — the slice tells the reader to look elsewhere
  (proxy statement, exhibit, etc.) and contains no disclosure of its own.
* ``not_applicable`` — the registrant declares the Item does not apply.
* ``reserved`` — the SEC Item is officially a placeholder (post-2020 Item 6).

Long slices are presumed substantive and short-circuit to ``extracted``
without an LLM call. Short slices are read by the LLM (wired in a later task)
to decide between the four labels.
"""

from __future__ import annotations

from dataclasses import dataclass

from .segment import Slice

_LONG_SLICE_TOKEN_THRESHOLD = 400


@dataclass(frozen=True)
class StatusResult:
    status: str


def _token_count(text: str) -> int:
    return len(text.split())


def _llm_read(text: str) -> str:
    """Placeholder LLM reader. Wired up in Task C5; stub returns ``substantive``
    so short slices default to ``extracted`` until the real client is in place.
    Tests monkeypatch this symbol to control the response.
    """
    return "substantive"


_LABEL_TO_STATUS = {
    "substantive": "extracted",
    "incorporated_by_reference": "incorporated_by_reference",
    "not_applicable": "not_applicable",
    "reserved": "reserved",
}


def classify(slice_: Slice) -> StatusResult:
    """Decide the slice's status. Long slices skip the LLM."""
    if _token_count(slice_.content_text) >= _LONG_SLICE_TOKEN_THRESHOLD:
        return StatusResult(status="extracted")
    label = _llm_read(slice_.content_text)
    return StatusResult(status=_LABEL_TO_STATUS.get(label, "extracted"))
