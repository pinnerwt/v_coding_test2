# Task 3 Extract Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python agent under `task3/src/extract_agent/` that drives a DeepSeek tool-calling loop to produce per-item JSON for one 10-K filing per invocation, replacing the per-filing script + skill prose workflow.

**Architecture:** Big DeepSeek model (`deepseek-v4-pro`, thinking on) runs a tool-call loop with hybrid coarse-default + fine-escape-hatch tools. Small DeepSeek model (`deepseek-v4-flash`, thinking on) handles Phase 5b status splitting via parallel fan-out. Deterministic Python owns cleaning, anchoring, slicing, validation; LLM owns orchestration, per-filing tweaks, and ambiguous-case classification. Session state is held in Python and never serialized into the LLM message log.

**Tech Stack:** Python 3.11, `httpx` (async, OpenAI-compatible client lifted from task2), `pytest`, `pytest-asyncio`, `pytest-httpx`, `ruff`. Reuses existing `task3/scripts/extract/_validate.py` and `_merge_5b.py` (wrapped as library imports). Managed via `uv` per repo convention.

**Reference design:** [docs/plans/2026-05-05-task3-extract-agent-design.md](2026-05-05-task3-extract-agent-design.md). When this plan is ambiguous, the design doc wins.

**TDD discipline:** Per repo CLAUDE.md, every code-bearing task starts with a failing test. Use @superpowers:test-driven-development. Use @superpowers:verification-before-completion before claiming any task complete (run the tests, confirm output, then commit).

---

## Task 1: Scaffold the package skeleton

**Files:**
- Create: `task3/src/extract_agent/__init__.py` (empty)
- Modify: `task3/pyproject.toml` (add deps)
- Test: `task3/tests/extract_agent/test_smoke.py`

**Step 1: Add deps via `uv`**

Run from `task3/`:
```bash
uv add --dev pytest-asyncio pytest-httpx
```

**Step 2: Write failing smoke test**

```python
# task3/tests/extract_agent/test_smoke.py
def test_package_importable():
    import extract_agent  # noqa: F401
```

Also add `task3/tests/extract_agent/__init__.py` (empty) and `task3/tests/__init__.py` if not present.

**Step 3: Run, expect ModuleNotFoundError**

```bash
cd task3 && uv run pytest tests/extract_agent/test_smoke.py -v
```

Expected: FAIL with "No module named 'extract_agent'".

**Step 4: Make it pass**

Create `task3/src/extract_agent/__init__.py` (empty file). Add to `task3/pyproject.toml` under `[tool.hatch.build.targets.wheel]`:

```toml
packages = ["src/sec_toolbox", "src/extract_agent"]
```

**Step 5: Verify**

```bash
cd task3 && uv sync && uv run pytest tests/extract_agent/test_smoke.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
cd /home/pgi/v_coding_test2
git add task3/pyproject.toml task3/uv.lock task3/src/extract_agent/__init__.py task3/tests/extract_agent/__init__.py task3/tests/extract_agent/test_smoke.py
git commit -m "feat(task3): scaffold extract_agent package"
```

---

## Task 2: Port `cleaner.py` from per-filing scripts (TDD)

The cleaner is the same 9-line transform across all 8 per-filing scripts. Lift it into `extract_agent/cleaner.py` with optional per-filing knobs (extra strip patterns, custom block tags).

**Files:**
- Create: `task3/src/extract_agent/cleaner.py`
- Test: `task3/tests/extract_agent/test_cleaner.py`
- Reference: `task3/scripts/extract/320193-000032019323000106.py` (Apple, the canonical implementation, lines 24–78)

**Step 1: Write failing tests**

```python
# task3/tests/extract_agent/test_cleaner.py
from extract_agent.cleaner import clean_html

def test_strips_script_and_style():
    raw = "<html><script>x=1</script><p>Hi</p><style>p{}</style></html>"
    assert "x=1" not in clean_html(raw)
    assert "p{}" not in clean_html(raw)
    assert "Hi" in clean_html(raw)

def test_strips_inline_xbrl_hidden_and_header():
    raw = '<ix:hidden>SECRET</ix:hidden><p>Visible</p><ix:header>HDR</ix:header>'
    out = clean_html(raw)
    assert "SECRET" not in out
    assert "HDR" not in out
    assert "Visible" in out

def test_inserts_newlines_at_block_tags():
    raw = "<p>A</p><p>B</p><div>C</div>"
    out = clean_html(raw)
    assert "A\nB" in out or "A\n\nB" in out
    assert "C" in out

def test_decodes_entities_and_normalises_nbsp():
    raw = "<p>A&nbsp;B&amp;C</p>"
    out = clean_html(raw)
    assert "A B&C" in out
    assert "\xa0" not in out

def test_collapses_horizontal_whitespace():
    raw = "<p>A   B</p>"
    assert "A B" in clean_html(raw)

def test_collapses_three_plus_newlines_to_two():
    raw = "<p>A</p><p></p><p></p><p></p><p>B</p>"
    out = clean_html(raw)
    assert "\n\n\n" not in out

def test_extra_strip_patterns_applied():
    raw = "<p>Apple Inc. | 2023 Form 10-K | 17</p><p>Body</p>"
    out = clean_html(raw, extra_strip_patterns=[r"\nApple Inc\.\s*\|\s*2023 Form 10-K\s*\|\s*\d+\s*\n"])
    assert "Form 10-K" not in out
    assert "Body" in out
```

**Step 2: Run, expect ImportError**

```bash
cd task3 && uv run pytest tests/extract_agent/test_cleaner.py -v
```

Expected: FAIL with import error.

**Step 3: Implement `clean_html`**

Port the function from `task3/scripts/extract/320193-000032019323000106.py:48-78`. Signature:

```python
def clean_html(raw: str, *, extra_strip_patterns: list[str] | None = None) -> str:
```

`extra_strip_patterns` is a list of regex strings applied after the default cleanup, each via `re.sub(pat, "\n", text)`. This replaces the per-filing `PAGE_FOOTER_RE` knob without making the cleaner filing-specific.

**Step 4: Run tests until green**

```bash
cd task3 && uv run pytest tests/extract_agent/test_cleaner.py -v
```

Expected: 7 PASS.

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2
git add task3/src/extract_agent/cleaner.py task3/tests/extract_agent/test_cleaner.py
git commit -m "feat(task3): port cleaner with extra-strip knob"
```

---

## Task 3: Port `anchors.py` (TDD)

The default `ITEM_RE` + `ITEM_TO_PART` map + TOC-region dedup logic is shared across all per-filing scripts. Lift it to `extract_agent/anchors.py`.

**Files:**
- Create: `task3/src/extract_agent/anchors.py`
- Test: `task3/tests/extract_agent/test_anchors.py`
- Reference: `task3/scripts/extract/320193-000032019323000106.py:81-96, 142-152`

**Step 1: Write failing tests**

```python
# task3/tests/extract_agent/test_anchors.py
from extract_agent.anchors import find_anchors, dedupe_anchors, ITEM_TO_PART

def test_finds_basic_item_headings():
    text = "Item 1.    Business\nstuff\n\nItem 1A.   Risk Factors\nstuff"
    anchors = find_anchors(text)
    nums = [a["item_number"] for a in anchors]
    assert nums == ["1", "1A"]

def test_skips_bare_item_running_headers():
    # "Item 1" with no period/colon should not match (filters page-running headers)
    text = "Item 1\nstuff\nItem 1A. Risk Factors\nstuff"
    anchors = find_anchors(text)
    assert [a["item_number"] for a in anchors] == ["1A"]

