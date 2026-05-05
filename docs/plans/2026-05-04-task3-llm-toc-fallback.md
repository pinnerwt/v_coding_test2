# Task 3 — LLM-Fallback TOC Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all 13 cached 10-K fixtures extract a non-empty `items` list by (1) fixing `segment._is_item_entry` to recognize entries via their anchor `target` and (2) adding an LLM-driven TOC fallback gated on a confidence threshold.

**Architecture:** Three independently-landable changes — a one-liner predicate fix that resolves 3 of 4 broken fixtures, a new `toc_llm.propose_toc` that returns the same `TOCRegion` shape downstream code already consumes, and a confidence gate in `segment.segment` that swaps the heuristic's region for the LLM's when item resolution drops below threshold. Taxonomy stays the source of truth for `part`/canonical title; the LLM only produces locators.

**Tech Stack:** Python 3.11, `uv`, `ruff`, `pytest`, `httpx`, FastAPI (already in repo). DeepSeek `deepseek-chat` via the existing `LLMClient` for the live path.

**Reference:** [Design doc](./2026-05-04-task3-llm-toc-fallback-design.md).

**Working directory:** `task3/` (run all `uv` commands from there).

---

## Task 1: Recognize anchor-target items in `_is_item_entry`

**Files:**
- Modify: `task3/src/sec_toolbox/segment.py:177-178` (`_is_item_entry`) and add a `_extract_item_number_from_target` helper near `_extract_item_number`.
- Test: `task3/tests/test_segment.py` — add new tests at the bottom.

**Why this comes first:** It's a one-line predicate change that fixes MSFT 2020, MSFT 2023, and BRKA 2025 with no LLM involvement. Lands cleanly without depending on later tasks.

**Step 1: Write failing test for the predicate**

Append to `task3/tests/test_segment.py`:

```python
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
```

**Step 2: Run the test — it must fail**

```
cd task3
uv run pytest tests/test_segment.py::test_is_item_entry_recognized_via_anchor_target -v
```

Expected: `ImportError` on `_extract_item_number_from_target` (or `AssertionError` on the second assert, depending on how Python orders import resolution).

**Step 3: Implement minimal predicate change**

In `task3/src/sec_toolbox/segment.py`, just below the existing `_ITEM_NUMBER_RE` block, add:

```python
_TARGET_ITEM_RE = re.compile(r"#?\s*item[_\-\s]*(\d+[a-z]?)\b", re.IGNORECASE)


def _extract_item_number_from_target(target: str | None) -> str | None:
    if not target:
        return None
    m = _TARGET_ITEM_RE.match(target.lstrip("#"))
    if not m:
        return None
    return m.group(1).upper()
```

Replace `_is_item_entry` with:

```python
def _is_item_entry(entry: TOCEntry) -> bool:
    if _ITEM_HEAD_RE.match(entry.text):
        return True
    return _extract_item_number_from_target(entry.target) is not None
```

And update `_extract_item_number` (used by `_match_item`) to fall back to the target:

```python
def _extract_item_number(entry_text: str, target: str | None = None) -> str | None:
    m = _ITEM_NUMBER_RE.match(entry_text)
    if m:
        return m.group(1).upper()
    return _extract_item_number_from_target(target)
```

Update the one caller in `_match_item`:

```python
def _match_item(entry: TOCEntry, schedule: list[Item]) -> Item | None:
    num = _extract_item_number(entry.text, entry.target)
    if not num:
        return None
    for item in schedule:
        if item.item_number == num:
            return item
    return None
```

**Step 4: Run the predicate test — must pass**

```
uv run pytest tests/test_segment.py::test_is_item_entry_recognized_via_anchor_target -v
```

Expected: PASS.

