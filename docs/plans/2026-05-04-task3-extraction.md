# Task 3 — 10-K item-level extraction implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a pipeline that takes a fetched 10-K (CIK + accession) and produces structured per-Item JSON with `part`, `item_number`, `item_title`, `content_text`, `char_range`, `status`.

**Architecture:** Render → find TOC → resolve TOC entries to body locations by visual prominence → slice → classify status (long slices presumed `extracted`; short slices read by LLM). LLM is reader, not extractor; bulk text never leaves local. Self-verifies via SEC Item schedule + XBRL cross-check + char-range roundtrip.

**Tech stack:** Python 3.11, `uv`, `pytest`, `ruff`, `lxml` (HTML rendering), `httpx` (already vendored), OpenAI-compatible LLM client (DeepSeek default, env-var swappable). FastAPI for the Zeabur surface (last phase).

**Reference design:** `docs/plans/2026-05-04-task3-extraction-design.md`.

**Existing surface:** `task3/src/sec_toolbox/` already has `fetch`, `cache`, `throttle`, `endpoints`, `client`, `cli`, `survey`. We add: `render`, `toc`, `segment`, `taxonomy`, `status`, `llm`, `extract`, `verify`, `eval`.

**Eval fixtures (already cached on disk):**
- Apple FY23 — `data/raw/archive/320193/000032019323000106/aapl-20230930.htm` (1.56 MB, modern XBRL)
- IBM FY19 — `data/raw/archive/51143/000155837020001334/ibm-20191231x10k2af531.htm` (1.59 MB, older HTML)
- ExxonMobil FY25 — `data/raw/archive/34088/000003408826000045/xom-20251231.htm` (5.59 MB, heavy IBR)

---

## Phase A — Render pass

### Task A1: HTML → plain text + reverse offset map

**Files:**
- Create: `task3/src/sec_toolbox/render.py`
- Test: `task3/tests/test_render.py`

**Step 1: Write failing test**

```python
# tests/test_render.py
from sec_toolbox.render import render_html

def test_render_returns_text_and_offset_map():
    html = b"<html><body><p>Hello <b>world</b>.</p></body></html>"
    rendered = render_html(html)
    assert "Hello world." in rendered.text
    # rendered.text[i] originated at rendered.source_offset[i] in the input
    i = rendered.text.index("world")
    assert html[rendered.source_offset[i]:].startswith(b"world")

def test_render_preserves_text_chunks():
    html = b"<html><body><h1>Title</h1><p>Body</p></body></html>"
    rendered = render_html(html)
    chunks = rendered.chunks
    assert any(c.text.strip() == "Title" for c in chunks)
    assert any(c.text.strip() == "Body" for c in chunks)
```

**Step 2: Run test → fails** (`uv run pytest tests/test_render.py -v`).

**Step 3: Implement minimally**

Use `lxml.html` to parse, walk the tree producing a `Rendered` dataclass with:
- `text: str` — concatenated visible text
- `source_offset: list[int]` — for each character of `text`, the byte offset into the source HTML where it came from
- `chunks: list[Chunk]` — one `Chunk` per text node, with `text`, `text_start`, `text_end`, `source_start`, `source_end`, and a `LayoutFeatures` placeholder (filled in Task A2)

`lxml.html.fragment_fromstring(html, create_parent=True)` then iterate `.iter()`. Track byte offsets via `etree.tostring`'s `sourceline`/`text_position` is unreliable; instead, do a streaming parse with `lxml.etree.iterparse` and use `event.position`-style tracking. If that's brittle, fall back to a manual scanner: walk the bytes, recognise `<…>` tags, output text bytes between them, recording offsets.

**Step 4: Run tests → pass.**

**Step 5: Commit**

```bash
git add task3/src/sec_toolbox/render.py task3/tests/test_render.py
git commit -m "feat(task3): render HTML to text with reverse offset map"
```

### Task A2: Layout features per chunk

**Files:**
- Modify: `task3/src/sec_toolbox/render.py`
- Modify: `task3/tests/test_render.py`

**Step 1: Failing test**

```python
def test_render_extracts_layout_features():
    html = b'''<html><body>
      <p style="font-weight:700;text-align:center;font-size:14pt">ITEM 1. BUSINESS</p>
      <p style="font-size:10pt">Apple Inc. designs ...</p>
    </body></html>'''
    rendered = render_html(html)
    heading = next(c for c in rendered.chunks if "ITEM 1" in c.text)
    assert heading.layout.is_bold
    assert heading.layout.is_centered
    assert heading.layout.font_size_pt > 12
    body = next(c for c in rendered.chunks if "Apple" in c.text)
    assert not body.layout.is_bold
```