def test_skips_out_of_range_item_numbers():
    text = "Item 60. Footnote ref\nItem 1. Business\nstuff"
    anchors = find_anchors(text)
    assert [a["item_number"] for a in anchors] == ["1"]

def test_part_map_is_canonical_form_10k():
    assert ITEM_TO_PART["1"] == "I"
    assert ITEM_TO_PART["7A"] == "II"
    assert ITEM_TO_PART["10"] == "III"
    assert ITEM_TO_PART["15"] == "IV"

def test_dedupe_drops_toc_when_body_exists():
    anchors = [
        {"item_number": "1", "match_start": 1000, "match_end": 1020, "title": "Business"},
        {"item_number": "1", "match_start": 9000, "match_end": 9020, "title": "Business"},
        {"item_number": "1A", "match_start": 1100, "match_end": 1130, "title": "Risk Factors"},
    ]
    deduped = dedupe_anchors(anchors, toc_region_end=8000)
    by_num = {a["item_number"]: a for a in deduped}
    # Item 1 should keep only the body anchor (>8000)
    assert by_num["1"]["match_start"] == 9000
    # Item 1A has only TOC-region anchor; keep it (no body alternative)
    assert by_num["1A"]["match_start"] == 1100

def test_anchor_captures_title_when_present():
    text = "Item 1.   Business\nbody"
    anchors = find_anchors(text)
    assert anchors[0]["title"] == "Business"

def test_anchor_empty_title_when_absent():
    text = "Item 1.\nBusiness\nbody"
    anchors = find_anchors(text)
    # Title may be empty or "Business" depending on regex; both acceptable —
    # the slicer's "next non-empty line" fallback handles empty titles.
    assert anchors[0]["title"] in ("", "Business")
```

**Step 2: Run, expect ImportError**

```bash
cd task3 && uv run pytest tests/extract_agent/test_anchors.py -v
```

Expected: FAIL with import error.

**Step 3: Implement**

```python
# task3/src/extract_agent/anchors.py
import re

ITEM_RE = re.compile(
    r"^\s*ITEM\s+(1[0-6]|[1-9])([A-C])?\s*[\.\:]\s*(.*?)$",
    re.IGNORECASE | re.MULTILINE,
)

ITEM_TO_PART = {
    "1": "I", "1A": "I", "1B": "I", "1C": "I", "2": "I", "3": "I", "4": "I",
    "5": "II", "6": "II", "7": "II", "7A": "II", "8": "II",
    "9": "II", "9A": "II", "9B": "II", "9C": "II",
    "10": "III", "11": "III", "12": "III", "13": "III", "14": "III",
    "15": "IV", "16": "IV",
}

def find_anchors(text: str, *, regex: re.Pattern | None = None) -> list[dict]:
    pat = regex or ITEM_RE
    out = []
    for m in pat.finditer(text):
        num = m.group(1)
        letter = (m.group(2) or "").upper()
        item_id = num + letter
        if item_id not in ITEM_TO_PART:
            continue
        out.append({
            "item_number": item_id,
            "title": (m.group(3) or "").strip(),
            "match_start": m.start(),
            "match_end": m.end(),
        })
    return out

def dedupe_anchors(anchors: list[dict], *, toc_region_end: int = 8000) -> list[dict]:
    """For each item_number: if any anchor has match_start > toc_region_end,
    drop all anchors with match_start <= toc_region_end for that item.
    Keep all surviving anchors (slicing decides longest later)."""
    by_num: dict[str, list[dict]] = {}
    for a in anchors:
        by_num.setdefault(a["item_number"], []).append(a)
    out: list[dict] = []
    for num, group in by_num.items():
        has_body = any(a["match_start"] > toc_region_end for a in group)
        pool = [a for a in group if a["match_start"] > toc_region_end] if has_body else group
        out.extend(pool)
    out.sort(key=lambda a: a["match_start"])
    return out
```

**Step 4: Run tests until green**

```bash
cd task3 && uv run pytest tests/extract_agent/test_anchors.py -v
```

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2
git add task3/src/extract_agent/anchors.py task3/tests/extract_agent/test_anchors.py
git commit -m "feat(task3): port anchor finder + TOC dedup"
```

---

## Task 4: Port `slicer.py` (TDD)

Slice each item's body between its anchor and the next anchor. Pick the longest body when an item has multiple anchors. Trim trailing page-numbers from short bodies.

**Files:**
- Create: `task3/src/extract_agent/slicer.py`
- Test: `task3/tests/extract_agent/test_slicer.py`
- Reference: `task3/scripts/extract/320193-000032019323000106.py:128-191`

**Step 1: Write failing tests**

```python
# task3/tests/extract_agent/test_slicer.py
from extract_agent.slicer import slice_items

def test_slices_between_adjacent_anchors():
    text = "Item 1.   Business\nbody1 body1\n\nItem 2. Properties\nbody2"
    anchors = [
        {"item_number": "1", "match_start": 0, "match_end": len("Item 1.   Business"), "title": "Business"},
        {"item_number": "2", "match_start": text.find("Item 2."), "match_end": text.find("Item 2.") + len("Item 2. Properties"), "title": "Properties"},
    ]
    records = slice_items(text, anchors)
    by_num = {r["item_number"]: r for r in records}
    assert "body1" in by_num["1"]["content_text"]
    assert "body2" in by_num["2"]["content_text"]
    assert by_num["1"]["part"] == "I"
    assert by_num["2"]["part"] == "I"

def test_picks_longest_body_when_item_has_two_anchors():
    text = "Item 1.   Stub\n5\nItem 1.   Real Business\nlong body of business\nItem 2. Properties\nx"
    a1 = text.index("Item 1.   Stub")
    a2 = text.index("Item 1.   Real Business")
    a3 = text.index("Item 2.")
    anchors = [
        {"item_number": "1", "match_start": a1, "match_end": a1 + len("Item 1.   Stub"), "title": "Stub"},
        {"item_number": "1", "match_start": a2, "match_end": a2 + len("Item 1.   Real Business"), "title": "Real Business"},
        {"item_number": "2", "match_start": a3, "match_end": a3 + len("Item 2. Properties"), "title": "Properties"},
    ]
    records = slice_items(text, anchors)
    item1 = next(r for r in records if r["item_number"] == "1")
    assert "long body" in item1["content_text"]
    assert item1["item_title"] == "Real Business"

def test_trims_trailing_pagenum_when_body_short():
    text = "Item 4. Mine Safety\nNot applicable.\n\n32\nItem 5. X\ny"
    a1 = text.index("Item 4.")
    a2 = text.index("Item 5.")
    anchors = [
        {"item_number": "4", "match_start": a1, "match_end": a1 + len("Item 4. Mine Safety"), "title": "Mine Safety"},
        {"item_number": "5", "match_start": a2, "match_end": a2 + len("Item 5. X"), "title": "X"},
    ]
    records = slice_items(text, anchors)
    item4 = next(r for r in records if r["item_number"] == "4")
    assert "32" not in item4["content_text"]
    assert "Not applicable" in item4["content_text"]

def test_does_not_trim_pagenum_from_long_body():
    body = "x " * 300 + "2023"
    text = f"Item 1. Business\n{body}\nItem 2. X\ny"
    a1 = text.index("Item 1.")
    a2 = text.index("Item 2.")
    anchors = [
        {"item_number": "1", "match_start": a1, "match_end": a1 + len("Item 1. Business"), "title": "Business"},
        {"item_number": "2", "match_start": a2, "match_end": a2 + len("Item 2. X"), "title": "X"},
    ]
    records = slice_items(text, anchors)
    item1 = next(r for r in records if r["item_number"] == "1")
    assert "2023" in item1["content_text"]

def test_records_sorted_by_char_range_start():
    text = "Item 2. P\nb2\nItem 1. B\nb1"
    # Anchors out of order intentionally
    a2 = text.index("Item 2.")
    a1 = text.index("Item 1.")
    anchors = [
        {"item_number": "1", "match_start": a1, "match_end": a1 + len("Item 1. B"), "title": "B"},
        {"item_number": "2", "match_start": a2, "match_end": a2 + len("Item 2. P"), "title": "P"},
    ]
    records = slice_items(text, anchors)
    starts = [r["char_range"][0] for r in records]
    assert starts == sorted(starts)
```

