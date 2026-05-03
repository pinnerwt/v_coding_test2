# Grounded `goto` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Constrain the `goto` browser tool to only accept URLs that appear in the goal text or in prior tape observations, defeating the LLM's "navigate to canonical-but-unobserved URL" shortcut surfaced by WebVoyager case 104.

**Architecture:** Two pure helpers (URL extractor, decision rule) drive a thin guard inside the `goto` handler. Allowlist is built per-call from a closure that returns the current pool of strings (goal + browser visited URLs + tape obs). Loop emits a `goto_blocked` trace event when an obs starts with the block marker. Config-flagged via `AGENT_RESTRICT_GOTO` (default `true`).

**Tech Stack:** Python 3.11, FastAPI, Playwright, pytest with `MockTransport`, `uv` for env, `ruff` for lint.

**Reference design:** `docs/plans/2026-05-02-task2-grounded-goto-design.md`.

**Working dir for all tasks:** `task2/` (run `uv run pytest`, `uv run ruff check .`, `uv run ruff format .` from there).

---

### Task 1: Pure URL-extraction helper

**Files:**
- Modify: `task2/src/agent/tools/browser.py` (add helper near top)
- Test: `task2/tests/unit/test_goto_guard.py` (new)

**Step 1: Write failing test**

```python
# task2/tests/unit/test_goto_guard.py
from agent.tools.browser import _extract_urls


def test_extract_urls_finds_http_and_https():
    s = "see https://en.wikipedia.org/wiki/Tokyo and http://x.test/a?b=c here"
    assert _extract_urls(s) == [
        "https://en.wikipedia.org/wiki/Tokyo",
        "http://x.test/a?b=c",
    ]


def test_extract_urls_strips_trailing_punctuation():
    s = "From https://arxiv.org. Visit https://github.com/repo) now."
    assert _extract_urls(s) == ["https://arxiv.org", "https://github.com/repo"]


def test_extract_urls_handles_empty():
    assert _extract_urls("") == []
    assert _extract_urls("no urls here") == []
```

**Step 2: Run, expect ImportError**

```
uv run pytest tests/unit/test_goto_guard.py -v
```

Expected: ImportError on `_extract_urls`.

**Step 3: Implement helper**

Add at top of `browser.py` (after the imports, before `_READ_LIMIT`):

```python
import re

_URL_RE = re.compile(r"https?://[^\s)\"'<>]+")
_TRAILING_PUNCT = ".,;:!?)]}"


def _extract_urls(text: str) -> list[str]:
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0)
        while url and url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
        if url:
            out.append(url)
    return out
```

**Step 4: Tests pass**

```
uv run pytest tests/unit/test_goto_guard.py -v
```

Expected: 3 passed.

**Step 5: Commit**

```bash
git add task2/src/agent/tools/browser.py task2/tests/unit/test_goto_guard.py
git commit -m "feat(task2): add _extract_urls helper for goto guard"
```

---

### Task 2: Pure decision-rule helper

**Files:**
- Modify: `task2/src/agent/tools/browser.py`
- Test: `task2/tests/unit/test_goto_guard.py` (extend)

**Step 1: Write failing tests**

Append to `test_goto_guard.py`:

```python
from agent.tools.browser import _is_goto_allowed


def test_is_goto_allowed_substring_of_observed():
    # observed url is longer; requested is a prefix → allowed
    assert _is_goto_allowed(
        "https://en.wikipedia.org",
        ["https://en.wikipedia.org/wiki/Tokyo"],
    )


def test_is_goto_allowed_observed_is_substring_of_request():
    # observed is a prefix of request → allowed
    assert _is_goto_allowed(
        "https://en.wikipedia.org/wiki/Tokyo",
        ["https://en.wikipedia.org"],
    )


def test_is_goto_allowed_case_insensitive():
    assert _is_goto_allowed(
        "https://Arxiv.org/abs/1406.2661",
        ["https://arxiv.org/abs/1406.2661"],
    )


def test_is_goto_allowed_blocked_when_unrelated():
    assert not _is_goto_allowed(
        "https://arxiv.org/abs/1406.2661",
        ["https://arxiv.org"],  # the bare domain doesn't contain the abs path
    )


def test_is_goto_allowed_empty_allowlist_blocks():
    assert not _is_goto_allowed("https://x.test", [])
```

