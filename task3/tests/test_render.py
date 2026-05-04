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