**Step 2: Run, expect ImportError**

```bash
cd task3 && uv run pytest tests/extract_agent/test_slicer.py -v
```

**Step 3: Implement**

Port the slice/longest-body logic from `task3/scripts/extract/320193-000032019323000106.py:128-191`. Records have `part`, `item_number`, `item_title`, `content_text`, `char_range`. **No `status` field** — that's added later by `classify_statuses`. Keep status absent so downstream tools can detect "pre-classify" vs "post-classify".

```python
# task3/src/extract_agent/slicer.py
import re
from .anchors import ITEM_TO_PART

TRAILING_PAGENUM_RE = re.compile(r"\n+\s*\d{1,4}\s*$")

def _slice_body(text: str, heading_match_end: int, next_start: int) -> tuple[int, int]:
    nl = text.find("\n", heading_match_end)
    if nl == -1 or nl > next_start:
        body_start = heading_match_end
    else:
        body_start = nl + 1
    return body_start, next_start

def slice_items(text: str, anchors: list[dict]) -> list[dict]:
    item_starts = sorted(a["match_start"] for a in anchors)
    by_item: dict[str, list[dict]] = {}
    for a in anchors:
        by_item.setdefault(a["item_number"], []).append(a)

    chosen: list[dict] = []
    for item_id, alist in by_item.items():
        scored = []
        for a in alist:
            idx = item_starts.index(a["match_start"])
            next_start = item_starts[idx + 1] if idx + 1 < len(item_starts) else len(text)
            body_start, body_end = _slice_body(text, a["match_end"], next_start)
            scored.append((body_end - body_start, a, body_start, body_end))
        scored.sort(key=lambda t: t[0], reverse=True)
        _, a, body_start, body_end = scored[0]

        title = a["title"]
        if not title:
            tail = text[body_start: body_start + 200]
            for line in tail.split("\n"):
                line = line.strip()
                if line:
                    title = line
                    break

        body = text[body_start:body_end]
        if len(body) < 500:
            body = TRAILING_PAGENUM_RE.sub("", body).rstrip()
            body_end = body_start + len(body)

        chosen.append({
            "part": ITEM_TO_PART[item_id],
            "item_number": item_id,
            "item_title": title,
            "content_text": body,
            "char_range": [body_start, body_end],
        })
    chosen.sort(key=lambda r: r["char_range"][0])
    return chosen
```

**Step 4: Run tests until green**

```bash
cd task3 && uv run pytest tests/extract_agent/test_slicer.py -v
```

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2
git add task3/src/extract_agent/slicer.py task3/tests/extract_agent/test_slicer.py
git commit -m "feat(task3): port item slicer with longest-body + pagenum trim"
```

---

## Task 5: Default rule-based status classifier (TDD)

For records the LLM never asks to re-classify, fall back to the same rule classifier all per-filing scripts use today. The big model can override on a per-record basis.

**Files:**
- Create: `task3/src/extract_agent/default_classify.py`
- Test: `task3/tests/extract_agent/test_default_classify.py`
- Reference: `task3/scripts/extract/320193-000032019323000106.py:99-125`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_default_classify.py
from extract_agent.default_classify import classify_default

def test_short_ibr_phrase_in_head_classified_as_ibr():
    body = "The information required by this Item is incorporated by reference to the Proxy Statement."
    assert classify_default("Item 11", body) == "incorporated_by_reference"

def test_ibr_with_intervening_words():
    body = "Information is incorporated herein by reference to the 2023 Proxy."
    assert classify_default("Item 11", body) == "incorporated_by_reference"

def test_long_body_with_ibr_phrase_stays_extracted():
    body = "incorporated by reference. " + ("X" * 5000)
    assert classify_default("Item 7", body) == "extracted"

def test_reserved_title_classified():
    assert classify_default("[Reserved]", "") == "reserved"
    assert classify_default("Reserved", "Reserved.") == "reserved"

def test_not_applicable_short_body():
    assert classify_default("Mine Safety Disclosures", "Not applicable.") == "not_applicable"

def test_none_body_classified_as_not_applicable():
    assert classify_default("Item 4", "None.") == "not_applicable"
    assert classify_default("Item 4", "N/A") == "not_applicable"

def test_default_extracted():
    body = "We are a technology company. " * 50
    assert classify_default("Business", body) == "extracted"

def test_internal_xref_stays_extracted():
    # Per CLAUDE.md feedback: "Refer to Item N" is internal cross-ref, NOT IBR.
    assert classify_default("Item 11", "Refer to Item 10.") == "extracted"
    assert classify_default("Item 7", "Refer to Note 30.") == "extracted"
```

**Step 2: Run, expect ImportError**

```bash
cd task3 && uv run pytest tests/extract_agent/test_default_classify.py -v
```

**Step 3: Implement**

Port from per-filing script's `classify`. The signature is `classify_default(title: str, body: str) -> str`. Returns one of `"extracted" | "incorporated_by_reference" | "not_applicable" | "reserved"`. Note the classifier must handle the case where `body` is empty (very short Reserved items).

**Step 4: Run tests until green.**

**Step 5: Commit**

```bash
cd /home/pgi/v_coding_test2
git add task3/src/extract_agent/default_classify.py task3/tests/extract_agent/test_default_classify.py
git commit -m "feat(task3): port default rule-based status classifier"
```

---

## Task 6: Wrap `_validate.py` and `_merge_5b.py` as library imports

The existing scripts already work; we just need them importable from `extract_agent`. Create thin re-exports rather than copying.

**Files:**
- Create: `task3/src/extract_agent/validate.py`
- Create: `task3/src/extract_agent/merge_5b.py`
- Test: `task3/tests/extract_agent/test_validate_wrapper.py`
- Test: `task3/tests/extract_agent/test_merge_5b_wrapper.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_validate_wrapper.py
from extract_agent.validate import validate, summary_lines

def test_validate_flags_overlapping_records():
    records = [
        {"part": "I", "item_number": "1", "item_title": "B", "content_text": "x"*100, "char_range": [0, 100], "status": "extracted"},
        {"part": "I", "item_number": "1", "item_title": "B", "content_text": "y"*100, "char_range": [50, 200], "status": "extracted"},
    ]
    findings = validate(records)
    assert any("overlap" in f for f in findings)

def test_summary_lines_formats_per_record():
    records = [
        {"part": "I", "item_number": "1", "item_title": "Business", "content_text": "x"*100, "char_range": [0, 100], "status": "extracted"},
    ]
    lines = summary_lines(records)
    assert lines == ["Part I Item 1 [extracted] -- Business (100 chars)"]
```

