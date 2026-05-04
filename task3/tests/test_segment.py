import pathlib

import pytest

from sec_toolbox.render import render_html
from sec_toolbox.segment import resolve_entries_to_body
from sec_toolbox.toc import find_toc_region

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_resolves_apple_anchors_to_body():
    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    body_locs = resolve_entries_to_body(rendered, region, html)
    # All Apple TOC entries have anchors and should resolve.
    assert len(body_locs) >= 16
    # Item 1 (Business) body location is past the TOC and well into the doc
    item1 = next(b for b in body_locs if "Business" in b.entry.text)
    assert item1.body_text_start > region.text_end


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_apple_body_locations_ordered():
    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    body_locs = resolve_entries_to_body(rendered, region, html)
    item_locs = [b for b in body_locs if b.entry.text.lower().startswith("item")]
    starts = [b.body_text_start for b in item_locs]
    assert starts == sorted(starts), "Item body locations should be in document order"