Wait — re-check `test_is_goto_allowed_substring_of_observed` vs
`test_is_goto_allowed_blocked_when_unrelated`. The design rule:
allowed iff requested is substring of observed OR observed is
substring of requested. So `goto https://arxiv.org` when only
`https://arxiv.org/abs/1406.2661` is observed → allowed (observed
contains request). And `goto https://arxiv.org/abs/1406.2661` when
only `https://arxiv.org` is observed → allowed too (observed is
substring of request). That's intentional per the design — the bare
domain in the goal allowlists everything under it.

Adjust the failing-block test to be unambiguously off-list:

```python
def test_is_goto_allowed_blocked_when_unrelated():
    assert not _is_goto_allowed(
        "https://arxiv.org/abs/1406.2661",
        ["https://en.wikipedia.org"],  # different domain entirely
    )
```

**Step 2: Run, expect ImportError**

```
uv run pytest tests/unit/test_goto_guard.py -v
```

**Step 3: Implement**

Add to `browser.py`:

```python
def _is_goto_allowed(url: str, allowlist: list[str]) -> bool:
    if not url:
        return False
    u = url.lower()
    for src in allowlist:
        s = (src or "").lower()
        if not s:
            continue
        if u in s or s in u:
            return True
    return False
```

**Step 4: Tests pass**

```
uv run pytest tests/unit/test_goto_guard.py -v
```

Expected: 5 new tests passing (plus the 3 from Task 1 = 8 total in this file).

**Step 5: Commit**

```bash
git add task2/src/agent/tools/browser.py task2/tests/unit/test_goto_guard.py
git commit -m "feat(task2): add _is_goto_allowed decision rule"
```

---

### Task 3: Guard the `goto` tool with an `allowlist_sources` callback

**Files:**
- Modify: `task2/src/agent/tools/browser.py` (`build_browser_tools` and
  `build_browser_tool_list` signatures + `goto` body)
- Test: `task2/tests/unit/test_goto_guard.py` (extend)

**Step 1: Write failing tests**

Append to `test_goto_guard.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.tools.browser import build_browser_tools


def _fake_session(landing_url: str = "https://done.test/"):
    s = MagicMock()
    s.page = MagicMock()
    s.page.url = landing_url
    s.page.goto = AsyncMock(return_value=None)
    return s


@pytest.mark.asyncio
async def test_goto_blocked_when_url_not_in_sources():
    sources = ["https://en.wikipedia.org"]
    sess = _fake_session()
    tools = build_browser_tools(
        sess, restrict_goto=True, allowlist_sources=lambda: sources
    )
    obs = await tools["goto"]("https://arxiv.org/abs/1406.2661")
    assert obs.startswith("ERROR: blocked goto to ")
    assert "list_interactive" in obs
    sess.page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_goto_allowed_when_url_in_sources():
    sources = ["Goal: Go to https://arxiv.org and find ..."]
    sess = _fake_session("https://arxiv.org/")
    tools = build_browser_tools(
        sess, restrict_goto=True, allowlist_sources=lambda: sources
    )
    obs = await tools["goto"]("https://arxiv.org")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_unrestricted_by_default():
    sess = _fake_session("https://anything.test/")
    tools = build_browser_tools(sess)  # restrict_goto defaults to False
    obs = await tools["goto"]("https://anything.test/")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_unrestricted_when_sources_callback_returns_no_urls():
    """Per design: if no URL in any source, restriction is disabled
    (constraint only applies when there's an anchor)."""
    sess = _fake_session("https://anywhere.test/")
    tools = build_browser_tools(
        sess, restrict_goto=True, allowlist_sources=lambda: ["plain text goal, no urls"]
    )
    obs = await tools["goto"]("https://anywhere.test/")
    assert "navigated to" in obs
    sess.page.goto.assert_awaited_once()
```

**Step 2: Run, expect failures**

```
uv run pytest tests/unit/test_goto_guard.py -v
```

Expected: 4 new failures — `build_browser_tools` doesn't accept the new kwargs.

**Step 3: Implement**

In `browser.py`, change `build_browser_tools` signature and `goto` body:

```python
from typing import Any, Callable


def build_browser_tools(
    session: BrowserSession,
    *,
    restrict_goto: bool = False,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    async def goto(url: str) -> str:
        if restrict_goto and allowlist_sources is not None:
            sources = allowlist_sources()
            allowlist = []
            for s in sources:
                allowlist.extend(_extract_urls(s))
            if allowlist and not _is_goto_allowed(url, allowlist):
                return (
                    f"ERROR: blocked goto to {url} — URL not present in prior "
                    "observations or goal. Use list_interactive + click to navigate."
                )
        try:
            await session.page.goto(url, wait_until="networkidle", timeout=10_000)
            return f"navigated to {session.page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    # ... rest unchanged
```

Update `build_browser_tool_list` to accept and forward the same kwargs:

```python
def build_browser_tool_list(
    session: BrowserSession,
    *,
    restrict_goto: bool = False,
    allowlist_sources: Callable[[], list[str]] | None = None,
) -> list[Tool]:
    fns = build_browser_tools(
        session, restrict_goto=restrict_goto, allowlist_sources=allowlist_sources
    )
    # ... rest unchanged
```

**Step 4: Tests pass**

```
uv run pytest tests/unit/test_goto_guard.py -v
uv run pytest  # full suite still green
```

Expected: 9 in goto_guard file, full suite still passes (existing
callers use defaults).

**Step 5: Commit**

```bash
git add task2/src/agent/tools/browser.py task2/tests/unit/test_goto_guard.py
git commit -m "feat(task2): guard goto behind restrict_goto + allowlist_sources"
```

---

### Task 4: Config flag `restrict_goto`

**Files:**
- Modify: `task2/src/agent/config.py`
- Test: `task2/tests/unit/test_config.py`

**Step 1: Write failing test**

Append to `test_config.py`:

```python
def test_restrict_goto_defaults_true(monkeypatch):
    monkeypatch.delenv("AGENT_RESTRICT_GOTO", raising=False)
    cfg = Config.from_env()
    assert cfg.restrict_goto is True


def test_restrict_goto_false_when_env_false(monkeypatch):
    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "false")
    cfg = Config.from_env()
    assert cfg.restrict_goto is False
```

**Step 2: Run, expect AttributeError**

```
uv run pytest tests/unit/test_config.py -v
```

**Step 3: Implement**

Edit `config.py`:

- Add `restrict_goto: bool` field to the `Config` dataclass.
- In `from_env()` add: `restrict_goto=_bool(os.getenv("AGENT_RESTRICT_GOTO"), True),`

**Step 4: Tests pass**

```
uv run pytest tests/unit/test_config.py -v
```

**Step 5: Commit**

```bash
git add task2/src/agent/config.py task2/tests/unit/test_config.py
git commit -m "feat(task2): add AGENT_RESTRICT_GOTO config flag (default true)"
```

---

### Task 5: Wire goto guard in `server.run_loop`

**Files:**
- Modify: `task2/src/agent/server.py` (around lines 38–110)
- Test: `task2/tests/integration/test_goto_guard_e2e.py` (new)

**Step 1: Write failing integration test**

Drop a tiny scripted-LLM integration test that proves the server-side
wiring works end-to-end:

```python
# task2/tests/integration/test_goto_guard_e2e.py
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


def _scripted_llm(steps):
    """steps: list[(tool_name, args_dict)] — replays in order."""
    it = iter(steps)

    async def handler(request):
        n, a = next(it)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "type": "function",
                                    "function": {
                                        "name": n,
                                        "arguments": json.dumps(a),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_goto_blocked_when_url_not_in_goal_or_tape(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "true")
    monkeypatch.setenv("MAX_STEPS", "4")

    transport = _scripted_llm(
        [
            # Try to jump to an unobserved URL
            ("goto", {"url": "https://arxiv.org/abs/1406.2661"}),
            # Then give up
            ("done", {"status": "failed", "answer": "blocked"}),
        ]
    )

    app = build_app(
        cfg=Config.from_env(),
        data_dir=tmp_path,
        llm_transport=transport,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/run_sync",
            json={"goal": "Go to https://wikipedia.org and find X"},
        )
        assert r.status_code == 200

    # Trace must contain a step whose obs starts with the block marker
    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert traces, "no trace written"
    blocked = False
    for line in traces[0].read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "step" and ev["payload"].get("obs", "").startswith(
            "ERROR: blocked goto"
        ):
            blocked = True
    assert blocked, "expected a blocked-goto step in trace"
```