```python
# task3/tests/extract_agent/test_merge_5b_wrapper.py
from extract_agent.merge_5b import merge_records

def test_passes_through_records_without_segments():
    records = [
        {"part": "I", "item_number": "1", "item_title": "B", "content_text": "abc", "char_range": [0, 3], "status": "extracted"},
    ]
    new, rejections = merge_records(records, {})
    assert new == records
    assert rejections == []

def test_splits_record_with_two_segments():
    body = "First sentence. Second sentence."
    records = [
        {"part": "I", "item_number": "10", "item_title": "X", "content_text": body, "char_range": [100, 100 + len(body)], "status": "extracted"},
    ]
    segments = {0: [
        {"status": "extracted", "starts_with": "First sentence", "ends_with": "."},
        {"status": "incorporated_by_reference", "starts_with": "Second sentence", "ends_with": "."},
    ]}
    new, rejections = merge_records(records, segments)
    assert rejections == []
    assert len(new) == 2
    assert new[0]["status"] == "extracted"
    assert new[1]["status"] == "incorporated_by_reference"
```

**Step 2: Run, expect ImportError**

**Step 3: Implement**

```python
# task3/src/extract_agent/validate.py
"""Library re-export of task3/scripts/extract/_validate.py."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[3] / "scripts" / "extract" / "_validate.py"
_spec = importlib.util.spec_from_file_location("_validate", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate = _mod.validate
summary_lines = _mod.summary_lines
inspect_item = _mod.inspect_item
compare_golden = _mod.compare_golden
```

```python
# task3/src/extract_agent/merge_5b.py
"""Library re-export of task3/scripts/extract/_merge_5b.py."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[3] / "scripts" / "extract" / "_merge_5b.py"
_spec = importlib.util.spec_from_file_location("_merge_5b", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

merge_records = _mod.merge_records
merge_segments = _mod.merge_segments
fold = _mod.fold
```

**Step 4: Run tests until green.**

**Step 5: Commit**

```bash
git add task3/src/extract_agent/validate.py task3/src/extract_agent/merge_5b.py task3/tests/extract_agent/test_validate_wrapper.py task3/tests/extract_agent/test_merge_5b_wrapper.py
git commit -m "feat(task3): wrap _validate / _merge_5b as importable library"
```

---

## Task 7: Port LLM client from task2 (TDD with `pytest-httpx`)

Lift `task2/src/agent/llm.py` verbatim to `task3/src/extract_agent/llm.py`. The shape is correct; only the model strings differ.

**Files:**
- Create: `task3/src/extract_agent/llm.py`
- Test: `task3/tests/extract_agent/test_llm.py`
- Reference: `task2/src/agent/llm.py` (lift verbatim, change nothing)

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_llm.py
import pytest
from pytest_httpx import HTTPXMock
from extract_agent.llm import LLMClient, ToolNameNotAllowed

@pytest.mark.asyncio
async def test_chat_sends_correct_payload(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.example.com/chat/completions",
        json={"choices": [{"message": {"role": "assistant", "content": "hi"}}],
              "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}},
    )
    client = LLMClient(base_url="https://api.example.com", model="x", api_key="k")
    msg, usage = await client.chat([{"role": "user", "content": "hello"}])
    assert msg["content"] == "hi"
    assert usage["total_tokens"] == 6
    req = httpx_mock.get_requests()[0]
    body = req.read().decode()
    assert '"model":"x"' in body or '"model": "x"' in body
    assert "Bearer k" in req.headers["Authorization"]
    await client.aclose()

@pytest.mark.asyncio
async def test_tool_name_not_allowed_raises(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.example.com/chat/completions",
        json={"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "bogus", "arguments": "{}"}}
        ]}}]},
    )
    client = LLMClient(base_url="https://api.example.com", model="x", api_key=None)
    with pytest.raises(ToolNameNotAllowed):
        await client.chat([], tools=[{"type": "function", "function": {"name": "real", "parameters": {}}}])
    await client.aclose()
```

Add to `task3/pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

**Step 2: Run, expect ImportError.**

**Step 3: Implement** — copy `task2/src/agent/llm.py` to `task3/src/extract_agent/llm.py` unchanged.

**Step 4: Run tests until green.**

**Step 5: Commit**

```bash
git add task3/src/extract_agent/llm.py task3/tests/extract_agent/test_llm.py task3/pyproject.toml
git commit -m "feat(task3): port LLM client from task2 with tool-name guard"
```

---

## Task 8: Config (TDD)

**Files:**
- Create: `task3/src/extract_agent/config.py`
- Test: `task3/tests/extract_agent/test_config.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_config.py
import os
import pytest
from extract_agent.config import Config

def test_defaults_when_env_unset(monkeypatch):
    for k in ("AGENT_MODEL_BASE_URL", "AGENT_MODEL_BIG", "AGENT_MODEL_SMALL",
              "DEEPSEEK_API_KEY", "MAX_STEPS", "COST_CEILING_USD"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.big_model == "deepseek-v4-pro"
    assert cfg.small_model == "deepseek-v4-flash"
    assert cfg.api_key is None
    assert cfg.max_steps == 30
    assert cfg.cost_ceiling_usd == 0.50

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_BIG", "custom-big")
    monkeypatch.setenv("AGENT_MODEL_SMALL", "custom-small")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("MAX_STEPS", "10")
    monkeypatch.setenv("COST_CEILING_USD", "1.50")
    cfg = Config.from_env()
    assert cfg.big_model == "custom-big"
    assert cfg.small_model == "custom-small"
    assert cfg.api_key == "secret"
    assert cfg.max_steps == 10
    assert cfg.cost_ceiling_usd == 1.50
```

**Step 2–4: Implement and verify.**

```python
# task3/src/extract_agent/config.py
from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    base_url: str
    big_model: str
    small_model: str
    api_key: str | None
    max_steps: int
    cost_ceiling_usd: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.getenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com"),
            big_model=os.getenv("AGENT_MODEL_BIG", "deepseek-v4-pro"),
            small_model=os.getenv("AGENT_MODEL_SMALL", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            max_steps=int(os.getenv("MAX_STEPS", "30")),
            cost_ceiling_usd=float(os.getenv("COST_CEILING_USD", "0.50")),
        )
```

**Step 5: Commit**

```bash
git add task3/src/extract_agent/config.py task3/tests/extract_agent/test_config.py
git commit -m "feat(task3): config with big/small model env vars and budget caps"
```

---

## Task 9: Session state container (TDD)

A typed dict-like object the loop and tools share. Each tool reads/writes via this object; the LLM never sees its internals.

**Files:**
- Create: `task3/src/extract_agent/state.py`
- Test: `task3/tests/extract_agent/test_state.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_state.py
from extract_agent.state import SessionState

def test_text_store_round_trip():
    s = SessionState()
    tid = s.store_text("hello world")
    assert s.get_text(tid) == "hello world"

def test_text_ids_are_unique():
    s = SessionState()
    a = s.store_text("a")
    b = s.store_text("b")
    assert a != b

def test_records_default_none():
    s = SessionState()
    assert s.records is None

def test_cost_accumulates():
    s = SessionState()
    s.add_cost(0.10)
    s.add_cost(0.05)
    assert s.cost_usd == 0.15

def test_step_counter():
    s = SessionState()
    assert s.steps == 0
    s.bump_step()
    s.bump_step()
    assert s.steps == 2
```

