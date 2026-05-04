"""Tests for the LLM-driven TOC fallback."""

import pathlib

import pytest

from sec_toolbox.render import render_html

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
GE_2018 = FIXTURES / "40545/000004054519000014/ge10-k2018.htm"


def test_propose_toc_returns_none_without_client():
    """Smoke: callable exists, returns None when given no client and the
    default LLMClient can't be constructed (no API key in test env)."""
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>nothing here</body></html>"
    rendered = render_html(html)
    # Don't pass a client; with no API key in test env, it should return None
    # rather than raising.
    assert propose_toc(rendered, html, client=None) is None
