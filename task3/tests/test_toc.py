import pathlib

import pytest

from sec_toolbox.render import render_html
from sec_toolbox.toc import find_toc_region

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"
IBM = FIXTURES / "51143/000155837020001334/ibm-20191231x10k2af531.htm"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_finds_toc_in_apple():
    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    assert region is not None
    # TOC region is in the first quarter of the document
    assert region.text_end < len(rendered.text) // 4
    # Apple has Items 1, 1A, 1B, 1C, 2-16 plus 7A/9A/9B/9C — at least 16 entries
    assert len(region.entries) >= 16


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_finds_toc_in_ibm():
    html = IBM.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    assert region is not None
    assert len(region.entries) >= 14


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_parses_apple_toc_entries():
    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    titles = [e.text for e in region.entries]
    assert any("Business" in t for t in titles)
    assert any("Risk Factors" in t for t in titles)
    assert any("Cybersecurity" in t for t in titles)
    # Page-number-only chunks should not appear as standalone entries
    assert not any(t.strip().isdigit() for t in titles)


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_apple_toc_entries_have_targets():
    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    # Apple uses internal anchors throughout its TOC.
    targeted = [e for e in region.entries if e.target]
    assert len(targeted) >= 16
    # Each target is a fragment identifier
    assert all(e.target.startswith("#") for e in targeted)


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_parses_ibm_toc_entries():
    html = IBM.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    titles = [e.text for e in region.entries]
    # IBM 2019 merges "Item 1. Business" into one chunk; the entry text
    # contains the substantive title.
    assert any("Business" in t for t in titles)
    assert any("Risk Factors" in t for t in titles)