**Step 2-4:** Add `LayoutFeatures(font_size_pt: float, is_bold: bool, is_italic: bool, is_centered: bool, line_length: int, capitalization_ratio: float, surrounding_whitespace: int)`. Walk parents during render, parse inline `style=`, fold relevant CSS. Fall back to default body size 10pt when unknown so older filings still produce sane numbers.

**Step 5: Commit**

```bash
git commit -m "feat(task3): extract per-chunk layout features during render"
```

### Task A3: Render Apple/IBM/Exxon end-to-end

**Files:**
- Modify: `task3/tests/test_render.py`

**Step 1: Failing test**

```python
import pathlib
FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"

def test_render_apple_fixture():
    html = (FIXTURES / "320193/000032019323000106/aapl-20230930.htm").read_bytes()
    r = render_html(html)
    assert len(r.text) > 100_000
    # heading "Item 1. Business" should appear as a chunk
    assert any("Item 1." in c.text and "Business" in c.text for c in r.chunks)
```

Repeat for IBM and Exxon fixtures.

**Step 2-5:** Run, fix any rendering edge case (inline-XBRL `<ix:…>` tags, entity decoding, `&nbsp;`), commit.

```bash
git commit -m "test(task3): render covers Apple/IBM/Exxon fixtures"
```

---

## Phase B — TOC detection and segmentation

### Task B1: TOC region finder

**Files:**
- Create: `task3/src/sec_toolbox/toc.py`
- Test: `task3/tests/test_toc.py`

**Step 1: Failing test**

```python
from sec_toolbox.render import render_html
from sec_toolbox.toc import find_toc_region

def test_finds_toc_in_apple():
    html = (FIXTURES / "320193/000032019323000106/aapl-20230930.htm").read_bytes()
    rendered = render_html(html)
    region = find_toc_region(rendered)
    assert region is not None
    # TOC region is in the first quarter of the document
    assert region.text_end < len(rendered.text) // 4
    # Has at least 16 entries (Items 1-16, ignoring Part headers)
    assert len(region.entries) >= 16
```

**Step 2-4:** TOC region = a window with high density of short, similar-prominence chunks each followed by a numeric/anchor target. Detection by sliding-window over the first ~30% of the rendered text scoring (mean chunk length, prominence variance, fraction-of-chunks-followed-by-link-or-page-number). The region with peak score wins. No regex on the word "Item."

**Step 5: Commit.**

### Task B2: TOC entry parsing

**Files:**
- Modify: `task3/src/sec_toolbox/toc.py`
- Modify: `task3/tests/test_toc.py`

```python
def test_parses_apple_toc_entries():
    region = find_toc_region(rendered_apple)
    titles = [e.text for e in region.entries]
    assert any("Business" in t for t in titles)
    assert any("Risk Factors" in t for t in titles)
```

`Entry(text: str, target: str | None, source_start: int)` where `target` is a `#anchor`, page number string, or `None`.

```bash
git commit -m "feat(task3): parse TOC entries with optional targets"
```

### Task B3: Resolve TOC entry to body location — anchor path

**Files:**
- Create: `task3/src/sec_toolbox/segment.py`
- Test: `task3/tests/test_segment.py`

**Step 1: Failing test**

```python
from sec_toolbox.segment import resolve_entries_to_body

def test_resolves_apple_anchors_to_body():
    rendered = render_html(apple_html)
    region = find_toc_region(rendered)
    body_locs = resolve_entries_to_body(rendered, region)
    # Item 1 body location is past the TOC and well into the document
    item1 = next(b for b in body_locs if "Business" in b.entry.text)
    assert item1.body_text_start > region.text_end
```

**Step 2-4:** Look up each `#anchor` in the source HTML, map back to the rendered text offset, return `BodyLocation(entry, body_text_start, body_source_start)`.

```bash
git commit -m "feat(task3): resolve TOC anchors to body text locations"
```

### Task B4: Resolve by visual prominence (older HTML fallback)

**Files:**
- Modify: `task3/src/sec_toolbox/segment.py`
- Modify: `task3/tests/test_segment.py`

**Step 1: Failing test (IBM fixture)**

```python
def test_resolves_ibm_via_prominence():
    rendered = render_html(ibm_html)
    region = find_toc_region(rendered)
    body_locs = resolve_entries_to_body(rendered, region)
    item1 = next(b for b in body_locs if "Business" in b.entry.text)
    # IBM 2019 body Item 1 heading is past the TOC
    assert item1.body_text_start > region.text_end
    # And it points at a chunk with above-baseline prominence
    chunk = rendered.chunk_at(item1.body_text_start)
    assert chunk.layout.is_bold or chunk.layout.font_size_pt > rendered.median_font_size
```