**Step 2–4:** Implement in `state.py` with attributes `text_store: dict[str, str]`, `anchors`, `records`, `diagnostic`, `cost_usd`, `steps`, `inputs`, `store_text(s) -> id`, `get_text(id) -> str`, `add_cost(usd)`, `bump_step()`.

**Step 5: Commit**

```bash
git add task3/src/extract_agent/state.py task3/tests/extract_agent/test_state.py
git commit -m "feat(task3): session state container shared by loop and tools"
```

---

## Task 10: Tool registry + first deterministic tool (`clean_and_load`) (TDD)

Each tool is a module under `extract_agent/tools/` exporting `SCHEMA: dict` (OpenAI tool schema) and `def run(state, args) -> dict`. The registry maps tool name → module.

**Files:**
- Create: `task3/src/extract_agent/tools/__init__.py`
- Create: `task3/src/extract_agent/tools/clean_and_load.py`
- Test: `task3/tests/extract_agent/test_tool_clean_and_load.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_tool_clean_and_load.py
from pathlib import Path
from extract_agent.state import SessionState
from extract_agent.tools import clean_and_load
from extract_agent.tools import REGISTRY

def test_registered():
    assert "clean_and_load" in REGISTRY
    assert REGISTRY["clean_and_load"].SCHEMA["function"]["name"] == "clean_and_load"

def test_run_loads_html_and_returns_text_id(tmp_path: Path):
    p = tmp_path / "f.html"
    p.write_text("<p>Hello</p><p>World</p>")
    state = SessionState()
    result = clean_and_load.run(state, {"html_path": str(p)})
    assert "text_id" in result
    assert result["length"] > 0
    text = state.get_text(result["text_id"])
    assert "Hello" in text and "World" in text

def test_run_applies_extra_strip_patterns(tmp_path: Path):
    p = tmp_path / "f.html"
    p.write_text("<p>Apple Inc. | 2023 Form 10-K | 17</p><p>Body</p>")
    state = SessionState()
    result = clean_and_load.run(
        state,
        {"html_path": str(p), "extra_strip_patterns": [r"\nApple Inc\.\s*\|\s*2023 Form 10-K\s*\|\s*\d+\s*\n"]},
    )
    text = state.get_text(result["text_id"])
    assert "Form 10-K" not in text
```

**Step 2: Run, expect ImportError.**

**Step 3: Implement**

```python
# task3/src/extract_agent/tools/clean_and_load.py
from pathlib import Path
from ..cleaner import clean_html

SCHEMA = {
    "type": "function",
    "function": {
        "name": "clean_and_load",
        "description": "Read an HTML 10-K from disk, clean it to plain text, and store the text in session state. Returns a text_id that downstream tools (find_anchors, slice_items, read_chars) use to refer to this cleaned text.",
        "parameters": {
            "type": "object",
            "properties": {
                "html_path": {"type": "string", "description": "Absolute path to the HTML file."},
                "extra_strip_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of regex patterns to strip after default cleanup. Use this to remove recurring page-running footers (e.g. 'Apple Inc. | 2023 Form 10-K | 17').",
                },
            },
            "required": ["html_path"],
        },
    },
}

def run(state, args: dict) -> dict:
    raw = Path(args["html_path"]).read_text(encoding="utf-8", errors="replace")
    text = clean_html(raw, extra_strip_patterns=args.get("extra_strip_patterns"))
    text_id = state.store_text(text)
    return {"text_id": text_id, "length": len(text), "n_lines": text.count("\n") + 1}
```

```python
# task3/src/extract_agent/tools/__init__.py
from . import clean_and_load
REGISTRY = {
    "clean_and_load": clean_and_load,
}

def schemas() -> list[dict]:
    return [m.SCHEMA for m in REGISTRY.values()]
```

**Step 4: Verify, Step 5: Commit**

```bash
git add task3/src/extract_agent/tools/__init__.py task3/src/extract_agent/tools/clean_and_load.py task3/tests/extract_agent/test_tool_clean_and_load.py
git commit -m "feat(task3): tool registry + clean_and_load tool"
```

---

## Task 11: Add `find_anchors`, `slice_items`, `validate_records`, `write_output`, `done` tools

Each follows the same shape as `clean_and_load`. One commit per tool, TDD each.

**For each of the 5 tools:**

1. Write a failing test (~3 assertions: registered, schema correct, `run` produces expected output / state mutation).
2. Implement the tool module with `SCHEMA` + `run(state, args)`. Wrap the corresponding deterministic helper from `extract_agent.{anchors,slicer,validate}`.
3. Add the module to `tools/__init__.py:REGISTRY`.
4. Run tests until green.
5. Commit one tool at a time: `feat(task3): tool find_anchors`, etc.

**Tool sketches (signatures only; flesh out the schemas to match):**

```python
# tools/find_anchors.py
SCHEMA = {"type": "function", "function": {"name": "find_anchors", "description": "Run ITEM_RE over the cleaned text and dedupe TOC vs body anchors. Returns the deduped anchor list, with each anchor's match_start, match_end, item_number, and title.", "parameters": {"type": "object", "properties": {"text_id": {"type": "string"}, "regex": {"type": "string", "description": "Optional override regex (Python re syntax). Defaults to the canonical ITEM_RE."}, "toc_threshold": {"type": "integer", "description": "Char offset; anchors at or below this offset are treated as TOC stubs. Defaults to 8000."}}, "required": ["text_id"]}}}
def run(state, args):
    text = state.get_text(args["text_id"])
    rx = re.compile(args["regex"], ...) if args.get("regex") else None
    anchors = find_anchors(text, regex=rx)
    deduped = dedupe_anchors(anchors, toc_region_end=args.get("toc_threshold", 8000))
    state.anchors = deduped
    return {"count": len(deduped), "by_item": {a["item_number"]: a["match_start"] for a in deduped}}
```

```python
# tools/slice_items.py
SCHEMA = {...}
def run(state, args):
    text = state.get_text(args["text_id"])
    anchors = state.anchors  # populated by find_anchors
    if not anchors:
        return {"error": "no anchors in state; call find_anchors first"}
    records = slice_items(text, anchors)
    # Apply default classifier to each record so records always have a status.
    for r in records:
        r["status"] = classify_default(r["item_title"], r["content_text"])
    state.records = records
    return {"count": len(records), "summary": [f"Item {r['item_number']} [{r['status']}] ({len(r['content_text'])} chars)" for r in records]}
```

```python
# tools/validate_records.py
def run(state, args):
    if state.records is None:
        return {"error": "no records in state"}
    findings = validate(state.records)
    return {"findings": findings, "ok": not findings}
```

```python
# tools/write_output.py
def run(state, args):
    if state.records is None:
        return {"error": "no records in state"}
    out = Path(args["json_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state.records, ensure_ascii=False, indent=2))
    return {"path": str(out), "count": len(state.records)}
```

```python
# tools/done.py
SCHEMA = {"type": "function", "function": {"name": "done", "description": "Signal that extraction is complete. The orchestration loop exits after this call.", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": []}}}
def run(state, args):
    return {"done": True, "message": args.get("message", "")}
```

**Tests for each** verify: tool registered in REGISTRY, schema name matches, `run` mutates `state` correctly (where applicable), error cases return `{"error": ...}` rather than raising.

**Commits:** one per tool, message `feat(task3): tool <name>`.

---

