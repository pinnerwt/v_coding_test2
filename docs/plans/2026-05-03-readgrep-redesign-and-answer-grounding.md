# read_grep redesign + done() answer-grounding — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop the agent from looping on `read_grep` no-ops and from emitting ungrounded `done(success, …)` answers — both surfaced in bench session `51bf0da0bb4f41d290e5325644b10404`.

**Architecture:**
- Redesign `read_grep` as a paginated, position-marked match index (not a single-snippet returner).
- Replace pattern-keyed dedup with obs-hash dedup; escalate to hard tool-hide after K=2 consecutive duplicate-output calls.
- Add a required `evidence` field to `done()` that must be a substring of prior tape obs and must contain the answer.

**Tech Stack:** Python 3.11+, `uv`, `pytest-asyncio`, `httpx.MockTransport`, Playwright (for integration).

**Reference design:** `docs/plans/2026-05-03-readgrep-redesign-and-answer-grounding-design.md`

**Branch:** `task2-readgrep-and-evidence-grounding` (already cut from `dev`).

**Working directory for all commands:** `task2/` unless stated otherwise.

---

## Task 1: New `read_grep` — return shape and offset

**Files:**
- Modify: `task2/src/agent/tools/browser.py:115-125` (the `read_grep` function)
- Modify: `task2/src/agent/tools/browser.py:253-266` (the `Tool` schema for `read_grep`)
- Test: `task2/tests/unit/test_browser_read_grep.py` (NEW)

**Step 1: Write the failing tests**

Create `task2/tests/unit/test_browser_read_grep.py`:

```python
import pytest

from agent.tools.browser import build_browser_tools


class _FakePage:
    def __init__(self, text: str):
        self._text = text
        self.url = "https://x.test/"

    async def evaluate(self, script: str):
        return self._text


class _FakeSession:
    def __init__(self, text: str):
        self.page = _FakePage(text)


def _make(text: str):
    return build_browser_tools(_FakeSession(text), restrict_goto=False)["read_grep"]


@pytest.mark.asyncio
async def test_read_grep_no_match_explicit_with_page_length_and_vocab():
    text = "Definite integral:\nStep-by-step solution\nIndefinite integral:\n"
    grep = _make(text)
    obs = await grep(pattern="x = 9")
    assert obs.startswith("NO MATCH")
    assert f"({len(text)} chars)" in obs
    # Vocabulary hint: must surface at least one distinctive page token
    assert "Definite integral" in obs or "Indefinite integral" in obs


@pytest.mark.asyncio
async def test_read_grep_single_match_marks_offset_and_count():
    # "needle" sits at offset 24
    text = "the quick brown fox at needle position\n"
    grep = _make(text)
    obs = await grep(pattern="needle")
    assert obs.startswith("1 match for")
    assert "[@" in obs and "needle" in obs


@pytest.mark.asyncio
async def test_read_grep_many_matches_lists_with_offsets_and_count():
    text = "alpha 2018 one\nbeta 2018 two\ngamma 2018 three\n"
    grep = _make(text)
    obs = await grep(pattern="2018")
    assert "3 matches for" in obs
    # Three distinct [@offset] markers
    assert obs.count("[@") == 3


@pytest.mark.asyncio
async def test_read_grep_caps_at_max_matches_and_indicates_more():
    text = ("XYZ " * 20).strip()  # 20 occurrences of "XYZ"
    grep = _make(text)
    obs = await grep(pattern="XYZ", max_matches=5)
    assert "20 matches for" in obs
    assert "Showing matches 0-4 of 20" in obs
    # Tail summary names the additional offsets and tells the LLM what to call
    assert "more matches" in obs
    assert "offset=5" in obs


@pytest.mark.asyncio
async def test_read_grep_offset_pages_through_matches():
    text = ("XYZ " * 20).strip()
    grep = _make(text)
    obs = await grep(pattern="XYZ", max_matches=5, offset=5)
    assert "Showing matches 5-9 of 20" in obs
    assert obs.count("[@") == 5


@pytest.mark.asyncio
async def test_read_grep_offset_past_end_is_explicit():
    text = ("XYZ " * 3).strip()
    grep = _make(text)
    obs = await grep(pattern="XYZ", offset=10)
    assert obs.startswith("NO MORE MATCHES")
    assert "3 total" in obs


@pytest.mark.asyncio
async def test_read_grep_context_controls_snippet_width_only_not_match_count():
    text = "AAA needle BBB needle CCC needle DDD"
    grep = _make(text)
    narrow = await grep(pattern="needle", context=4)
    wide = await grep(pattern="needle", context=20)
    # Same match count regardless of context
    assert "3 matches for" in narrow and "3 matches for" in wide
    # Wider context produces strictly more total chars
    assert len(wide) > len(narrow)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_browser_read_grep.py -v`