**Step 2-4:** When an entry has no anchor (or anchor lookup fails), search the rendered text *after* the TOC region for chunks whose text matches the entry's title (fuzzy: lowercase + collapse whitespace + edit distance ≤ 2) AND whose layout prominence is in the top quartile of the document. Return the first match.

```bash
git commit -m "feat(task3): resolve via visual prominence for older HTML"
```

### Task B5: Slice extraction

**Files:**
- Modify: `task3/src/sec_toolbox/segment.py`
- Modify: `task3/tests/test_segment.py`

**Step 1: Failing test**

```python
from sec_toolbox.segment import segment

def test_segment_apple_produces_slices():
    slices = segment(apple_html)
    titles = [s.item_title for s in slices]
    assert "Business" in titles
    assert "Risk Factors" in titles
    # Each slice has non-empty text and source byte ranges
    for s in slices:
        assert s.content_text
        assert s.char_range[1] > s.char_range[0]
    # Slices are non-overlapping and ordered
    for a, b in zip(slices, slices[1:]):
        assert a.char_range[1] <= b.char_range[0]
```

**Step 2-4:** `segment(html_bytes) -> list[Slice]`. Pipeline: render → find TOC → resolve → take rendered text between consecutive body locations as `content_text`, map back through `source_offset` to get `char_range` in source bytes. `Slice` is the in-progress shape (no status yet).

```bash
git commit -m "feat(task3): slice 10-K into Item segments"
```

---

## Phase C — Item identification and status

### Task C1: SEC Item taxonomy

**Files:**
- Create: `task3/src/sec_toolbox/taxonomy.py`
- Test: `task3/tests/test_taxonomy.py`

**Step 1: Failing test**

```python
from sec_toolbox.taxonomy import items_for_year

def test_2023_includes_item_1c():
    items = items_for_year(2023)
    nums = [i.item_number for i in items]
    assert "1C" in nums  # Cybersecurity, added 2023

def test_2019_excludes_item_1c():
    items = items_for_year(2019)
    nums = [i.item_number for i in items]
    assert "1C" not in nums

def test_2022_item_6_reserved():
    item6 = next(i for i in items_for_year(2022) if i.item_number == "6")
    assert item6.is_reserved_default

def test_2018_item_6_selected_financial_data():
    item6 = next(i for i in items_for_year(2018) if i.item_number == "6")
    assert "Selected Financial Data" in item6.canonical_title
    assert not item6.is_reserved_default
```

**Step 2-4:** Static dict keyed by year-ranges. Each `Item(part, item_number, canonical_title, is_reserved_default)`.

```bash
git commit -m "feat(task3): SEC Item taxonomy with year-aware schedule"
```

### Task C2: Map TOC entries to canonical Items

**Files:**
- Modify: `task3/src/sec_toolbox/segment.py`
- Modify: `task3/tests/test_segment.py`

**Step 1: Failing test**

```python
def test_apple_slices_have_canonical_item_numbers():
    slices = segment(apple_html, fiscal_year=2023)
    by_num = {s.item_number: s for s in slices}
    assert by_num["1"].canonical_title == "Business"
    assert by_num["1A"].canonical_title == "Risk Factors"
    assert by_num["1C"].canonical_title.startswith("Cybersecurity")
```

**Step 2-4:** For each resolved entry, fuzzy-match its text against the taxonomy for the filing's year. If match found, attach `(part, item_number, canonical_title)`. Otherwise mark as `unknown` and let verification flag it.

```bash
git commit -m "feat(task3): map TOC entries to canonical Items"
```

### Task C3: Long-slice classifier (no LLM)

**Files:**
- Create: `task3/src/sec_toolbox/status.py`
- Test: `task3/tests/test_status.py`

**Step 1: Failing test**

```python
from sec_toolbox.status import classify

def test_long_slice_is_extracted_without_llm(monkeypatch):
    long_slice = make_slice(content_text="x " * 5000)  # ~5000 tokens of filler
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _: pytest.fail("LLM should not be called"))
    result = classify(long_slice)
    assert result.status == "extracted"
```

**Step 2-4:** Threshold (start at 400 tokens; tunable from eval). Above → `extracted` directly.

```bash
git commit -m "feat(task3): long-slice classifier short-circuits to extracted"
```

### Task C4: LLM client (provider-agnostic)

**Files:**
- Create: `task3/src/sec_toolbox/llm.py`
- Test: `task3/tests/test_llm.py`