## Task 12: Escape-hatch tools (`read_chars`, `regex_search`, `inspect_record`, `update_record`)

Same TDD pattern, one commit per tool.

**`read_chars`:** read `text_id[start:end]`, cap return at 8KB. Schema requires `text_id`, `start`, `end`.

**`regex_search`:** compile pattern, run `finditer`, return up to `max_matches` (default 50) `{start, end, groups: [...]}` entries. Errors on invalid regex return `{"error": "invalid regex: ..."}`.

**`inspect_record`:** given record `index`, return `{record: {...}, body_preview: <first 4KB + "...[truncated]..." + last 2KB if longer>, neighbor_above, neighbor_below}`. Lets the big model debug a single record without dumping the full 100KB body into the message log.

**`update_record`:** apply a `patch` (subset of record fields) to `state.records[index]`. Recomputes `content_text` if the caller supplies new `char_range`. Used for edge cases like GE-2018 where the big model decides to override slicing manually.

Each test verifies registry membership, schema, success, and at least one error path.

**Commits:** `feat(task3): escape-hatch tool <name>` × 4.

---

## Task 13: `classify_statuses` tool with small-model fan-out (TDD)

This is the only tool that itself calls the LLM. It applies the eligibility filter from the skill (Phase 5b prose), fans out to the small model in parallel, and merges via `merge_5b.merge_records`.

**Files:**
- Create: `task3/src/extract_agent/classify.py` (the fan-out logic)
- Create: `task3/src/extract_agent/tools/classify_statuses.py` (the tool wrapper)
- Create: `task3/src/extract_agent/prompts/system_small.md` (the per-call system prompt; lift from `.claude/skills/10k-extraction/phase5b-status-split.md`)
- Test: `task3/tests/extract_agent/test_classify.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_classify.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from extract_agent.classify import eligible_indices, classify_records

def test_eligibility_skips_short_bodies():
    records = [
        {"item_number": "4", "content_text": "Not applicable.", "status": "not_applicable"},
        {"item_number": "1", "content_text": "x" * 500 + " incorporated by reference " + "x" * 500, "status": "extracted"},
        {"item_number": "10", "content_text": "x" * 500, "status": "extracted"},  # no IBR phrase
    ]
    assert eligible_indices(records) == [1]

def test_eligibility_skips_huge_item_15():
    records = [
        {"item_number": "15", "content_text": ("x incorporated by reference " * 30000), "status": "extracted"},
    ]
    assert eligible_indices(records) == []

@pytest.mark.asyncio
async def test_classify_records_dispatches_per_eligible():
    records = [
        {"part": "I", "item_number": "11", "item_title": "X", "char_range": [0, 100], "status": "extracted",
         "content_text": "Body. " * 30 + "incorporated by reference. " + "Trailing. " * 20},
    ]
    fake_segments = [
        {"status": "extracted", "starts_with": "Body."},
        {"status": "incorporated_by_reference", "starts_with": "incorporated by reference"},
        {"status": "extracted", "starts_with": "Trailing."},
    ]
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(return_value=({"role": "assistant", "content": '{"segments": ' + str(fake_segments).replace("'", '"') + '}'}, None))
    new_records, rejections, calls = await classify_records(records, client=fake_client, model="x")
    assert calls == 1
    assert len(new_records) == 3
```

**Step 2: Run, expect ImportError.**

**Step 3: Implement** in `classify.py`:

- `eligible_indices(records)` — apply skill's filter: `status == "extracted"`, `len(body) >= 200`, NOT (item==`15` AND len>=500_000), AND `re.search(r"incorporat\w*(?:\s+\w+){0,8}\s+by\s+reference", body, re.IGNORECASE)`.
- `async def classify_records(records, client, model)` — for each eligible index, build a chat with the small system prompt, send via `client.chat`. Parse the JSON `{segments: [...]}` from `message["content"]`. Pass results dict to `merge_records`. Run all chats with `asyncio.gather`. Return `(new_records, rejections, n_calls)`.

`tools/classify_statuses.py`:

```python
SCHEMA = {"type": "function", "function": {"name": "classify_statuses", "description": "Run Phase 5b status splitting: for each record whose body looks like it might mix extracted prose with incorporated-by-reference sentences, fan out a small-model classification call. Replaces eligible records with sub-records (one per status segment). Records that don't meet the eligibility filter are passed through unchanged.", "parameters": {"type": "object", "properties": {}}}}

async def run(state, args, *, client, model):
    if state.records is None:
        return {"error": "no records in state"}
    new_records, rejections, n_calls = await classify_records(state.records, client=client, model=model)
    state.records = new_records
    return {"calls": n_calls, "new_record_count": len(new_records), "rejections": rejections}
```

(`run` for this tool takes extra kwargs the dispatcher passes — see Task 14.)

`prompts/system_small.md`: lift verbatim from `.claude/skills/10k-extraction/phase5b-status-split.md`, removing the "Read this file" wrapper.

**Step 4: Run tests until green.**

**Step 5: Commit**

```bash
git add task3/src/extract_agent/classify.py task3/src/extract_agent/tools/classify_statuses.py task3/src/extract_agent/prompts/system_small.md task3/tests/extract_agent/test_classify.py
git commit -m "feat(task3): classify_statuses tool with small-model fan-out"
```

---

## Task 14: Big-model orchestration loop (TDD with stub LLM)

The loop runs the tool-calling cycle: call LLM with messages + tool schemas, dispatch any `tool_calls`, append results, recurse. Bounded by `max_steps` and `cost_ceiling_usd`. Exits cleanly when the model calls `done`.

**Files:**
- Create: `task3/src/extract_agent/loop.py`
- Create: `task3/src/extract_agent/prompts/system_big.md`
- Test: `task3/tests/extract_agent/test_loop.py`

**Step 1: Failing tests** — use a stub `LLMClient` that returns scripted tool calls.

```python
# task3/tests/extract_agent/test_loop.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock
from extract_agent.loop import run_loop
from extract_agent.config import Config

class StubLLM:
    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls = []
    async def chat(self, messages, *, tools=None, tool_choice=None, **_):
        self.calls.append((messages[-1].get("role"), len(messages)))
        return self._scripted.pop(0), {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    async def aclose(self): pass

@pytest.mark.asyncio
async def test_loop_happy_path(tmp_path: Path):
    html = tmp_path / "f.html"
    html.write_text("<p>Item 1. Business</p><p>body</p><p>Item 2. Properties</p><p>body2</p>")
    out = tmp_path / "out.json"
    scripted = [
        {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "clean_and_load", "arguments": f'{{"html_path": "{html}"}}'}}]},
        {"role": "assistant", "tool_calls": [{"id": "2", "type": "function", "function": {"name": "find_anchors", "arguments": '{"text_id": "t0"}'}}]},
        {"role": "assistant", "tool_calls": [{"id": "3", "type": "function", "function": {"name": "slice_items", "arguments": '{"text_id": "t0"}'}}]},
        {"role": "assistant", "tool_calls": [{"id": "4", "type": "function", "function": {"name": "validate_records", "arguments": "{}"}}]},
        {"role": "assistant", "tool_calls": [{"id": "5", "type": "function", "function": {"name": "write_output", "arguments": f'{{"json_path": "{out}"}}'}}]},
        {"role": "assistant", "tool_calls": [{"id": "6", "type": "function", "function": {"name": "done", "arguments": '{"message": "ok"}'}}]},
    ]
    cfg = Config.from_env()
    big = StubLLM(scripted)
    small = StubLLM([])
    result = await run_loop(html_path=str(html), out_path=str(out), cfg=cfg, big=big, small=small)
    assert result["status"] == "done"
    assert out.exists()

@pytest.mark.asyncio
async def test_loop_max_steps(tmp_path: Path):
    # Scripted to always call clean_and_load, never done — loop must terminate at max_steps.
    html = tmp_path / "f.html"
    html.write_text("<p>x</p>")
    scripted = [
        {"role": "assistant", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "clean_and_load", "arguments": f'{{"html_path": "{html}"}}'}}]}
        for i in range(50)
    ]
    cfg = Config.from_env()
    object.__setattr__(cfg, "max_steps", 3)  # frozen dataclass; bypass for test
    big = StubLLM(scripted)
    small = StubLLM([])
    out = tmp_path / "out.json"
    result = await run_loop(html_path=str(html), out_path=str(out), cfg=cfg, big=big, small=small)
    assert result["status"] == "max_steps_exceeded"
```