Expected: all 7 tests FAIL — current `read_grep` returns `text[start:end]` and `NOT FOUND: 'X'`, neither of which match the new shape.

**Step 3: Implement the new `read_grep`**

In `task2/src/agent/tools/browser.py`, replace the function at lines 115-125 with:

```python
async def read_grep(
    pattern: str,
    context: int = 80,
    max_matches: int = 10,
    offset: int = 0,
) -> str:
    try:
        text = await session.page.evaluate("document.body.innerText")
    except Exception as e:
        return f"ERROR: {e}"
    n = len(text)
    if not pattern:
        return f"NO MATCH for '' in page text ({n} chars)."
    needle = pattern.lower()
    hay = text.lower()
    positions: list[int] = []
    i = 0
    while True:
        j = hay.find(needle, i)
        if j < 0:
            break
        positions.append(j)
        i = j + max(1, len(needle))
    total = len(positions)
    if total == 0:
        # Page-vocabulary hint: pull a few of the longest distinct lines.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        seen: set[str] = set()
        vocab: list[str] = []
        for ln in sorted(lines, key=len, reverse=True):
            key = ln.lower()
            if key in seen:
                continue
            seen.add(key)
            vocab.append(ln if len(ln) <= 40 else ln[:37] + "…")
            if len(vocab) >= 5:
                break
        if vocab:
            quoted = ", ".join(f'"{v}"' for v in vocab)
            return (
                f"NO MATCH for {pattern!r} in page text ({n} chars). "
                f"The text contains: {quoted} — try one of these, or call done()."
            )
        return f"NO MATCH for {pattern!r} in page text ({n} chars)."
    if offset >= total:
        return (
            f"NO MORE MATCHES for {pattern!r} at offset={offset} "
            f"({total} total in page). Call read_grep with offset=0..{total - 1} or done()."
        )
    end = min(total, offset + max_matches)
    shown = positions[offset:end]
    snippets: list[str] = []
    for p in shown:
        s = max(0, p - context)
        e = min(n, p + len(pattern) + context)
        chunk = text[s:e].replace("\n", "\\n")
        snippets.append(f"  [@{p}] …{chunk}…")
    if total == 1:
        header = f"1 match for {pattern!r} in page text ({n} chars):"
        body = "\n".join(snippets)
        return f"{header}\n{body}"
    header = (
        f"{total} matches for {pattern!r} in page text ({n} chars). "
        f"Showing matches {offset}-{end - 1} of {total}:"
    )
    tail = ""
    if end < total:
        more = positions[end:]
        more_preview = ", ".join(str(p) for p in more[:5])
        if len(more) > 5:
            more_preview += f", … (+{len(more) - 5} more)"
        tail = (
            f"\n({len(more)} more matches at offsets {more_preview} — call "
            f"read_grep(pattern={pattern!r}, offset={end}) for the rest.)"
        )
    return f"{header}\n" + "\n".join(snippets) + tail
```

Update the `Tool` registration at lines 253-266 to:

```python
Tool(
    "read_grep",
    "Find all case-insensitive occurrences of pattern; returns a "
    "paginated index with character-offset markers. context controls "
    "snippet width only; offset skips the first N matches.",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "context": {"type": "integer", "default": 80},
            "max_matches": {"type": "integer", "default": 10},
            "offset": {"type": "integer", "default": 0},
            "reason": {"type": "string"},
        },
        "required": ["pattern", "reason"],
    },
    fns["read_grep"],
),
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_browser_read_grep.py -v`
Expected: all 7 tests PASS.

Run: `uv run ruff check tests/unit/test_browser_read_grep.py src/agent/tools/browser.py`
Expected: clean.

**Step 5: Commit**