**Step 2: Run, expect failure**

```
uv run pytest tests/integration/test_goto_guard_e2e.py -v
```

Expected: fails because the server isn't yet passing `restrict_goto` /
`allowlist_sources` into `build_browser_tool_list`.

**Step 3: Wire in `server.py`**

In `run_loop`, after `browser.start()` and before building the
registry, add a closure that supplies the allowlist sources, then pass
the new kwargs to `build_browser_tool_list`. Late-binding closure
captures `loop` (defined later):

```python
loop_holder: list = []  # populated below to break the chicken-and-egg

def allowlist_sources():
    if not loop_holder:
        return [goal]
    return [goal] + [
        step.get("obs", "") for step in loop_holder[0].tape
    ] + [browser.page.url or ""]

reg = ToolRegistry()
for t in build_browser_tool_list(
    browser,
    restrict_goto=cfg.restrict_goto,
    allowlist_sources=allowlist_sources,
):
    reg.register(t)
# ... existing meta-tool registration ...
loop = ReactLoop(...)
loop_holder.append(loop)
return await loop.run(goal)
```

**Step 4: Test passes**

```
uv run pytest tests/integration/test_goto_guard_e2e.py -v
uv run pytest  # full suite green
```

**Step 5: Commit**

```bash
git add task2/src/agent/server.py task2/tests/integration/test_goto_guard_e2e.py
git commit -m "feat(task2): wire goto guard in server.run_loop (closure over goal+tape)"
```

---

### Task 6: Emit `goto_blocked` trace event

**Files:**
- Modify: `task2/src/agent/loop.py` (around the step trace write, ~lines 173–184)
- Test: `task2/tests/integration/test_goto_guard_e2e.py` (extend)

**Step 1: Write failing assertion**

Add to the existing e2e test, after the `blocked` assertion:

```python
    # Also expect a distinct goto_blocked event for bench/UI scoring
    saw_event = False
    for line in traces[0].read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "goto_blocked":
            assert ev["payload"]["url"] == "https://arxiv.org/abs/1406.2661"
            saw_event = True
    assert saw_event, "expected a goto_blocked trace event"
```

**Step 2: Run, expect AssertionError on `saw_event`**

```
uv run pytest tests/integration/test_goto_guard_e2e.py -v
```

**Step 3: Implement in `loop.py`**

Right after the `self.trace.write({"type": "step", ...})` block,
detect the block-marker prefix and emit a sibling event:

```python
self.trace.write({"type": "step", "payload": {...}})  # existing
if name == "goto" and obs_str.startswith("ERROR: blocked goto"):
    self.trace.write(
        {
            "type": "goto_blocked",
            "payload": {"url": args.get("url", ""), "reason": "not in observation allowlist"},
        }
    )
```

**Step 4: Test passes**

```
uv run pytest tests/integration/test_goto_guard_e2e.py -v
uv run pytest
```

**Step 5: Commit**

```bash
git add task2/src/agent/loop.py task2/tests/integration/test_goto_guard_e2e.py
git commit -m "feat(task2): emit goto_blocked trace event for blocked navigations"
```

---

### Task 7: Bench scoreboard `blocks` column

**Files:**
- Modify: `task2/scripts/bench_webvoyager.py`

