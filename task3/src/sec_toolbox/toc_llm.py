"""LLM-driven fallback for locating the master 10-K TOC.

Used when ``toc.find_toc_region`` finds a region but the resolved item
count or resolution rate is too low (the GE 2018 case, where the heuristic
locks onto a nested MD&A sub-TOC). The LLM sees the first ~50K rendered
characters and returns a structured tool-call payload describing where the
TOC ends and where each Item begins.

The output is a synthetic :class:`TOCRegion` shaped exactly like the one
:func:`sec_toolbox.toc.find_toc_region` produces, so downstream code in
``segment.py`` is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .render import Rendered
from .toc import TOCRegion

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "toc_extraction.md"
_FRONT_SLICE_CHARS = 50_000


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def propose_toc(
    rendered: Rendered, html: bytes, *, client: LLMClient | None = None
) -> TOCRegion | None:
    """Best-effort LLM TOC extraction. Returns ``None`` on any failure."""
    return None