```bash
git add task2/src/agent/tools/browser.py task2/tests/unit/test_browser_read_grep.py
git commit -m "feat(task2): paginated read_grep with offset markers and vocab hints"
```

---

## Task 2: Update existing read_grep callers/tests for the new tool surface

**Files:**
- Verify: `task2/tests/unit/test_loop_grep_grounding.py` (uses pattern only — should still pass).
- Modify: `task2/tests/unit/test_loop_anti_repeat.py:567-635` (the existing dedup test inlines the OLD `read_grep`; rewrite to inline the NEW shape).
- Modify: `task2/tests/integration/test_browser_tools_nav_read.py` if it asserts on the `NOT FOUND:` string.

**Step 1: Run the existing tests to find breakage**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py tests/unit/test_loop_grep_grounding.py tests/integration/test_browser_tools_nav_read.py -v`
Expected: failures wherever the old `NOT FOUND:` shape or single-snippet shape is asserted.

**Step 2: Update each broken test**

For `test_loop_anti_repeat.py:567-635` (`test_read_grep_dedup_returns_synthetic_when_pattern_repeats`): the inline `read_grep` stub is the OLD function. Rewrite the stub to call the real function from `agent.tools.browser`:

```python
async def read_grep(pattern: str, context: int = 80, max_matches: int = 10,
                    offset: int = 0, reason: str = ""):
    from agent.tools.browser import build_browser_tools
    fns = build_browser_tools(_StubSession(browser.page._text), restrict_goto=False)
    return await fns["read_grep"](pattern=pattern, context=context,
                                   max_matches=max_matches, offset=offset)
```

(or factor out a small helper if multiple tests need it). Adjust the dedup-synthetic assertion in step 5 (Task 4 will tighten it further).

For `test_browser_tools_nav_read.py`: replace any `NOT FOUND:` substring assertions with `NO MATCH for`.

**Step 3: Run the suite**

Run: `uv run pytest tests/unit tests/integration -k "read_grep or grep or dedup" -v`
Expected: all green.

**Step 4: Commit**

```bash
git add task2/tests/
git commit -m "test(task2): adapt existing read_grep tests to new paginated tool shape"
```

---

## Task 3: Per-line hash dedup — replace `_read_grep_seen`

**Files:**
- Modify: `task2/src/agent/loop.py:159` (init), `loop.py:248-249` (page-mutation clear), `loop.py:372-380` (delete the pre-call synthetic block), `loop.py:504-512` (replace recording with per-line hash logic).
- Test: `task2/tests/unit/test_loop_anti_repeat.py` (extend).

**Step 1: Write the failing tests**

Append to `tests/unit/test_loop_anti_repeat.py`:

```python
@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_fires_synthetic_when_all_lines_seen(tmp_path):
    """Same args → every line of call 2 was already shown → DUPLICATE synthetic."""
    text = "alpha needle beta needle gamma"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "needle", "reason": "first"}),
        ("read_grep", {"pattern": "needle", "reason": "again — every line was already shown"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "alpha needle beta", "reason": "x"}),
    ])
    # ... build + run with the real read_grep registered ...
    assert loop.tape[1]["obs"].startswith("DUPLICATE:")


@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_passes_partial_overlap_with_note(tmp_path):
    """Two different patterns; second call's match-lines overlap by 1 line.
    Output keeps new lines, drops the overlap, and notes the hidden count."""
    # Construct text so that both patterns return the SAME line for one match
    # but the second pattern also produces a unique match.
    text = "alpha shared line\nbeta unique to second\n"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "shared", "reason": "first"}),
        ("read_grep", {"pattern": "line", "reason": "overlaps shared but adds unique-to-second"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "alpha shared line", "reason": "x"}),
    ])
    # ... build + run ...
    obs2 = loop.tape[1]["obs"]
    assert not obs2.startswith("DUPLICATE:")
    assert "lines hidden" in obs2  # hidden-count note present


@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_does_not_fire_when_offset_yields_new_lines(tmp_path):
    text = ("XYZ " * 20).strip()
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "XYZ", "max_matches": 5, "offset": 0, "reason": "page1"}),
        ("read_grep", {"pattern": "XYZ", "max_matches": 5, "offset": 5, "reason": "page2"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "XYZ XYZ XYZ", "reason": "x"}),
    ])
    # ... build + run ...
    assert not loop.tape[1]["obs"].startswith("DUPLICATE:")
    assert "Showing matches 5-9" in loop.tape[1]["obs"]