**Step 1: Behavior to add (no separate unit test — it's a script)**

After each `run_one`, the script should read the most recent trace
file written for that run and count `goto_blocked` events. The summary
line per case becomes:

```
  ->      success  steps= 12  blocks=1  225145ms  <answer>
```

**Step 2: Modify `run_one`**

Change `run_one` to also accept `data_dir: Path` and, after the HTTP
call, scan `data_dir/traces/*.jsonl` for the *newest* file modified
after `t0` and count `goto_blocked` events:

```python
def _count_blocks(data_dir: Path, since_ts: float) -> int:
    traces_dir = data_dir / "traces"
    if not traces_dir.exists():
        return 0
    candidates = [p for p in traces_dir.glob("*.jsonl") if p.stat().st_mtime >= since_ts]
    if not candidates:
        return 0
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    n = 0
    for line in newest.read_text().splitlines():
        try:
            if json.loads(line).get("type") == "goto_blocked":
                n += 1
        except Exception:
            pass
    return n
```

Inject the count into the result dict and the print line. Pass
`ROOT / "data"` as the `data_dir`.

**Step 3: Smoke run (manual)**

Restart the dev server (already running on port 8001) and run a single
case to verify the column renders. The agent will (likely) be allowed
on case 101 — the goal contains `https://en.wikipedia.org`:

```
uv run python scripts/bench_webvoyager.py --ids 101 --timeout 600
```

Expected: prints `blocks=N` for some N (probably 0).

**Step 4: Commit**

```bash
git add task2/scripts/bench_webvoyager.py
git commit -m "feat(task2): bench scoreboard counts goto_blocked events per case"
```

---

### Task 8: SPA — render blocked-goto step with warning style

**Files:**
- Modify: `task2/src/agent/static/app.js` (the step-card render path)
- Modify: `task2/src/agent/templates/index.html` (CSS for `.card.warning`)

**Step 1: Add the CSS class**

In `index.html`'s `<style>` block, add a `.card.warning` rule (e.g. a
yellow left border and a tinted background — match the existing card
pattern; aim for ~5 lines of CSS).

**Step 2: Render warning when obs starts with the marker**

In `app.js`, where each step card is rendered, if `step.obs` starts
with `"ERROR: blocked goto"`, add the `warning` class to the card
element. Keep the rest of the rendering identical.

**Step 3: Manual smoke**

Restart the dev server and trigger a blocked goto via the SPA (e.g.
goal "Go to https://example.com and …" plus a manual or scripted run
that ends up trying an off-list URL). Verify the card visibly differs.

If staging this is fiddly, skip the manual smoke — the e2e test from
Task 6 already proves the trace event is emitted; the CSS is cosmetic.

**Step 4: Commit**

```bash
git add task2/src/agent/static/app.js task2/src/agent/templates/index.html
git commit -m "feat(task2): SPA renders blocked-goto step with warning style"
```

---

### Task 9: Re-bench WebVoyager Tier1 with restriction on

**Files:**
- Modify: `task2/data/bench/` will receive a new dated JSON (gitignored, but reference it in the commit message)
- Optionally create: `docs/plans/2026-05-02-task2-grounded-goto-results.md`
  with a small comparison table.

**Step 1: Restart server**

```
tmux kill-session -t task2-server || true
cd task2
tmux new-session -d -s task2-server -c "$(pwd)" \
  "uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001 \
   2>&1 | tee /tmp/task2-server.log"
sleep 3
curl -s http://127.0.0.1:8001/api/llm_health
```

**Step 2: Run all 12 cases**

```
uv run python scripts/bench_webvoyager.py --limit 12 --timeout 600
```

This will take 20–60 min. Run in background; poll via `tail` on the
output file. The unrestricted baseline from this session is **9/12
success**; expected new score is lower (some cases will be unable to
recover from blocks within the step budget).

**Step 3: Compare against baseline and write results doc**

Create `docs/plans/2026-05-02-task2-grounded-goto-results.md` with a
small table:

```
| Case | Baseline | Restricted | Blocks | Notes |
|------|----------|------------|--------|-------|
| 101  | success  | success    | 0      |       |
...
| 104  | success  | ???        | ???    | the GAN shortcut case |
```

Plus: total success rate before/after, headline interpretation.

**Step 4: Commit results**

```bash
git add docs/plans/2026-05-02-task2-grounded-goto-results.md
git commit -m "docs(task2): WebVoyager Tier1 results with grounded goto on"
```

---

## Done criteria

- All 40 prior tests + new ones (≥10 added across the file
  `test_goto_guard.py` + 1 in `test_config.py` + 1 in
  `test_goto_guard_e2e.py`) pass.
- `uv run ruff check .` clean.
- Default behavior (no env override) restricts `goto`.
- `AGENT_RESTRICT_GOTO=false` reproduces the original unrestricted
  behavior.
- Re-bench results doc committed; comparison table in place.
- For case 104 specifically: the trace shows a `goto_blocked` event
  for `https://arxiv.org/abs/1406.2661` (the prior shortcut path).

## Out of scope (do NOT do in this plan)

- LLM-judge / WebJudge-style detection.
- Restricting click/type.
- Adding `href` to `list_interactive` output (would expand the
  allowlist beyond what the design specifies).
- Replacing the WebVoyager dataset with harder cases.