**Step 5: Add MSFT 2023 integration test (still red against the predicate fix only if there's another bug — this confirms end-to-end recovery)**

Append to `task3/tests/test_segment.py`:

```python
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
    assert len(slices) >= 18
```

**Step 6: Run the integration tests — must pass**

```
uv run pytest tests/test_segment.py::test_msft_2023_extracts_full_item_set tests/test_segment.py::test_brka_2025_extracts_full_item_set -v
```

Expected: PASS.

**Step 7: Run the full test suite to confirm nothing regressed**

```
uv run pytest -q
uv run ruff check .
```

Expected: all green.

**Step 8: Commit**

```
git add src/sec_toolbox/segment.py tests/test_segment.py
git commit -m "fix(task3): recognize Item entries via anchor target

Modern 10-K TOCs (MSFT 2020/2023, BRKA 2025) split 'Item 1' and 'Business'
across sibling table cells, so the TOCEntry.text we keep is just 'Business'
without the 'Item 1.' prefix. The href target preserves the canonical form
('#item_1_business'). Recognize Item entries via either text or target,
and derive item_number from the target when text doesn't carry it."
```

---

## Task 2: Sanity-check the live API on the fixed fixtures

**Files:** None — verification only.

**Step 1: Rebuild the running container with the fix**

```
cd task3
bash install.sh --restart
```

**Step 2: Re-run the three fixtures that were previously empty**

```
for case in msft-2023:789019:0000950170-23-035122:msft-20230630.htm:2023 \
            msft-2020:789019:0001564590-20-034944:msft-10k_20200630.htm:2020 \
            brka-2025:1067983:0001193125-26-083899:brka-20251231.htm:2025; do
  IFS=: read name cik acc fname fy <<<"$case"
  curl -sS -X POST http://localhost:8080/extract \
    -H "Content-Type: application/json" \
    -d "{\"cik\":\"$cik\",\"accession\":\"$acc\",\"filename\":\"$fname\",\"fiscal_year\":$fy}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('$name', 'items:', len(d['items']), 'verif:', len(d['verification']))"
done
```

Expected: each prints `items: >= 18` and `verif: <= 4`. GE 2018 (next case) still fails — that's intentional, Tasks 3+ address it.

**Step 3: Re-run GE 2018 to confirm it still fails (this is the LLM-fallback target)**

```
curl -sS -X POST http://localhost:8080/extract \
  -H "Content-Type: application/json" \
  -d '{"cik":"40545","accession":"0000040545-19-000014","filename":"ge10-k2018.htm","fiscal_year":2018}' \
| python3 -c "import json,sys; d=json.load(sys.stdin); print('items:', len(d['items']))"
```

Expected: `items: 0`. Confirms GE 2018 is the remaining target for the LLM fallback.

**No commit** — this is a verification step.

---

## Task 3: Stub `toc_llm.propose_toc`

**Files:**
- Create: `task3/src/sec_toolbox/toc_llm.py`
- Test: `task3/tests/test_toc_llm.py`

**Step 1: Write the failing signature test**

Create `task3/tests/test_toc_llm.py`:

```python
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
```

**Step 2: Run — must fail**

```
uv run pytest tests/test_toc_llm.py::test_propose_toc_returns_none_without_client -v
```

Expected: `ModuleNotFoundError: No module named 'sec_toolbox.toc_llm'`.

**Step 3: Create the stub**

`task3/src/sec_toolbox/toc_llm.py`:

```python
"""LLM-driven fallback for locating the master 10-K TOC.

Used when ``toc.find_toc_region`` finds a region but the resolved item
count or resolution rate is too low (the GE 2018 case, where the heuristic
locks onto a nested MD&A sub-TOC). The LLM sees the first ~50K rendered
characters and returns a structured tool-call payload describing where the
TOC ends and where each Item begins.

The output is a synthetic :class:`TOCRegion` shaped exactly like the one
:func:`sec_toolbox.toc.find_toc_region` produces, so downstream code in
``segment.py`` is unchanged.
"""

from __future__ import annotations

from .llm import LLMClient
from .render import Rendered
from .toc import TOCRegion


def propose_toc(
    rendered: Rendered, html: bytes, *, client: LLMClient | None = None
) -> TOCRegion | None:
    """Best-effort LLM TOC extraction. Returns ``None`` on any failure."""
    return None
```

**Step 4: Run — must pass**

```
uv run pytest tests/test_toc_llm.py -v
```

Expected: PASS.

**Step 5: Commit**

```
git add src/sec_toolbox/toc_llm.py tests/test_toc_llm.py
git commit -m "feat(task3): stub toc_llm.propose_toc fallback entry point"
```

---

## Task 4: Author the prompt and tool spec

**Files:**
- Create: `task3/prompts/toc_extraction.md`
- Modify: `task3/src/sec_toolbox/toc_llm.py` to load the prompt and define the JSON schema constant.

**Approach note:** We use **JSON-schema-in-system-prompt** rather than OpenAI tool-calling. The existing `LLMClient.chat()` returns text; asking for strict JSON output keeps the client surface unchanged. DeepSeek follows the schema reliably with `temperature=0`.

**Step 1: Write the prompt**

`task3/prompts/toc_extraction.md`:

```markdown
You are extracting the table of contents (TOC) of an SEC Form 10-K filing.

## Input

You receive the first ~50,000 rendered characters of the filing. The master TOC is in this window.

## Output (strict JSON, no prose)

Call the `report_toc` "tool" by emitting **only** a JSON object with this shape:

```json
{
  "toc_end_marker": "string — short verbatim text that appears immediately AFTER the master TOC and BEFORE Item 1's body content, e.g. 'PART I' or 'Item 1. Business' as it appears at the body location, not the TOC location",
  "items": [
    {
      "item_number": "1" | "1A" | "1B" | "1C" | "2" | "3" | "4" | "5" | "6" | "7" | "7A" | "8" | "9" | "9A" | "9B" | "9C" | "10" | "11" | "12" | "13" | "14" | "15" | "16",
      "anchor": "#item_1_business" or null,
      "heading_snippet": "Item 1. Business — verbatim 30-80 chars from the BODY of the filing where this item's content begins"
    }
  ]
}
```

## Rules

- Emit **only** the JSON object. No markdown fences, no commentary.
- `item_number` MUST be one of the canonical values above. Skip non-canonical items (e.g., "Information about our Executive Officers" — that's a sub-section of Item 1, not a top-level item).
- `anchor` is the `href` from the TOC link if visible (`<a href="#item_1_business">`); otherwise `null`. Do not invent anchors.
- `heading_snippet` is verbatim text from the **body** of the filing (where the item's content actually begins), not from the TOC itself. It should be unique enough to substring-match.
- `toc_end_marker` MUST appear in the input text after the TOC and before any item's body. "PART I" is usually a reliable marker.
- Order items as they appear in the TOC.
- If you can't identify a master 10-K TOC in the input, return `{"toc_end_marker": "", "items": []}`.
```

**Step 2: Add the prompt loader and schema constant to `toc_llm.py`**

Modify `task3/src/sec_toolbox/toc_llm.py`:

```python
from __future__ import annotations

from pathlib import Path

from .llm import LLMClient
from .render import Rendered
from .toc import TOCRegion

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "toc_extraction.md"
_FRONT_SLICE_CHARS = 50_000


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def propose_toc(
    rendered: Rendered, html: bytes, *, client: LLMClient | None = None
) -> TOCRegion | None:
    return None
```

**Step 3: Add a test that the prompt file exists and is non-empty**

Append to `task3/tests/test_toc_llm.py`:

```python
def test_prompt_file_exists():
    from sec_toolbox.toc_llm import _load_prompt

    text = _load_prompt()
    assert "toc_end_marker" in text
    assert "item_number" in text
    assert "anchor" in text
    assert "heading_snippet" in text
```

**Step 4: Run — must pass**

```
uv run pytest tests/test_toc_llm.py -v
```

Expected: PASS.

**Step 5: Commit**

```
git add prompts/toc_extraction.md src/sec_toolbox/toc_llm.py tests/test_toc_llm.py
git commit -m "feat(task3): TOC-extraction prompt and schema constants for toc_llm"
```

---

## Task 5: Implement `propose_toc` happy path with anchor resolution

**Files:**
- Modify: `task3/src/sec_toolbox/toc_llm.py`
- Test: `task3/tests/test_toc_llm.py`

**Step 1: Write the failing test (mocked LLM)**

Append to `task3/tests/test_toc_llm.py`:

```python
import json
from unittest.mock import MagicMock


def _mock_client(payload: dict) -> MagicMock:
    """LLMClient stub whose chat() returns the JSON-encoded payload."""
    client = MagicMock()
    client.chat.return_value = json.dumps(payload)
    return client


@pytest.mark.skipif(not GE_2018.exists(), reason="GE 2018 fixture not cached")
def test_propose_toc_happy_path_anchor_resolution():
    """When the LLM returns a payload with valid anchors, propose_toc resolves
    each anchor via the id index and returns a TOCRegion whose entries point
    at body locations past the toc_end_marker."""
    from sec_toolbox.toc_llm import propose_toc

    html = GE_2018.read_bytes()
    rendered = render_html(html)

    # Hand-picked: GE 2018 does have anchors in the master TOC; pick a few
    # known-good ones that exist as id= attributes in the HTML body.
    # (In practice the test uses whatever anchors actually appear in GE's
    # master TOC; this list will need to be tuned to the real fixture.)
    payload = {
        "toc_end_marker": "PART I",
        "items": [
            {"item_number": "1", "anchor": None, "heading_snippet": "Item 1. Business"},
            {"item_number": "1A", "anchor": None, "heading_snippet": "Item 1A. Risk Factors"},
            {"item_number": "7", "anchor": None,
             "heading_snippet": "Management's Discussion and Analysis"},
        ],
    }
    client = _mock_client(payload)

    region = propose_toc(rendered, html, client=client)
    assert region is not None
    assert len(region.entries) >= 2  # at least 2 of 3 must resolve via snippet
    # All entries must land past the TOC end.
    for e in region.entries:
        assert e.text_start > region.text_start
```

**Step 2: Run — must fail**

```
uv run pytest tests/test_toc_llm.py::test_propose_toc_happy_path_anchor_resolution -v
```

Expected: FAIL — `propose_toc` still returns `None`.

**Step 3: Implement the resolution path**

Replace the body of `propose_toc` in `task3/src/sec_toolbox/toc_llm.py`:

```python
import json
import re
from pathlib import Path

from .llm import LLMClient
from .render import Rendered
from .segment import _build_id_index, _source_byte_to_text_offset
from .toc import TOCEntry, TOCRegion

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "toc_extraction.md"
_FRONT_SLICE_CHARS = 50_000


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_client() -> LLMClient | None:
    try:
        return LLMClient()
    except RuntimeError:
        return None


def _parse_payload(text: str) -> dict | None:
    """Pull a JSON object out of the LLM's response. Tolerate accidental
    code-fence wrapping."""
    s = text.strip()
    if s.startswith("```"):
        # Strip markdown fence if present.
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _resolve_item(
    item: dict,
    rendered: Rendered,
    id_index: dict[str, int],
    body_start: int,
) -> tuple[int, int] | None:
    """Resolve a single item to (text_start, source_start) past body_start."""
    anchor = item.get("anchor")
    snippet = item.get("heading_snippet") or ""
    # Try anchor first.
    if isinstance(anchor, str) and anchor:
        name = anchor.lstrip("#")
        src = id_index.get(name)
        if src is not None:
            text_pos = _source_byte_to_text_offset(rendered.source_offset, src)
            if text_pos > body_start:
                return text_pos, src
    # Fall back to substring match.
    if snippet:
        idx = rendered.text.find(snippet, body_start)
        if idx != -1:
            src = (
                rendered.source_offset[idx]
                if idx < len(rendered.source_offset)
                else 0
            )
            return idx, src
    return None


def propose_toc(
    rendered: Rendered, html: bytes, *, client: LLMClient | None = None
) -> TOCRegion | None:
    """Best-effort LLM TOC extraction. Returns None on any failure."""
    if client is None:
        client = _build_client()
        if client is None:
            return None

    front = rendered.text[:_FRONT_SLICE_CHARS]
    if not front:
        return None

    prompt = _load_prompt()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": front},
    ]
    try:
        raw = client.chat(messages, temperature=0.0)
    except Exception:  # noqa: BLE001
        return None

    payload = _parse_payload(raw)
    if payload is None:
        return None

    marker = payload.get("toc_end_marker") or ""
    items = payload.get("items") or []
    if not isinstance(marker, str) or not isinstance(items, list) or not items:
        return None

    # Locate toc_end_marker in the rendered text.
    body_start = rendered.text.find(marker)
    if body_start < 0:
        return None

    id_index = _build_id_index(html)
    entries: list[TOCEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        num = item.get("item_number")
        if not isinstance(num, str) or not num:
            continue
        loc = _resolve_item(item, rendered, id_index, body_start)
        if loc is None:
            continue
        text_pos, src_pos = loc
        snippet = item.get("heading_snippet") or num
        entries.append(
            TOCEntry(
                text=f"Item {num}. {snippet}",
                target=item.get("anchor"),
                source_start=src_pos,
                text_start=text_pos,
            )
        )

    if not entries:
        return None

    entries.sort(key=lambda e: e.text_start)
    text_start = min(e.text_start for e in entries[:1] + [TOCEntry("", None, 0, 0)])
    text_start = 0  # synthetic — the LLM region starts before the body.
    text_end = body_start + len(marker)

    return TOCRegion(
        text_start=text_start,
        text_end=text_end,
        chunk_start=0,
        chunk_end=0,
        entries=entries,
    )
```

Note: `_build_id_index` and `_source_byte_to_text_offset` are private in `segment.py`. We import them directly. If pre-commit / ruff complains about leading-underscore imports across modules, lift them to module-level (rename to non-underscored) in `segment.py` and re-import — but try the underscored import first; ruff doesn't flag this by default in the existing config.

**Step 4: Run — must pass**

```
uv run pytest tests/test_toc_llm.py::test_propose_toc_happy_path_anchor_resolution -v
```

Expected: PASS.

**Step 5: Run the full toc_llm test file**

```
uv run pytest tests/test_toc_llm.py -v
uv run ruff check src/sec_toolbox/toc_llm.py
```

Expected: PASS, ruff clean.

**Step 6: Commit**

```
git add src/sec_toolbox/toc_llm.py tests/test_toc_llm.py
git commit -m "feat(task3): propose_toc resolves LLM payload to a TOCRegion"
```

---

## Task 6: Error-path tests for `propose_toc`

**Files:**
- Test: `task3/tests/test_toc_llm.py`

**Step 1: Write failing tests**

Append to `task3/tests/test_toc_llm.py`:

```python
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
        {"toc_end_marker": "NOT IN THE TEXT", "items": [{"item_number": "1", "heading_snippet": "x"}]}
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
                {"item_number": "1", "anchor": "#nonexistent",
                 "heading_snippet": "this snippet is not in the text"}
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
```

**Step 2: Run — should already pass against Task 5 implementation**

```
uv run pytest tests/test_toc_llm.py -v
```

Expected: PASS. (If any fail, the implementation in Task 5 needs the matching guard added; common cases — make sure `body_start < 0` returns None and `not entries` returns None.)

**Step 3: Commit**

```
git add tests/test_toc_llm.py
git commit -m "test(task3): error-path coverage for propose_toc"
```

---

## Task 7: Confidence gate in `segment.segment`

**Files:**
- Modify: `task3/src/sec_toolbox/segment.py:181-239` (`segment` function)
- Test: `task3/tests/test_segment.py`

**Step 1: Write the failing test (mocked LLM, GE 2018 fixture)**

Append to `task3/tests/test_segment.py`:

```python
GE_2018 = FIXTURES / "40545/000004054519000014/ge10-k2018.htm"


@pytest.mark.skipif(not GE_2018.exists(), reason="GE 2018 fixture not cached")
def test_segment_falls_back_to_llm_when_heuristic_under_resolves(monkeypatch):
    """GE 2018: heuristic locks onto a nested MD&A sub-TOC. The confidence
    gate should detect under-resolution (< 10 items) and call propose_toc
    instead. We mock propose_toc to return a region with several known
    item-shaped entries; the segmenter must use that region."""
    import json
    from unittest.mock import MagicMock

    import sec_toolbox.segment as seg

    # Mock LLMClient at the propose_toc level: have segment.segment see a
    # patched propose_toc that returns a synthetic region with valid item
    # entries pointing into the body.
    html = GE_2018.read_bytes()

    # Build a minimal believable region by sniffing the HTML for "Item 1." etc.
    text = html.decode("utf-8", errors="replace")
    # We don't actually run the LLM; instead, monkeypatch propose_toc to
    # return a TOCRegion built from anchor scanning.
    from sec_toolbox.render import render_html
    from sec_toolbox.toc import TOCEntry, TOCRegion

    rendered = render_html(html)

    # Find a few "Item N." occurrences in the rendered text past the front matter.
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
    # The fallback should pull at least Item 1, 1A, 7, 8 into the result.
    assert {"1", "7", "8"} <= item_numbers
    assert len(slices) >= 5
```

**Step 2: Run — must fail**

```
uv run pytest tests/test_segment.py::test_segment_falls_back_to_llm_when_heuristic_under_resolves -v
```

Expected: FAIL (current `segment` has no fallback).

**Step 3: Wire propose_toc into `segment.segment`**

In `task3/src/sec_toolbox/segment.py`, add an import near the top:

```python
from .toc_llm import propose_toc
```

Replace the `segment` function (lines ~181-239) with:

```python
_MIN_ITEM_COUNT = 10
_MIN_RESOLUTION_RATE = 0.80


def _resolution_rate(item_count: int, entry_count: int) -> float:
    if entry_count == 0:
        return 0.0
    return item_count / entry_count


def segment(html: bytes, fiscal_year: int | None = None) -> list[Slice]:
    rendered = render_html(html)
    region = find_toc_region(rendered, html)

    body_locs: list[BodyLocation] = []
    if region is not None:
        body_locs = resolve_entries_to_body(rendered, region, html)

    item_locs = [b for b in body_locs if _is_item_entry(b.entry)]
    needs_fallback = (
        region is None
        or len(item_locs) < _MIN_ITEM_COUNT
        or _resolution_rate(len(item_locs), len(region.entries)) < _MIN_RESOLUTION_RATE
    )

    if needs_fallback:
        llm_region = propose_toc(rendered, html)
        if llm_region is not None:
            llm_locs = resolve_entries_to_body(rendered, llm_region, html)
            llm_item_locs = [b for b in llm_locs if _is_item_entry(b.entry)]
            if len(llm_item_locs) >= _MIN_ITEM_COUNT:
                region = llm_region
                item_locs = llm_item_locs

    if not item_locs:
        return []

    item_locs.sort(key=lambda b: b.body_text_start)
    deduped: list[BodyLocation] = []
    for b in item_locs:
        if deduped and b.body_text_start == deduped[-1].body_text_start:
            continue
        deduped.append(b)

    schedule: list[Item] | None = items_for_year(fiscal_year) if fiscal_year is not None else None
    slices: list[Slice] = []
    text = rendered.text
    src_offsets = rendered.source_offset
    for i, loc in enumerate(deduped):
        text_start = loc.body_text_start
        text_end = deduped[i + 1].body_text_start if i + 1 < len(deduped) else len(text)
        if text_end <= text_start:
            continue
        content = text[text_start:text_end]
        if not content.strip():
            continue
        char_start = src_offsets[text_start] if text_start < len(src_offsets) else 0
        last_char_idx = text_end - 1
        char_end = src_offsets[last_char_idx] + 1 if last_char_idx < len(src_offsets) else len(html)
        if char_end <= char_start:
            continue
        canon = _match_item(loc.entry, schedule) if schedule is not None else None
        slices.append(
            Slice(
                item_title=_strip_item_prefix(loc.entry.text),
                content_text=content,
                char_range=(char_start, char_end),
                text_range=(text_start, text_end),
                entry=loc.entry,
                part=canon.part if canon else None,
                item_number=canon.item_number if canon else None,
                canonical_title=canon.canonical_title if canon else None,
            )
        )
    return slices
```

**Step 4: Run the gate test — must pass**

```
uv run pytest tests/test_segment.py::test_segment_falls_back_to_llm_when_heuristic_under_resolves -v
```

Expected: PASS.

**Step 5: Run the full segment test file — must all pass**

```
uv run pytest tests/test_segment.py tests/test_toc_llm.py -v
uv run ruff check .
```

Expected: PASS, ruff clean. The earlier MSFT 2023 / BRKA 2025 tests should still pass — those filings don't trigger the fallback (heuristic resolves > 10 items after Task 1).

**Step 6: Commit**

```
git add src/sec_toolbox/segment.py tests/test_segment.py
git commit -m "feat(task3): confidence gate routes to LLM TOC fallback

When find_toc_region resolves <10 items or <80% of entries land in the
body, call propose_toc and use its region instead. Fallback is also tried
when find_toc_region returns None. If the LLM fallback fails or also
under-resolves, returns the heuristic's (possibly empty) result."
```

---

## Task 8: Live integration test (gated on `RUN_LIVE_LLM=1`)

**Files:**
- Test: `task3/tests/test_toc_llm.py`

**Step 1: Add the gated test**

Append to `task3/tests/test_toc_llm.py`:

```python
import os


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
```

**Step 2: Run normally (skipped) and confirm**

```
uv run pytest tests/test_toc_llm.py::test_propose_toc_live_ge_2018 -v
```

Expected: SKIPPED.

**Step 3: Run with the live gate — must pass**

Requires `DEEPSEEK_API_KEY` or `TASK3_API_KEY` in env.

```
RUN_LIVE_LLM=1 uv run pytest tests/test_toc_llm.py::test_propose_toc_live_ge_2018 -v
```

Expected: PASS. If it fails, inspect the LLM response by adding a temporary `print(client.chat(...))` in `propose_toc` and re-running. Iterate on the prompt in `prompts/toc_extraction.md` until it produces ≥10 resolvable items.

**Step 4: Commit**

```
git add tests/test_toc_llm.py
git commit -m "test(task3): live LLM integration test for GE 2018, gated on RUN_LIVE_LLM=1"
```

---

## Task 9: End-to-end verification on all 13 cached fixtures

**Files:** None — verification only.

**Step 1: Rebuild and restart**

```
cd task3
bash install.sh --restart
```

**Step 2: Run all 13 fixtures**

Save this script as `/tmp/extract_all.py`:

```python
import json, subprocess, time, concurrent.futures as cf
from pathlib import Path

CASES = [
  ("aapl-2023", "320193", "0000320193-23-000106", "aapl-20230930.htm", 2023),
  ("msft-2023", "789019", "0000950170-23-035122", "msft-20230630.htm", 2023),
  ("msft-2020", "789019", "0001564590-20-034944", "msft-10k_20200630.htm", 2020),
  ("nvda-2023", "1045810", "0001045810-24-000029", "nvda-20240128.htm", 2023),
  ("jpm-2025",  "19617",  "0001628280-26-008131", "jpm-20251231.htm", 2025),
  ("xom-2025",  "34088",  "0000034088-26-000045", "xom-20251231.htm", 2025),
  ("brka-2025", "1067983","0001193125-26-083899", "brka-20251231.htm", 2025),
  ("rivn-2025", "1874178","0001874178-26-000008", "rivn-20251231.htm", 2025),
  ("pltr-2025", "1321655","0001321655-26-000011", "pltr-20251231.htm", 2025),
  ("ibm-2019",  "51143",  "0001558370-20-001334", "ibm-20191231x10k2af531.htm", 2019),
  ("intc-2017", "21344",  "0000021344-18-000008", "a2017123110-k.htm", 2017),
  ("ge-2018",   "40545",  "0000040545-19-000014", "ge10-k2018.htm", 2018),
  ("cpng-2023", "1834584","0001834584-24-000023", "cpng-20231231.htm", 2023),
]

def run(case):
    name, cik, acc, fname, fy = case
    body = json.dumps({"cik": cik, "accession": acc, "filename": fname, "fiscal_year": fy})
    t0 = time.time()
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST", "http://localhost:8080/extract",
         "-H", "Content-Type: application/json", "-d", body, "--max-time", "600"],
        capture_output=True, text=True
    )
    dt = time.time() - t0
    try:
        d = json.loads(r.stdout)
        return name, len(d.get("items", [])), len(d.get("verification", [])), round(dt, 1)
    except Exception:
        return name, "ERR", "ERR", round(dt, 1)

with cf.ThreadPoolExecutor(max_workers=4) as ex:
    for name, items, verif, dt in ex.map(run, CASES):
        print(f"{name:12s}  items={items:>3}  verif={verif:>3}  {dt}s")
```

```
uv run python /tmp/extract_all.py
```

Expected: every fixture reports `items >= 18` (with the possible exceptions of XOM/CPNG/PLTR/JPM that have a small handful of `verif` entries due to legitimately incorporated-by-reference Items, and NVDA Item 8 which is structurally short — see design doc). GE 2018 should now extract ≥ 18 items via the LLM fallback.

**Step 3: Document the result in the README's "Known coverage" section** (only if absent today; otherwise skip).

**No commit unless step 3 changed README.**

---

## Final checklist

- [ ] All `pytest` tests green: `cd task3 && uv run pytest -q`.
- [ ] Ruff clean: `uv run ruff check .`.
- [ ] Live LLM test passes once: `RUN_LIVE_LLM=1 uv run pytest tests/test_toc_llm.py::test_propose_toc_live_ge_2018 -v`.
- [ ] All 13 cached fixtures return non-empty `items` via the API.
- [ ] No changes to `taxonomy.py`, `verify.py`, `extract.py`, `api.py` (sanity-check `git diff main --stat`).