@pytest.mark.asyncio
async def test_read_grep_dedup_resets_on_page_mutation(tmp_path):
    """After a navigation, the line cache is cleared — same pattern → fresh obs."""
    browser = _StubBrowser("alpha needle beta")
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "needle", "reason": "first"}),
        ("goto", {"url": "https://x.test/2", "reason": "navigate"}),
        ("read_grep", {"pattern": "needle", "reason": "post-nav"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "alpha needle beta", "reason": "x"}),
    ])
    # ... build + run, mutate browser.set_text after the goto step ...
    assert not loop.tape[2]["obs"].startswith("DUPLICATE:")
```

**Step 2: Run them red**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py -k "per_line or dedup_resets" -v`
Expected: FAIL — the per-line mechanism doesn't exist yet.

**Step 3: Implement per-line hash dedup in `loop.py`**

Add `import hashlib` to module imports if absent.

In `ReactLoop.__init__` (around line 159), replace:
```python
self._read_grep_seen: dict[str, int] = {}
```
with:
```python
self._read_grep_line_hashes: dict[str, int] = {}
self._read_grep_dedup_streak: int = 0
```

In the page-mutation clear block at line 249, replace `self._read_grep_seen.clear()` with:
```python
self._read_grep_line_hashes.clear()
self._read_grep_dedup_streak = 0
```

**Delete** the pre-call `read_grep_synthetic` block at lines 372-380 — the new logic runs *after* the call (we need the obs to hash).

Replace the recording block at lines 504-512 with the per-line hash logic. Inside the `try:` that runs the call (around line 493-512), after `obs = await self.registry.call(name, args)` returns successfully, when `name == "read_grep"`:

```python
if name == "read_grep" and isinstance(obs, str):
    raw_lines = obs.splitlines()
    kept: list[str] = []
    hidden = 0
    has_new = False
    for ln in raw_lines:
        key = ln.strip()
        if not key:
            kept.append(ln)
            continue
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if h in self._read_grep_line_hashes:
            hidden += 1
            continue
        self._read_grep_line_hashes[h] = step_idx
        kept.append(ln)
        has_new = True
    if not has_new:
        obs = (
            f"DUPLICATE: read_grep returned {hidden} lines, all previously shown. "
            "Page text has not changed since. Try a different pattern, "
            "a different offset, or call done()."
        )
        self._read_grep_dedup_streak += 1
    else:
        if hidden > 0:
            obs = "\n".join(kept) + (
                f"\n  ({hidden} lines hidden — already shown in prior read_grep calls)"
            )
        else:
            obs = "\n".join(kept)
        self._read_grep_dedup_streak = 0
elif name != "read_grep":
    self._read_grep_dedup_streak = 0
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py -k "per_line or dedup_resets or read_grep_dedup_returns_synthetic" -v`
Expected: PASS. (Note: the legacy `test_read_grep_dedup_returns_synthetic_when_pattern_repeats` will likely need its assertion updated from `"already searched"` to `"DUPLICATE:"` — that's a Task-2 follow-up edit.)

Run: `uv run ruff check src/agent/loop.py tests/unit/test_loop_anti_repeat.py`
Expected: clean.

**Step 5: Commit**

```bash
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): per-line hash dedup for read_grep — passes partial overlap"
```

---

## Task 4: Hide `read_grep` after K=2 streak of fully-redundant calls

**Files:**
- Modify: `task2/src/agent/loop.py` (extend the per-line dedup block from Task 3)
- Test: `task2/tests/unit/test_loop_anti_repeat.py` (extend)

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_read_grep_hidden_after_3_fully_redundant_calls(tmp_path):
    text = "alpha needle beta"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "needle", "reason": "1"}),
        ("read_grep", {"pattern": "needle", "reason": "2 — every line already shown"}),
        ("read_grep", {"pattern": "needle", "reason": "3 — every line still already shown"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "alpha needle beta", "reason": "x"}),
    ])
    # ... build + run ...
    third = loop.tape[2]["obs"]
    assert "no longer available" in third
    assert "read_grep" in loop._hidden_tools


