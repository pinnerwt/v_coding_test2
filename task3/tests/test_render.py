import pathlib

import pytest

from sec_toolbox.render import render_html

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"

APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"
IBM = FIXTURES / "51143/000155837020001334/ibm-20191231x10k2af531.htm"
EXXON = FIXTURES / "34088/000003408826000045/xom-20251231.htm"


def test_render_returns_text_and_offset_map():
    html = b"<html><body><p>Hello <b>world</b>.</p></body></html>"
    rendered = render_html(html)
    assert "Hello world." in rendered.text
    i = rendered.text.index("world")
    assert html[rendered.source_offset[i] :].startswith(b"world")


def test_render_preserves_text_chunks():
    html = b"<html><body><h1>Title</h1><p>Body</p></body></html>"
    rendered = render_html(html)
    chunks = rendered.chunks
    assert any(c.text.strip() == "Title" for c in chunks)
    assert any(c.text.strip() == "Body" for c in chunks)


def test_render_extracts_layout_features():
    html = b"""<html><body>
      <p style="font-weight:700;text-align:center;font-size:14pt">ITEM 1. BUSINESS</p>
      <p style="font-size:10pt">Apple Inc. designs ...</p>
    </body></html>"""
    rendered = render_html(html)
    heading = next(c for c in rendered.chunks if "ITEM 1" in c.text)
    assert heading.layout.is_bold
    assert heading.layout.is_centered
    assert heading.layout.font_size_pt > 12
    body = next(c for c in rendered.chunks if "Apple" in c.text)
    assert not body.layout.is_bold


def test_render_layout_uses_b_and_i_tags():
    html = b"<html><body><p><b>Bold heading</b></p><p><i>italic</i></p></body></html>"
    rendered = render_html(html)
    bold = next(c for c in rendered.chunks if "Bold" in c.text)
    italic = next(c for c in rendered.chunks if "italic" in c.text)
    assert bold.layout.is_bold
    assert italic.layout.is_italic


def test_render_layout_capitalization_ratio():
    html = b"<html><body><p>ALL CAPS HEADING</p><p>mixed case body text</p></body></html>"
    rendered = render_html(html)
    caps = next(c for c in rendered.chunks if "ALL" in c.text)
    body = next(c for c in rendered.chunks if "mixed" in c.text)
    assert caps.layout.capitalization_ratio > 0.9
    assert body.layout.capitalization_ratio < 0.1


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_render_apple_fixture():
    html = APPLE.read_bytes()
    r = render_html(html)
    assert len(r.text) > 100_000
    assert any("Item 1." in c.text and "Business" in c.text for c in r.chunks)


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_render_ibm_fixture():
    html = IBM.read_bytes()
    r = render_html(html)
    assert len(r.text) > 100_000
    # IBM 2019 uses "ITEM 1." in caps
    assert any("ITEM 1" in c.text.upper() and "BUSINESS" in c.text.upper() for c in r.chunks)


@pytest.mark.skipif(not EXXON.exists(), reason="Exxon fixture not cached")
def test_render_exxon_fixture():
    html = EXXON.read_bytes()
    r = render_html(html)
    assert len(r.text) > 100_000
    # Exxon's inline-XBRL TOC puts each label in its own table cell, so
    # "Item 1." and "Business" land in adjacent chunks rather than merged.
    item_chunks = [i for i, c in enumerate(r.chunks) if c.text.strip().startswith("Item 1.")]
    assert item_chunks, "expected an 'Item 1.' chunk"
    follow = r.chunks[item_chunks[0] + 1].text
    assert "Business" in follow