**Step 1: Failing test (mocks the network, not the contract)**

```python
def test_llm_client_uses_env_vars(monkeypatch, httpx_mock):
    monkeypatch.setenv("TASK3_MODEL_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("TASK3_MODEL_NAME", "test-model")
    monkeypatch.setenv("TASK3_API_KEY", "k")
    httpx_mock.add_response(json={"choices":[{"message":{"content":"reply"}}]})
    from sec_toolbox.llm import LLMClient
    out = LLMClient().chat([{"role":"user","content":"hi"}])
    assert out == "reply"
    req = httpx_mock.get_requests()[0]
    assert req.url.host == "api.example.com"
    assert req.headers["authorization"] == "Bearer k"
```

**Step 2-4:** Mirror the Task 2 pattern (defaults: DeepSeek base + `deepseek-chat`). Single `chat()` method, OpenAI-compatible `/chat/completions` payload, no streaming, no reasoning. Reads `TASK3_MODEL_BASE_URL`, `TASK3_MODEL_NAME`, `TASK3_API_KEY` (`DEEPSEEK_API_KEY` fallback).

Add `pytest-httpx` to dev deps via `uv add --dev pytest-httpx`.

```bash
git commit -m "feat(task3): provider-agnostic LLM client"
```

### Task C5: Short-slice reader

**Files:**
- Modify: `task3/src/sec_toolbox/status.py`
- Modify: `task3/tests/test_status.py`
- Create: `task3/prompts/status_reader.md`

**Step 1: Failing test (mocks the LLM response, not the prompt)**

```python
def test_short_slice_routed_to_llm(monkeypatch):
    captured = {}
    def fake_read(text):
        captured["text"] = text
        return "incorporated_by_reference"
    monkeypatch.setattr("sec_toolbox.status._llm_read", fake_read)
    s = make_slice(content_text="The information required by Item 11 is hereby incorporated by reference to the 2026 Proxy Statement.")
    result = classify(s)
    assert result.status == "incorporated_by_reference"
    assert "Item 11" in captured["text"]

def test_short_slice_extracted_when_substantive(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _: "substantive")
    s = make_slice(content_text="Brief but real disclosure paragraph.")
    assert classify(s).status == "extracted"

def test_short_slice_not_applicable(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _: "not_applicable")
    s = make_slice(content_text="None.")
    assert classify(s).status == "not_applicable"

def test_short_slice_reserved(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _: "reserved")
    s = make_slice(content_text="[Reserved]")
    assert classify(s).status == "reserved"
```

**Step 2-4:** Prompt asks the human-reader question (substantive / cross-reference / not-applicable / reserved). Returns one of four labels. The prompt does **not** enumerate keywords; it describes the four functional categories. Cache by content hash to avoid re-charging on repeat runs.

Prompt file (`prompts/status_reader.md`) is the source of truth and is loaded at runtime.

```bash
git commit -m "feat(task3): short-slice LLM reader for status"
```

### Task C6: End-to-end extract on Apple

**Files:**
- Create: `task3/src/sec_toolbox/extract.py`
- Test: `task3/tests/test_extract.py`

**Step 1: Failing test**

```python
def test_extract_apple_produces_full_schedule(monkeypatch):
    # Stub LLM to "substantive" for any short slice (Apple has Item 6 = Reserved which is short)
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda t: "reserved" if "Reserved" in t else "substantive")
    items = extract(apple_html, fiscal_year=2023)
    nums = [i["item_number"] for i in items]
    assert set(nums) >= {"1","1A","1B","1C","2","3","4","5","6","7","7A","8","9","9A","9B","9C","10","11","12","13","14","15","16"}
    item6 = next(i for i in items if i["item_number"] == "6")
    assert item6["status"] == "reserved"
    item1 = next(i for i in items if i["item_number"] == "1")
    assert item1["status"] == "extracted"
    assert item1["content_text"]
    assert item1["char_range"][1] > item1["char_range"][0]
```

**Step 2-4:** `extract(html, fiscal_year) -> list[dict]`. Glue: `segment` → `classify` per slice → emit dict matching brief schema. Add an integration test that runs without monkeypatching against a recorded LLM cache (committed under `task3/tests/fixtures/llm_cache.json`).

```bash
git commit -m "feat(task3): end-to-end extract for Apple 10-K"
```

### Task C7: IBM and Exxon end-to-end

**Files:**
- Modify: `task3/tests/test_extract.py`

```python
def test_extract_ibm_2019(): ...
def test_extract_exxon_2025_part_iii_is_ibr(): ...
```

