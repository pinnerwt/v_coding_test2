"""Tests for the LLM-driven TOC fallback."""

import json
import os
import pathlib
from unittest.mock import MagicMock

import pytest

from sec_toolbox.render import render_html

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
GE_2018 = FIXTURES / "40545/000004054519000014/ge10-k2018.htm"


def _mock_client(payload: dict) -> MagicMock:
    """LLMClient stub whose chat() returns the JSON-encoded payload."""
    client = MagicMock()
    client.chat.return_value = json.dumps(payload)
    return client


def test_propose_toc_returns_none_without_client():
    """Smoke: callable exists, returns None when given no client and the
    default LLMClient can't be constructed (no API key in test env)."""
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>nothing here</body></html>"
    rendered = render_html(html)
    # Don't pass a client; with no API key in test env, it should return None
    # rather than raising.
    assert propose_toc(rendered, html, client=None) is None


def test_prompt_file_exists():
    from sec_toolbox.toc_llm import _load_prompt

    text = _load_prompt()
    assert "toc_end_marker" in text
    assert "item_number" in text
    assert "anchor" in text
    assert "heading_snippet" in text


@pytest.mark.skipif(not GE_2018.exists(), reason="GE 2018 fixture not cached")
def test_propose_toc_happy_path_anchor_resolution():
    """When the LLM returns a payload with valid heading snippets,
    propose_toc resolves each one via substring search past the marker
    and returns a TOCRegion whose entries point at body locations.

    GE 2018's master TOC sits at the *end* of the document, so the front
    50K window doesn't actually contain the TOC — but the mocked payload
    just needs marker + snippets that exist *somewhere* in the rendered
    text past the marker for resolution to succeed.
    """
    from sec_toolbox.toc_llm import propose_toc

    html = GE_2018.read_bytes()
    rendered = render_html(html)

    payload = {
        # "FORM 10-K" appears in GE's front matter; body_start lands very early
        # so all the body snippets below resolve past it.
        "toc_end_marker": "FORM 10-K",
        "items": [
            {"item_number": "1A", "anchor": None, "heading_snippet": "Risk Factors"},
            {"item_number": "3", "anchor": None, "heading_snippet": "Legal Proceedings"},
            {
                "item_number": "7",
                "anchor": None,
                "heading_snippet": "Management's Discussion and Analysis",
            },
        ],
    }
    client = _mock_client(payload)

    region = propose_toc(rendered, html, client=client)
    assert region is not None
    assert len(region.entries) >= 2
    for e in region.entries:
        assert e.text_start > region.text_start


def test_propose_toc_returns_none_on_malformed_json():
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>x</body></html>"
    rendered = render_html(html)
    client = MagicMock()
    client.chat.return_value = "this is not json {{"

    assert propose_toc(rendered, html, client=client) is None


def test_propose_toc_returns_none_when_marker_not_found():
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>some text without the marker</body></html>"
    rendered = render_html(html)
    client = _mock_client(
        {
            "toc_end_marker": "NOT IN THE TEXT",
            "items": [{"item_number": "1", "heading_snippet": "x"}],
        }
    )

    assert propose_toc(rendered, html, client=client) is None


def test_propose_toc_returns_none_when_zero_items_resolve():
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>BODY START here is some content</body></html>"
    rendered = render_html(html)
    client = _mock_client(
        {
            "toc_end_marker": "BODY START",
            "items": [
                {
                    "item_number": "1",
                    "anchor": "#nonexistent",
                    "heading_snippet": "this snippet is not in the text",
                }
            ],
        }
    )

    assert propose_toc(rendered, html, client=client) is None


def test_propose_toc_returns_none_on_client_exception():
    from sec_toolbox.toc_llm import propose_toc

    html = b"<html><body>x</body></html>"
    rendered = render_html(html)
    client = MagicMock()
    client.chat.side_effect = RuntimeError("network down")

    assert propose_toc(rendered, html, client=client) is None


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1" or not GE_2018.exists(),
    reason="live LLM test gated on RUN_LIVE_LLM=1 and GE 2018 fixture",
)
def test_propose_toc_live_ge_2018():
    """End-to-end with the real LLM. Does not run in CI."""
    from sec_toolbox.toc_llm import propose_toc

    html = GE_2018.read_bytes()
    rendered = render_html(html)
    region = propose_toc(rendered, html)
    assert region is not None, "live LLM should produce a region for GE 2018"
    assert len(region.entries) >= 10