(The frozen-dataclass override is hacky for the test — alternative: make `max_steps` a kwarg of `run_loop` defaulting to `cfg.max_steps`.)

**Step 2: Run, expect ImportError.**

**Step 3: Implement** `loop.py`:

```python
async def run_loop(*, html_path, out_path, cfg, big, small) -> dict:
    state = SessionState()
    state.inputs = {"html_path": html_path, "out_path": out_path}
    system = (Path(__file__).parent / "prompts" / "system_big.md").read_text()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Extract per-item JSON from {html_path}. Write the result to {out_path}. Use the tools."},
    ]
    schemas = tools.schemas()
    while state.steps < cfg.max_steps:
        if state.cost_usd >= cfg.cost_ceiling_usd:
            return {"status": "cost_exceeded", "state": state}
        msg, usage = await big.chat(messages, tools=schemas, reasoning=True)
        if usage:
            state.add_cost(_estimate_cost(usage, cfg.big_model))  # see helper
        messages.append(msg)
        if not msg.get("tool_calls"):
            # Model said something without calling a tool; treat as done? Or loop.
            # Per design, model must call done() to exit. If it doesn't tool-call,
            # nudge it: append a user message reminding it to use tools.
            messages.append({"role": "user", "content": "Use the tools to make progress, or call done() if extraction is complete."})
            state.bump_step()
            continue
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            mod = tools.REGISTRY[name]
            if name == "classify_statuses":
                result = await mod.run(state, args, client=small, model=cfg.small_model)
            else:
                result = mod.run(state, args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
            if name == "done":
                return {"status": "done", "state": state}
        state.bump_step()
    return {"status": "max_steps_exceeded", "state": state}
```

`prompts/system_big.md`: write a system prompt that explains:

- The schema (per-item record shape).
- The canonical happy path: `clean_and_load → find_anchors → slice_items → classify_statuses → validate_records → write_output → done`.
- When to use escape hatches: zero anchors, validation flags monotonicity errors, item bodies obviously truncated, GE-2018-style index-page filings.
- The 16 expected items; that not all are required (some filings stop at 15).
- The status taxonomy (extracted / incorporated_by_reference / not_applicable / reserved) and the rule that internal cross-refs ("Refer to Item 10") stay `extracted`.

Lift content from existing skill prose where possible.

**Cost helper:** `_estimate_cost(usage, model)` — DeepSeek prices per million tokens (cache miss):
- `deepseek-v4-flash`: input $0.14, output $0.28
- `deepseek-v4-pro`: input $0.435, output $0.87

**Step 4: Run tests until green.**

**Step 5: Commit**

```bash
git add task3/src/extract_agent/loop.py task3/src/extract_agent/prompts/system_big.md task3/tests/extract_agent/test_loop.py
git commit -m "feat(task3): orchestration loop with tool dispatch and budget"
```

---

## Task 15: Queue (`test_source.md`) read + tick (TDD)

**Files:**
- Create: `task3/src/extract_agent/queue.py`
- Test: `task3/tests/extract_agent/test_queue.py`

**Step 1: Failing tests**

```python
# task3/tests/extract_agent/test_queue.py
from pathlib import Path
from extract_agent.queue import next_pending, mark_done

QUEUE = """\
# Test queue
| CIK | Accession | Path | Done |
|---|---|---|---|
| 320193 | 000032019323000106 | data/raw/archive/320193/.../aapl.htm | [x] |
| 1067983 | 000119312526083899 | data/raw/archive/1067983/.../brk.htm | [ ] |
| 789019 | 000156459020034944 | data/raw/archive/789019/.../msft.htm | [ ] |
"""

def test_next_pending_returns_first_unchecked(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE)
    row = next_pending(p)
    assert row["cik"] == "1067983"
    assert row["accession"] == "000119312526083899"
    assert row["path"].endswith("brk.htm")

def test_mark_done_flips_box(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE)
    mark_done(p, cik="1067983", accession="000119312526083899")
    text = p.read_text()
    assert text.count("[x]") == 2
    assert text.count("[ ]") == 1

def test_next_pending_returns_none_when_all_done(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE.replace("[ ]", "[x]"))
    assert next_pending(p) is None
```

**Step 2–4:** Implement parser (regex over `| CIK | Accession | Path | [ ]/[x] |`).

**Step 5: Commit**

```bash
git add task3/src/extract_agent/queue.py task3/tests/extract_agent/test_queue.py
git commit -m "feat(task3): test_source.md queue read + tick"
```

---

## Task 16: CLI / `__main__` (TDD via subprocess)

**Files:**
- Create: `task3/src/extract_agent/__main__.py`
- Test: `task3/tests/extract_agent/test_cli.py`

**Step 1: Failing tests** — subprocess-based, mocking the LLM via `MOCK_LLM=1` env var that swaps in a stub.

```python
# task3/tests/extract_agent/test_cli.py
import subprocess
from pathlib import Path

def test_cli_help():
    r = subprocess.run(["uv", "run", "python", "-m", "extract_agent", "--help"],
                       cwd="/home/pgi/v_coding_test2/task3", capture_output=True, text=True)
    assert r.returncode == 0
    assert "--cik" in r.stdout
    assert "--queue" in r.stdout
    assert "--out" in r.stdout
```

(End-to-end CLI tests with real LLM live in the eval suite, not in unit tests.)

**Step 2–4:** Argparse with three modes (positional `html_path` + `--out`; `--cik` + `--accession`; `--queue` [+ `--all`]). Resolve CIK+accession via `task3/data/index.json`. On `--queue`, call `queue.next_pending`, run, on success call `queue.mark_done`. Print stdout summary using `summary_lines(state.records)` followed by the `items=N parts=[...]` line and `cost=$X.XX steps=N`.

**Step 5: Commit**

```bash
git add task3/src/extract_agent/__main__.py task3/tests/extract_agent/test_cli.py
git commit -m "feat(task3): CLI with html_path / --cik+--accession / --queue modes"
```

---

## Task 17: Move legacy per-filing scripts

**Files:**
- Move: `task3/scripts/extract/{cik}-{accession}.py` (8 files) → `task3/scripts/extract_legacy/`
- Keep in place: `_validate.py`, `_merge_5b.py`, `_probe.py`

**Step 1:** Verify the 8 scripts are no longer referenced elsewhere:

```bash
cd /home/pgi/v_coding_test2
grep -rn "scripts/extract/[0-9]" --include='*.py' --include='*.md' --include='*.sh'
```