For Exxon, assert Items 10–14 land as `incorporated_by_reference`. For IBM, assert Item 6 status is `extracted` (pre-2021 "Selected Financial Data") and `Item 1A` resolves correctly. Fix any rendering/segmentation bugs the older filings expose.

```bash
git commit -m "test(task3): IBM and Exxon extraction integration tests"
```

---

## Phase D — Self-verification

### Task D1: Schedule check

**Files:**
- Create: `task3/src/sec_toolbox/verify.py`
- Test: `task3/tests/test_verify.py`

```python
def test_schedule_check_flags_missing_item():
    items = [{"item_number": n, "status": "extracted"} for n in ["1","1A","1B","2","3","4"]]  # missing rest
    issues = verify_schedule(items, fiscal_year=2023)
    assert any("missing" in i.message.lower() for i in issues)

def test_schedule_check_no_issues_on_complete():
    items = full_2023_items()
    assert verify_schedule(items, 2023) == []
```

```bash
git commit -m "feat(task3): schedule check against SEC Item taxonomy"
```

### Task D2: char_range roundtrip

**Files:**
- Modify: `task3/src/sec_toolbox/verify.py`
- Modify: `task3/tests/test_verify.py`

```python
def test_char_range_roundtrip_apple():
    html = apple_html
    items = extract(html, 2023)
    for it in items:
        if it["status"] != "extracted": continue
        s, e = it["char_range"]
        re_rendered = render_html(html[s:e]).text
        # Allow trim of whitespace; substantive text should match
        assert it["content_text"].strip()[:200] in re_rendered or re_rendered.strip()[:200] in it["content_text"]
```

```bash
git commit -m "feat(task3): char_range roundtrip verification"
```

### Task D3: XBRL Company Facts cross-check

**Files:**
- Modify: `task3/src/sec_toolbox/verify.py`
- Modify: `task3/tests/test_verify.py`

```python
def test_xbrl_metadata_matches_extraction():
    extracted_meta = {"cik": "320193", "period_end": "2023-09-30"}
    facts = load_fixture_xbrl_facts("apple_2023.json")
    assert verify_xbrl_metadata(extracted_meta, facts) == []
```

Use cached XBRL facts under `data/raw/xbrl/`; fetch via existing `endpoints.py` if not cached.

```bash
git commit -m "feat(task3): XBRL metadata cross-check"
```

### Task D4: Eval harness

**Files:**
- Create: `task3/src/sec_toolbox/eval.py`
- Modify: `task3/src/sec_toolbox/cli.py`
- Test: `task3/tests/test_eval.py`

`eval` subcommand runs extraction over the 15-filing survey set, writes per-filing JSON + a CSV of (filing, item, status, length, verification flags). Diff against previous run committed at `data/eval/baseline.csv`. Tracks regressions across changes.

```bash
git commit -m "feat(task3): eval harness over 15-filing survey set"
```

---

## Phase E — Zeabur API

### Task E1: FastAPI surface

**Files:**
- Create: `task3/src/sec_toolbox/api.py`
- Modify: `task3/pyproject.toml` (add `fastapi`, `uvicorn`)
- Test: `task3/tests/test_api.py`

```python
def test_extract_endpoint_returns_items(client):
    resp = client.post("/extract", json={"cik": "320193", "accession": "0000320193-23-000106"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert any(i["item_number"] == "1A" for i in data["items"])
```

Endpoint: `POST /extract` with `{cik, accession}` or `{file_url}`; returns `{items: [...], verification: [...]}`. Use the existing fetch toolbox under the hood. Disk cache means repeat calls to the same filing skip the network.

```bash
git commit -m "feat(task3): FastAPI /extract endpoint"
```

### Task E2: Zeabur deploy

**Files:**
- Create: `task3/Dockerfile`
- Modify: `task3/README.md`

`Dockerfile` mirrors task2 pattern (uv-based, `uv sync --frozen`, exposes 8080). Push branch, set Zeabur project, capture URL into README.

```bash
git commit -m "chore(task3): Zeabur Dockerfile and deploy notes"
```

---

## Done definition

- All tests pass: `cd task3 && uv run pytest -v`.
- Ruff clean: `uv run ruff check . && uv run ruff format --check .`.
- `extract` runs on all 15 survey filings without crash; eval CSV committed.
- Zeabur URL in `task3/README.md` and reachable.

## Things explicitly out of scope

- Item-internal extraction (risk-factor enumeration, etc.).
- Persistence beyond the existing on-disk archive cache.
- Authentication on the API.
- Partial-IBR status (single binary call per item; documented as known limitation).
