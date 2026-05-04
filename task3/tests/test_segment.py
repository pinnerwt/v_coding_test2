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


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_apple_slices_have_canonical_item_numbers():
    html = APPLE.read_bytes()
    slices = segment(html, fiscal_year=2023)
    by_num = {s.item_number: s for s in slices if s.item_number}
    assert by_num["1"].canonical_title == "Business"
    assert by_num["1A"].canonical_title == "Risk Factors"
    assert by_num["1C"].canonical_title.startswith("Cybersecurity")
    assert by_num["1"].part == "I"
    assert by_num["7"].part == "II"
    assert by_num["10"].part == "III"
    assert by_num["15"].part == "IV"


@pytest.mark.skipif(not IBM.exists(), reason="IBM fixture not cached")
def test_ibm_slices_have_canonical_item_numbers():
    html = IBM.read_bytes()
    slices = segment(html, fiscal_year=2019)
    by_num = {s.item_number: s for s in slices if s.item_number}
    # IBM 2019 has Item 6 = Selected Financial Data (pre-2021)
    assert "6" in by_num
    assert "Selected Financial Data" in by_num["6"].canonical_title
    # 1C didn't exist in 2019
    assert "1C" not in by_num


def test_is_item_entry_recognized_via_anchor_target():
    """Modern filings (MSFT 2023, BRKA 2025) put 'Item 1' and 'Business' in
    sibling table cells, so the TOC entry text is just 'Business' but the
    href target is '#item_1_business'. We must still recognize this as an
    Item entry and recover the item number from the target.
    """
    from sec_toolbox.segment import _extract_item_number_from_target, _is_item_entry
    from sec_toolbox.toc import TOCEntry

    e = TOCEntry(text="Business", target="#item_1_business", source_start=0, text_start=0)
    assert _is_item_entry(e) is True
    assert _extract_item_number_from_target(e.target) == "1"

    e2 = TOCEntry(text="Risk Factors", target="#ITEM_1A_RISK_FACTORS", source_start=0, text_start=0)
    assert _is_item_entry(e2) is True
    assert _extract_item_number_from_target(e2.target) == "1A"

    # Already-prefixed text path still works.
    e3 = TOCEntry(text="Item 7. MD&A", target=None, source_start=0, text_start=0)
    assert _is_item_entry(e3) is True

    # Non-item targets are still rejected.
    e4 = TOCEntry(text="Glossary", target="#glossary", source_start=0, text_start=0)
    assert _is_item_entry(e4) is False


MSFT_2023 = FIXTURES / "789019/000095017023035122/msft-20230630.htm"
BRKA_2025 = FIXTURES / "1067983/000119312526083899/brka-20251231.htm"


@pytest.mark.skipif(not MSFT_2023.exists(), reason="MSFT 2023 fixture not cached")
def test_msft_2023_extracts_full_item_set():
    html = MSFT_2023.read_bytes()
    slices = segment(html, fiscal_year=2023)
    item_numbers = {s.item_number for s in slices if s.item_number}
    assert {"1", "1A", "7", "8"} <= item_numbers
    assert len(slices) >= 20


@pytest.mark.skipif(not BRKA_2025.exists(), reason="BRKA 2025 fixture not cached")
def test_brka_2025_extracts_full_item_set():
    html = BRKA_2025.read_bytes()
    slices = segment(html, fiscal_year=2025)
    item_numbers = {s.item_number for s in slices if s.item_number}
    assert {"1", "1A", "7", "8"} <= item_numbers
    # BRKA incorporates Part III (Items 10-14) by reference from its proxy.
    assert len(slices) >= 17


GE_2018 = FIXTURES / "40545/000004054519000014/ge10-k2018.htm"


@pytest.mark.skipif(not GE_2018.exists(), reason="GE 2018 fixture not cached")
def test_segment_falls_back_to_llm_when_heuristic_under_resolves(monkeypatch):
    """GE 2018: heuristic locks onto a nested MD&A sub-TOC. The confidence
    gate should detect under-resolution (< 10 items) and call propose_toc
    instead. We mock propose_toc to return a region with several known
    item-shaped entries; the segmenter must use that region."""
    import sec_toolbox.segment as seg
    from sec_toolbox.render import render_html
    from sec_toolbox.toc import TOCEntry, TOCRegion

    html = GE_2018.read_bytes()
    rendered = render_html(html)

    fake_entries: list[TOCEntry] = []
    for n in ("1", "1A", "2", "3", "7", "7A", "8", "15"):
        needle = f"Item {n}."
        idx = rendered.text.find(needle, 5000)
        if idx > 0:
            src = rendered.source_offset[idx] if idx < len(rendered.source_offset) else 0
            fake_entries.append(
                TOCEntry(text=f"Item {n}. ...", target=None, source_start=src, text_start=idx)
            )
    fake_region = TOCRegion(
        text_start=0,
        text_end=fake_entries[0].text_start - 1 if fake_entries else 0,
        chunk_start=0,
        chunk_end=0,
        entries=fake_entries,
    )

    monkeypatch.setattr(seg, "propose_toc", lambda r, h, client=None: fake_region)

    slices = seg.segment(html, fiscal_year=2018)
    item_numbers = {s.item_number for s in slices if s.item_number}
    # The fallback should pull at least Item 1, 7, 8 into the result.
    assert {"1", "7", "8"} <= item_numbers
    assert len(slices) >= 5