Expected: matches only inside the script files themselves and inside `clean.sh` (which we update in Task 18).

**Step 2:** Move with `git mv`:

```bash
cd /home/pgi/v_coding_test2/task3
mkdir -p scripts/extract_legacy
git mv scripts/extract/1045810-000104581024000029.py scripts/extract_legacy/
git mv scripts/extract/1067983-000119312526083899.py scripts/extract_legacy/
git mv scripts/extract/1321655-000132165526000011.py scripts/extract_legacy/
git mv scripts/extract/19617-000162828026008131.py scripts/extract_legacy/
git mv scripts/extract/320193-000032019323000106.py scripts/extract_legacy/
git mv scripts/extract/40545-000004054519000014.py scripts/extract_legacy/
git mv scripts/extract/789019-000095017023035122.py scripts/extract_legacy/
git mv scripts/extract/789019-000156459020034944.py scripts/extract_legacy/
```

**Step 3:** Add a one-line README to `scripts/extract_legacy/` explaining what these are (frozen reference corpus; their JSON outputs in `data/extracted/` are ground truth for the agent's regression).

**Step 4:** `uv run pytest` — must still pass (the legacy scripts were not imported by any test).

**Step 5: Commit**

```bash
git commit -m "refactor(task3): move legacy per-filing scripts to extract_legacy/"
```

---

## Task 18: Eval / regression CLI

**Files:**
- Create: `task3/eval/regression.py`
- Test: smoke test only — full eval is run-on-demand, not in CI.

**Step 1: Write the eval CLI**

```python
# task3/eval/regression.py
"""Run extract_agent against each filing whose legacy JSON exists in
data/extracted/, diff per-item layout, and report drift.

Usage:
  uv run python eval/regression.py             # all 8 filings
  uv run python eval/regression.py --cik 320193  # one filing
"""
```

For each filing:
1. Look up its HTML path via `data/index.json` from CIK+accession (parsed from the JSON filename).
2. Run the agent: `await run_loop(html_path=..., out_path=tmp.json, ...)`.
3. Diff: per item_number, compare item count, status, char_range Hausdorff distance.
4. Soft-fail on small drift (<5% body length change, status unchanged); hard-fail on missing items or status flips.
5. Print summary table at the end.

**Step 2: Smoke test**

```python
# task3/tests/extract_agent/test_eval_smoke.py
import subprocess
def test_eval_help():
    r = subprocess.run(["uv", "run", "python", "eval/regression.py", "--help"],
                       cwd="/home/pgi/v_coding_test2/task3", capture_output=True, text=True)
    assert r.returncode == 0
```

**Step 3: Commit**

```bash
git add task3/eval/regression.py task3/tests/extract_agent/test_eval_smoke.py
git commit -m "feat(task3): regression eval CLI vs legacy JSON ground truth"
```

**Step 4: Run the eval and tune** — this is the calibration step. Expect drift; investigate and fix the agent (typically by tightening the system prompt or fixing a deterministic helper). Each fix is its own commit (`fix(task3): ...`).

---

## Task 19: Wire CLI into `clean.sh` and update README

**Files:**
- Modify: `task3/clean.sh` (untracked file in current working tree — read it first, then edit to call the agent CLI instead of per-filing scripts)
- Modify: `task3/README.md`

**Step 1: Read current `clean.sh`** (it's untracked per `git status`, so it exists in the working tree).

**Step 2:** Replace per-filing-script invocations with `uv run python -m extract_agent --queue --all` (drains the queue) or whatever loop matches the existing intent.

**Step 3:** Update `task3/README.md`:
- Add `extract_agent` section: how to run, env vars (`DEEPSEEK_API_KEY`, `AGENT_MODEL_BIG`, `AGENT_MODEL_SMALL`, `MAX_STEPS`, `COST_CEILING_USD`), CLI modes.
- Note that legacy per-filing scripts moved to `scripts/extract_legacy/` and are kept as the regression baseline.

**Step 4:** Run the existing test suite end-to-end:

```bash
cd /home/pgi/v_coding_test2/task3 && uv run pytest -v && uv run ruff check . && uv run ruff format --check .
```

All green.

**Step 5: Commit**

```bash
git add task3/clean.sh task3/README.md
git commit -m "doc(task3): wire extract_agent CLI into clean.sh and README"
```

---

## Task 20: Update the `10k-extraction` skill prose

The skill stays for now but becomes a thin shim over the agent.

**Files:**
- Modify: `.claude/skills/10k-extraction/SKILL.md`

**Step 1:** Replace Phases 4 (author script) and 5 (run script) with a single phase that runs `uv run python -m extract_agent --cik X --accession Y --out ...`. Phases 1, 2, 7, 8, 9 stay (they're orchestration around the run, not the run itself).

**Step 2:** Mark Phases 3 (diagnostic) and 5b (status splitting) and 6 (validation) as "implemented inside the agent — see `task3/src/extract_agent/tools/`". Keep the eligibility filter prose because it's load-bearing if anyone reads `phase5b-status-split.md`.

**Step 3:** Add a one-paragraph note explaining when to fall back to the legacy per-filing scripts (`scripts/extract_legacy/`): when the agent fails repeatedly on a filing whose pattern doesn't fit the hybrid tools, copying a legacy script as a starting point and running it deterministically is still allowed.

**Step 4:** Commit.

```bash
git add .claude/skills/10k-extraction/SKILL.md
git commit -m "doc(skill): 10k-extraction now delegates Phase 4-6 to extract_agent"
```

---

## Task 21: Final sweep

**Step 1:** Run everything:

```bash
cd /home/pgi/v_coding_test2/task3
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

All green.

**Step 2:** Run the regression eval:

```bash
uv run python eval/regression.py
```

Capture the output. If drift exceeds soft thresholds on multiple filings, halt — that's signal the agent's defaults need tuning before merging.

**Step 3:** Make sure `task3/data/extracted/<cik>-<acc>.json` files are unchanged (these are the legacy ground truth). If they did change, that's a bug — either from a new agent run that overwrote them or a stray edit.

**Step 4:** `git log --oneline feature/task3 ^origin/main` — review the commit chain. Each commit should be one focused change with a passing test. Squash only if a commit is plainly broken (no green tests at that point in history).

**Step 5:** Open PR or hand off to `/finishing-a-development-branch`.

---

## Notes for the executor

- **Repo conventions** (CLAUDE.md): `uv` for everything, no manual `pip` / `venv`; commit `uv.lock`; ruff is the only linter. Run `uv run ruff check .` and `uv run ruff format .` before each commit.
- **TDD is non-negotiable** (CLAUDE.md). Red → green → refactor. No "tests after". Bug fixes = regression test first.
- **No `--no-verify`.** If a hook fails, fix the cause.
- **Memory** (auto-memory): user prefers terse responses with no trailing summaries; "Refer to Item N" is internal cross-ref, not IBR; per-filing scripts dedup TOC anchors first then take longest body. These are encoded into the agent's defaults already; do not "fix" them.
- **Cost discipline:** the eval (Task 18) and regression run (Task 21) make real DeepSeek calls. Use the `--small-model` and `--big-model` flags to override to cheaper models during dev if needed. The default $0.50/filing ceiling is a guess; adjust per `cost_ceiling_usd` env var.
- **Don't extend scope.** This plan does not deprecate the skill, does not delete legacy scripts, does not add Anthropic provider support. Each is a separate decision.
