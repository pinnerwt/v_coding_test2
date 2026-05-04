from sec_toolbox.render import render_html


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
