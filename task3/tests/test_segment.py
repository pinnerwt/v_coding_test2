import pathlib

import pytest

from sec_toolbox.render import render_html
from sec_toolbox.segment import resolve_entries_to_body, segment
from sec_toolbox.toc import find_toc_region

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"
IBM = FIXTURES / "51143/000155837020001334/ibm-20191231x10k2af531.htm"


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


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_resolves_ibm_item1_lands_on_prominent_chunk():
    html = IBM.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    body_locs = resolve_entries_to_body(rendered, region, html)
    item1 = next(b for b in body_locs if "Business" in b.entry.text)
    assert item1.body_text_start > region.text_end
    chunk = rendered.chunk_at(item1.body_text_start)
    assert chunk is not None
    assert chunk.layout.is_bold or chunk.layout.font_size_pt > rendered.median_font_size


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_prominence_fallback_finds_target_when_anchor_missing():
    """Entries without targets should still resolve via title+prominence search."""
    from dataclasses import replace

    from sec_toolbox.toc import TOCEntry

    html = APPLE.read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered, html)
    # Strip targets from all entries to force the fallback path.
    stripped = replace(
        region,
        entries=[
            TOCEntry(
                text=e.text,
                target=None,
                source_start=e.source_start,
                text_start=e.text_start,
            )
            for e in region.entries
        ],
    )
    body_locs = resolve_entries_to_body(rendered, stripped, html)
    item1 = next((b for b in body_locs if "Business" in b.entry.text), None)
    assert item1 is not None, "prominence fallback should still find Item 1 Business"
    assert item1.body_text_start > region.text_end
    chunk = rendered.chunk_at(item1.body_text_start)
    assert chunk is not None
    assert chunk.layout.is_bold or chunk.layout.font_size_pt > rendered.median_font_size


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_segment_apple_produces_slices():
    html = APPLE.read_bytes()
    slices = segment(html)
    titles = [s.item_title for s in slices]
    assert "Business" in titles
    assert "Risk Factors" in titles
    for s in slices:
        assert s.content_text
        assert s.char_range[1] > s.char_range[0]
    for a, b in zip(slices, slices[1:], strict=False):
        assert a.char_range[1] <= b.char_range[0]


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_segment_apple_covers_expected_items():
    html = APPLE.read_bytes()
    slices = segment(html)
    titles = [s.item_title for s in slices]
    for expected in ["Business", "Risk Factors", "Cybersecurity", "Properties"]:
        assert expected in titles, f"missing slice for {expected}"


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_segment_ibm_produces_slices():
    html = IBM.read_bytes()
    slices = segment(html)
    titles = [s.item_title for s in slices]
    assert "Business" in titles
    assert "Risk Factors" in titles
    for a, b in zip(slices, slices[1:], strict=False):
        assert a.char_range[1] <= b.char_range[0]