@pytest.mark.asyncio
async def test_read_grep_streak_resets_on_other_tool(tmp_path):
    text = "alpha needle beta"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "needle", "reason": "1"}),
        ("read_grep", {"pattern": "needle", "reason": "2"}),
        ("read", {"offset": 0, "reason": "interleave"}),
        ("read_grep", {"pattern": "needle", "reason": "3 — should NOT hide"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "alpha needle", "reason": "x"}),
    ])
    # ... build + run ...
    assert "read_grep" not in loop._hidden_tools
    assert "no longer available" not in loop.tape[3]["obs"]


@pytest.mark.asyncio
async def test_read_grep_partial_overlap_does_not_count_toward_streak(tmp_path):
    """Calls returning even one new line reset the streak — only fully-
    redundant calls (DUPLICATE synthetic) accumulate."""
    # Two patterns that overlap on one line each but each surfaces a unique line.
    text = "shared one\nshared two\nunique-A only\nunique-B only\n"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "shared", "reason": "1"}),
        ("read_grep", {"pattern": "unique-A", "reason": "2 — new line"}),
        ("read_grep", {"pattern": "unique-B", "reason": "3 — new line"}),
        ("done", {"status": "failed", "answer": "stop", "evidence": "unique-A only", "reason": "x"}),
    ])
    # ... build + run ...
    assert "read_grep" not in loop._hidden_tools
```

**Step 2: Run them red**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py -k "hidden_after_3 or streak_resets_on_other or partial_overlap_does_not" -v`
Expected: FAIL — `_hidden_tools` does not yet receive `read_grep`.

**Step 3: Add the hide-after-K logic**

Inside the `not has_new` branch added in Task 3, after `self._read_grep_dedup_streak += 1`, append:
```python
if self._read_grep_dedup_streak >= 2:
    self._hidden_tools.add("read_grep")
    obs = (
        "read_grep is no longer available this turn — it returned only "
        "previously-shown lines 3× in a row. Use 'read' with a different "
        "offset, list_interactive, or done()."
    )
```

(`_hidden_tools` is already cleared on page mutation at `loop.py:248`, so re-enable on navigation is automatic. The streak reset on partial overlap is already in place because the `else: ... has_new` branch sets `_read_grep_dedup_streak = 0`.)

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py -k "hidden_after_3 or streak_resets" -v`
Expected: PASS.

Run the full anti-repeat module: `uv run pytest tests/unit/test_loop_anti_repeat.py -v`
Expected: all green (existing tests still pass — streak is additive).

**Step 5: Commit**

```bash
git add task2/src/agent/loop.py task2/tests/unit/test_loop_anti_repeat.py
git commit -m "feat(task2): hide read_grep after 3 consecutive duplicate outputs"
```

---

## Task 5: `done()` evidence schema and validation

**Files:**
- Modify: `task2/src/agent/tools/meta.py:47-48` (the `done` async function), `meta.py:99-115` (schema).
- Test: `task2/tests/unit/test_done_evidence.py` (NEW).

**Step 1: Write the failing tests**

Create `task2/tests/unit/test_done_evidence.py`:

```python
import pytest

from agent.tools.meta import LoopDone, build_meta_tools, QuestionChannel


def _done(prior_obs: list[str]):
    """Return a done() bound to a tape composed of the given prior obs."""
    tape = [{"action": "read", "obs": o} for o in prior_obs]
    qc = QuestionChannel()
    fns = build_meta_tools(question_channel=qc, tape=tape)
    return fns["done"]


@pytest.mark.asyncio
async def test_done_success_requires_non_empty_evidence():
    done = _done(["alpha beta gamma"])
    with pytest.raises(ValueError, match="evidence"):
        await done(status="success", answer="alpha")


@pytest.mark.asyncio
async def test_done_success_evidence_must_be_in_prior_obs():
    done = _done(["alpha beta gamma"])
    with pytest.raises(ValueError, match="not found in any prior observation"):
        await done(status="success", answer="alpha", evidence="omega delta sigma")


@pytest.mark.asyncio
async def test_done_success_answer_must_be_in_evidence():
    done = _done(["alpha beta gamma delta epsilon"])
    with pytest.raises(ValueError, match="answer.*not contained in evidence"):
        await done(status="success", answer="omega", evidence="alpha beta gamma")


