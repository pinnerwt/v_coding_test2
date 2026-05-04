"""Per-slice status classification.

A 10-K Item slice can be in one of four states:

* ``extracted`` — substantive disclosure text lives in the slice.
* ``incorporated_by_reference`` — the slice tells the reader to look elsewhere
  (proxy statement, exhibit, etc.) and contains no disclosure of its own.
* ``not_applicable`` — the registrant declares the Item does not apply.
* ``reserved`` — the SEC Item is officially a placeholder (post-2020 Item 6).

Long slices are presumed substantive and short-circuit to ``extracted``
without an LLM call. Short slices are read by the LLM, which returns one of
four labels. Calls are cached by content hash so repeat runs (eval, regression
tests) don't re-charge.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass

from .llm import LLMClient
from .segment import Slice

_LONG_SLICE_TOKEN_THRESHOLD = 400

_PROMPT_PATH = pathlib.Path(__file__).parent.parent.parent / "prompts" / "status_reader.md"

_VALID_LABELS = {"substantive", "incorporated_by_reference", "not_applicable", "reserved"}

_READ_CACHE: dict[str, str] = {}


@dataclass(frozen=True)
class StatusResult:
    status: str


def _token_count(text: str) -> int:
    return len(text.split())


def _build_client() -> LLMClient:
    return LLMClient()


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _parse_label(reply: str) -> str:
    """Extract a valid label from the model reply. Falls back to substantive."""
    cleaned = reply.strip().lower().strip("`'\".,")
    for label in _VALID_LABELS:
        if cleaned == label or cleaned.startswith(label):
            return label
    # Look for any label as a token in the reply
    for label in _VALID_LABELS:
        if label in cleaned:
            return label
    return "substantive"


def _llm_read(text: str) -> str:
    """Ask the LLM which of the four functional categories this slice is.

    Cached by SHA-256 of the slice text — identical content (e.g. boilerplate
    "None." across many filings) hits the cache.
    """
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if key in _READ_CACHE:
        return _READ_CACHE[key]
    template = _load_prompt()
    user_msg = template.replace("{slice_text}", text)
    client = _build_client()
    reply = client.chat([{"role": "user", "content": user_msg}])
    label = _parse_label(reply)
    _READ_CACHE[key] = label
    return label


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