@pytest.mark.asyncio
async def test_done_success_evidence_min_length_10():
    done = _done(["the answer is 9 today"])
    with pytest.raises(ValueError, match="at least 10"):
        await done(status="success", answer="9", evidence="9")


@pytest.mark.asyncio
async def test_done_success_passes_when_evidence_grounded_and_contains_answer():
    done = _done(["The Definite integral evaluates to 9 over [0,3]"])
    with pytest.raises(LoopDone) as e:
        await done(status="success", answer="9",
                   evidence="evaluates to 9 over [0,3]")
    assert e.value.status == "success" and e.value.answer == "9"


@pytest.mark.asyncio
async def test_done_failed_evidence_optional():
    done = _done(["page text"])
    with pytest.raises(LoopDone) as e:
        await done(status="failed", answer="couldn't find it")
    assert e.value.status == "failed"


@pytest.mark.asyncio
async def test_done_failed_evidence_when_provided_must_be_in_prior_obs():
    done = _done(["page text"])
    with pytest.raises(ValueError, match="not found in any prior observation"):
        await done(status="failed", answer="blocked",
                   evidence="this string is not on the page")


@pytest.mark.asyncio
async def test_done_evidence_normalization_whitespace_and_case():
    done = _done(["The Quick Brown Fox\nJumps Over"])
    with pytest.raises(LoopDone):
        await done(status="success", answer="quick brown fox",
                   evidence="the   QUICK brown   fox")  # extra spaces, mixed case
```

**Step 2: Run them red**

Run: `uv run pytest tests/unit/test_done_evidence.py -v`
Expected: FAIL — `build_meta_tools` doesn't accept `tape`, `done` doesn't accept `evidence`.

**Step 3: Implement validation**

Modify `task2/src/agent/tools/meta.py`:

```python
import re

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def build_meta_tools(
    *,
    question_channel: QuestionChannel,
    reason_log: list[str] | None = None,
    tape: list[dict] | None = None,
) -> dict:
    async def ask_user_question(question: str) -> str:
        ans = await question_channel.ask(question)
        return f"user said: {ans}"

    async def done(status: str, answer: str, evidence: str = "") -> str:
        if status == "success":
            if not evidence:
                raise ValueError(
                    "done() rejected — status='success' requires a non-empty "
                    "evidence string. Cite a substring of page text you read."
                )
            if len(evidence) < 10:
                raise ValueError(
                    "done() rejected — evidence must be at least 10 characters "
                    "to avoid trivial matches. Quote a longer surrounding span."
                )
            ev_norm = _normalize(evidence)
            ans_norm = _normalize(answer)
            if ans_norm not in ev_norm:
                raise ValueError(
                    f"done() rejected — answer {answer!r} not contained in "
                    f"evidence {evidence!r}. Cite text that includes the answer."
                )
            haystack = " ".join(
                _normalize(str(s.get("obs", "")))
                for s in (tape or [])
                if isinstance(s, dict)
            )
            if ev_norm not in haystack:
                raise ValueError(
                    f"done() rejected — evidence {evidence!r} not found in any "
                    "prior observation. Cite text from a prior read/read_grep "
                    "obs, or call done(status='failed', evidence='<short reason>')."
                )
        elif status == "failed" and evidence:
            ev_norm = _normalize(evidence)
            haystack = " ".join(
                _normalize(str(s.get("obs", "")))
                for s in (tape or [])
                if isinstance(s, dict)
            )
            if ev_norm not in haystack:
                raise ValueError(
                    f"done() rejected — evidence {evidence!r} not found in any "
                    "prior observation."
                )
        raise LoopDone(status, answer, evidence)
    ...  # ask_user_question, reason unchanged
    return {
        "ask_user_question": ask_user_question,
        "done": done,
        "reason": reason,
    }
```

Update `LoopDone` to carry evidence:
```python
class LoopDone(Exception):
    def __init__(self, status: str, answer: str, evidence: str = ""):
        super().__init__(f"done({status})")
        self.status = status
        self.answer = answer
        self.evidence = evidence
```

Update the `Tool` schema in `build_meta_tool_list` (lines 99-115):
```python
Tool(
    "done",
    "Finish the task. status ∈ {success, failed, needs_user}. evidence MUST "
    "be a substring of a prior observation when status='success' (and must "
    "contain the answer); optional but validated when status='failed'.",
    {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "failed", "needs_user"],
            },
            "answer": {"type": "string"},
            "evidence": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["status", "answer", "reason"],
    },
    fns["done"],
),
```

`build_meta_tool_list` must pass `tape` through to `build_meta_tools`. Add a `tape` kwarg to `build_meta_tool_list` and forward it.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_done_evidence.py -v`
Expected: all 8 tests PASS.

**Step 5: Commit**

```bash
git add task2/src/agent/tools/meta.py task2/tests/unit/test_done_evidence.py
git commit -m "feat(task2): require evidence on done(success), validate against tape"
```

---

## Task 6: Wire `tape` to `build_meta_tools` in the loop

**Files:**
- Modify: `task2/src/agent/loop.py` wherever `build_meta_tools` / `build_meta_tool_list` is invoked.
- Modify: `task2/src/agent/cli.py` and/or `task2/src/agent/app_factory.py` if they construct meta tools at startup.
- Verify the loop's exception handler around `await self.registry.call(name, args)` (loop.py:493-512) catches `ValueError` from `done()` and folds it back into the obs stream for the LLM (it almost certainly already does — confirm).

**Step 1: Find all call sites**

Run: `grep -rn "build_meta_tool" task2/src task2/tests`

For each call site that wires `done` into a `ReactLoop`, ensure the same `tape` reference the loop uses is also passed to `build_meta_tools`. This requires either:
- Lazy construction inside the loop (pass `tape=self.tape`), or
- A `tape_provider: Callable[[], list[dict]]` so the closure reads the live list.

Prefer the closure form to avoid re-binding tape on each call.

**Step 2: Write a failing integration test**

Append to `tests/unit/test_loop_anti_repeat.py`:
```python
@pytest.mark.asyncio
async def test_done_validation_error_returned_as_obs_then_retry(tmp_path):
    text = "the answer 9 is here"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls([
        ("read_grep", {"pattern": "answer", "reason": "look"}),
        ("done", {"status": "success", "answer": "9", "evidence": "fictional",
                  "reason": "first try"}),  # fails: not in obs
        ("done", {"status": "success", "answer": "9",
                  "evidence": "the answer 9 is here", "reason": "fixed"}),
    ])
    # ... build + run with read_grep registered ...
    # Step 1 obs is the rejection; loop continues; step 2 succeeds.
    assert "rejected" in loop.tape[1]["obs"]
    assert result["status"] == "success" and result["answer"] == "9"
```

**Step 3: Run red**

Run: `uv run pytest tests/unit/test_loop_anti_repeat.py::test_done_validation_error_returned_as_obs_then_retry -v`
Expected: FAIL until tape is wired through.

**Step 4: Wire tape through**

Modify the meta-tools construction site in `loop.py` (and any factory that builds them outside the loop) so `done` sees the live tape. Sketch in the loop file:
```python
self.meta_fns = build_meta_tools(
    question_channel=self.qc,
    reason_log=self.reason_log,
    tape=self.tape,
)
```

If `build_meta_tool_list` is called by `app_factory.py` or `cli.py` *before* a loop instance exists, refactor: construct meta tools with `tape=[]` at startup, then have `ReactLoop.__init__` overwrite the `tape` reference in the closure (or simpler: move meta-tool construction into `ReactLoop.__init__` itself).

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit -k "done_validation" -v`
Run: `uv run pytest tests/unit/test_force_done.py -v` (should still pass — Task 7 covers force-done explicitly).

**Step 6: Commit**

```bash
git add task2/src/agent/
git commit -m "feat(task2): pass live tape into done() so evidence validates against it"
```

---

## Task 7: `coerce_done_via_llm` evidence-aware fallback

**Files:**
- Modify: `task2/src/agent/force_done.py:73-125`
- Test: `task2/tests/unit/test_force_done.py` (extend)

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_coerce_done_forces_failed_when_evidence_missing(monkeypatch):
    """When the coerced LLM response is success but lacks valid evidence,
    coerce_done downgrades to failed automatically."""
    # ... mock LLM to return done(success, "9", evidence="fabricated") ...
    # ... tape contains no "fabricated" anywhere ...
    result = await coerce_done_via_llm(...)
    assert result["status"] == "failed"
```

**Step 2: Run red**

Run: `uv run pytest tests/unit/test_force_done.py -k "evidence_missing" -v`
Expected: FAIL.

**Step 3: Implement**

In `force_done.py`, after parsing `args` from the LLM's tool call (line 122-125), validate evidence against tape using the same `_normalize` + substring check. On failure → `{"status": "failed", "answer": _placeholder(...)}`. Add `evidence` to the returned dict (additive — bench scorer will ignore it).

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_force_done.py -v`
Expected: all green.

**Step 5: Commit**

```bash
git add task2/src/agent/force_done.py task2/tests/unit/test_force_done.py
git commit -m "feat(task2): coerce_done downgrades ungrounded success to failed"
```

---

## Task 8: System-prompt update for `evidence`

**Files:**
- Modify: `task2/prompts/system.md` (or wherever the agent system prompt lives — `grep -rn "list_interactive" task2/prompts task2/src` to find).

**Step 1: Locate the prompt**

Run: `grep -l "done(status" task2/prompts/ task2/src/agent/`

**Step 2: Add a paragraph to the system prompt**

Insert under the `done()` description:

```
When calling done(status="success", ...), you MUST include `evidence`: a
short verbatim substring of a prior read/read_grep observation that
contains your answer. The evidence is validated against your action tape;
ungrounded answers will be rejected and you will be asked to retry. If
the page does not contain the answer, call done(status="failed",
evidence="<short reason>") instead — fabricating an answer from prior
knowledge is a failure mode.
```

**Step 3: Commit**

```bash
git add task2/prompts/
git commit -m "docs(task2): instruct agent to provide evidence on done(success)"
```

---

## Task 9: Bench validation and observations

**Files:**
- Run: existing bench harness (per `bench-failure-triage` skill conventions).
- Write: `observations.md` at repo root with results.

**Step 1: Run the full 12-case WebVoyager bench**

Per project convention (see prior `observations.md` runs):
```bash
cd task2
uv run python -m bench.run_webvoyager  # or whatever the harness command is — confirm
```

**Step 2: Specifically verify session 51bf0da0 regression**

Re-run the Wolfram integral case in isolation. Assert:
- No `done(success, "9")` without evidence.
- Either `done(failed)` (correct outcome — answer not on page) or `done(success, ...)` with evidence drawn from actual page text.
- No more than ~5 `read_grep` calls before the agent escalates / tries a different action.

**Step 3: Write observations.md**

Follow the format in current `observations.md`:
- Per-case wall-time delta vs. previous run.
- Findings (P0/P1) with trace evidence.
- Net assessment vs. design's stated goals.
- Confirm bar holds: ≥ 9/12 success.

**Step 4: Commit**

```bash
git add observations.md
git commit -m "docs(task2): bench validation for read_grep + evidence grounding"
```

---

## Task 10: Open PR

**Step 1: Push branch**

```bash
git push -u origin task2-readgrep-and-evidence-grounding
```

**Step 2: Open PR via gh**

```bash
gh pr create --title "feat(task2): paginated read_grep + done() evidence grounding" \
  --body "$(cat <<'EOF'
## Summary
- Redesigns `read_grep` as a paginated match-index with offset markers and NO-MATCH vocab hints
- Replaces pattern-keyed dedup with obs-hash dedup; hides `read_grep` after 3 consecutive duplicate outputs
- Requires `evidence` on `done(status='success', ...)`, validated against the action tape

## Why
Bench session `51bf0da0bb4f41d290e5325644b10404` (Wolfram integral) burned 42/50 steps in a `read_grep` no-op loop and emitted `done(success, "9")` despite "9" never appearing in any observation. This PR closes both failure modes.

## Test plan
- [ ] `uv run pytest tests/unit tests/integration -v`
- [ ] `uv run ruff check .`
- [ ] 12-case WebVoyager bench: ≥ 9/12 success, no regressions
- [ ] Wolfram case ends in `done(failed)` or `done(success, ...)` with valid evidence

## Design
docs/plans/2026-05-03-readgrep-redesign-and-answer-grounding-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Done criteria

1. All new and existing unit + integration tests pass: `uv run pytest tests/ -v`.
2. `uv run ruff check .` clean.
3. Bench result documented in `observations.md` with ≥ 9/12 success.
4. PR opened, design doc linked.
