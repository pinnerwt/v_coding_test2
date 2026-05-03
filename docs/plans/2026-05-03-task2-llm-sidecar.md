# Task 2 — LLM Sidecar Logging + Cost Analyzer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every LLM call to a per-session sidecar file (`data/traces/<sid>.llm.jsonl`), and ship a CLI analyzer (`scripts/cost_report.py`) that reports cost + token attribution from those sidecars.

**Architecture:** A new `LoggingLLMClient` proxy wraps the existing `LLMClient`, intercepts each `chat()` call, and writes the request + response + usage to a per-session `LLMTraceWriter`. `LLMClient` itself stays unchanged. The proxy is constructed once per session in `server.py` and passed into `ReactLoop` — that single substitution covers all three current `chat()` call-sites (`loop.py`, `force_done.py`, `distill.py`) without modifying any of them. A separate Python script reads the resulting JSONL files and prints a markdown cost report.

**Tech Stack:** Python 3.11+, `httpx`, `pytest` / `pytest-asyncio`, `uv` for env mgmt, `ruff` for lint/format. No new runtime deps.

**Design doc:** [`2026-05-03-task2-llm-sidecar-design.md`](./2026-05-03-task2-llm-sidecar-design.md)

**One refinement vs. the design:** the per-line schema replaces `step_idx` with `call_idx` (auto-incremented per session). The proxy doesn't know loop semantics; the analyzer can correlate to the main trace by timestamp if a particular step matters. This keeps the three call-sites untouched and the proxy stupid.

---

## Task 1: Truncation helper + `LLMTraceWriter` (red first)

**Files:**
- Create: `task2/src/agent/llm_trace.py`
- Create: `task2/tests/unit/test_llm_trace_writer.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_llm_trace_writer.py`:

```python
import json
from pathlib import Path

from agent.llm_trace import LLMTraceWriter, truncate_for_log


def test_truncate_short_string_passes_through():
    assert truncate_for_log("hello") == "hello"


def test_truncate_long_string_returns_dict_with_original_size():
    s = "x" * 15000
    out = truncate_for_log(s)
    assert isinstance(out, dict)
    assert out["truncated"] == "x" * 12000
    assert out["original_chars"] == 15000


def test_truncate_respects_custom_cap():
    out = truncate_for_log("y" * 50, max_chars=10)
    assert out == {"truncated": "y" * 10, "original_chars": 50}


def test_writer_creates_parent_dir_and_appends_jsonl(tmp_path: Path):
    p = tmp_path / "deep" / "nested" / "abc.llm.jsonl"
    w = LLMTraceWriter(p)
    w.write({"call_idx": 0, "request": {"model": "m"}})
    w.write({"call_idx": 1, "request": {"model": "m"}})

    lines = p.read_text().splitlines()
    assert len(lines) == 2
    e0 = json.loads(lines[0])
    e1 = json.loads(lines[1])
    assert e0["call_idx"] == 0
    assert e1["call_idx"] == 1
    # ts auto-stamped if missing
    assert "ts" in e0 and "ts" in e1


def test_writer_preserves_explicit_ts(tmp_path: Path):
    p = tmp_path / "x.llm.jsonl"
    w = LLMTraceWriter(p)
    w.write({"call_idx": 0, "ts": "2026-01-01T00:00:00+00:00"})
    line = p.read_text().splitlines()[0]
    assert json.loads(line)["ts"] == "2026-01-01T00:00:00+00:00"
```

**Step 2: Run test to verify it fails**

```bash
cd task2 && uv run pytest tests/unit/test_llm_trace_writer.py -v
```

Expected: ImportError / ModuleNotFoundError on `agent.llm_trace`.

**Step 3: Write minimal implementation**

Create `task2/src/agent/llm_trace.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRUNCATE_CHARS_DEFAULT = 12000


def truncate_for_log(value: Any, *, max_chars: int = TRUNCATE_CHARS_DEFAULT) -> Any:
    """Truncate a string when it exceeds `max_chars`. Returns the original
    value unchanged if it is not a string or fits under the cap. For
    over-cap strings, returns `{"truncated": <prefix>, "original_chars": N}`
    so an analyzer can still report the true payload size."""
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return {"truncated": value[:max_chars], "original_chars": len(value)}


class LLMTraceWriter:
    """Append-only JSONL writer for per-session LLM call records.
    Mirrors `agent.trace.TraceWriter` deliberately."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        out = dict(event)
        out.setdefault("ts", datetime.now(UTC).isoformat())
        with self._path.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
```

**Step 4: Run test to verify it passes**

```bash
cd task2 && uv run pytest tests/unit/test_llm_trace_writer.py -v
```

Expected: 5 passed.

**Step 5: Lint + commit**

```bash
cd task2 && uv run ruff check . && uv run ruff format --check .
git add task2/src/agent/llm_trace.py task2/tests/unit/test_llm_trace_writer.py
git commit -m "feat(task2): LLMTraceWriter + truncate_for_log helper"
```

---

## Task 2: `LoggingLLMClient` proxy (red first)

**Files:**
- Modify: `task2/src/agent/llm_trace.py` (add proxy class)
- Create: `task2/tests/unit/test_logging_llm_client.py`

**Step 1: Write the failing test**

Create `task2/tests/unit/test_logging_llm_client.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from agent.llm import LLMClient
from agent.llm_trace import LLMTraceWriter, LoggingLLMClient


class _FakeTransport:
    def __init__(self, response_body: dict):
        self.response_body = response_body
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.response_body)


def _make_inner(response_body: dict) -> tuple[LLMClient, _FakeTransport]:
    transport = _FakeTransport(response_body)
    inner = LLMClient(
        base_url="http://test/v1",
        model="deepseek-chat",
        api_key="sk-x",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    return inner, transport


@pytest.mark.asyncio
async def test_proxy_forwards_chat_and_writes_one_sidecar_line(tmp_path: Path):
    inner, _ = _make_inner(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {"name": "done", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
            },
        }
    )
    sidecar = tmp_path / "abc.llm.jsonl"
    writer = LLMTraceWriter(sidecar)
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    msg, usage = await proxy.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "done", "parameters": {}}}],
        tool_choice="required",
    )
    assert usage["prompt_tokens"] == 100

    lines = sidecar.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["call_idx"] == 0
    assert rec["request"]["model"] == "deepseek-chat"
    assert rec["request"]["base_url"] == "http://test/v1"
    assert rec["request"]["messages"] == [{"role": "user", "content": "hi"}]
    assert rec["request"]["tool_choice"] == "required"
    assert rec["response"]["usage"]["prompt_cache_hit_tokens"] == 80
    assert rec["response"]["message"]["tool_calls"][0]["function"]["name"] == "done"
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_proxy_increments_call_idx_per_call(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    writer = LLMTraceWriter(tmp_path / "x.llm.jsonl")
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    await proxy.chat([{"role": "user", "content": "1"}])
    await proxy.chat([{"role": "user", "content": "2"}])
    await proxy.chat([{"role": "user", "content": "3"}])

    recs = [json.loads(line) for line in (tmp_path / "x.llm.jsonl").read_text().splitlines()]
    assert [r["call_idx"] for r in recs] == [0, 1, 2]


@pytest.mark.asyncio
async def test_proxy_truncates_oversize_message_content(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    writer = LLMTraceWriter(tmp_path / "x.llm.jsonl")
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    big = "z" * 20000
    await proxy.chat([{"role": "tool", "content": big, "tool_call_id": "t1"}])

    rec = json.loads((tmp_path / "x.llm.jsonl").read_text().splitlines()[0])
    msg0 = rec["request"]["messages"][0]
    assert isinstance(msg0["content"], dict)
    assert msg0["content"]["truncated"] == "z" * 12000
    assert msg0["content"]["original_chars"] == 20000


@pytest.mark.asyncio
async def test_proxy_swallows_writer_failures(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    class _BoomWriter:
        def write(self, _event):
            raise RuntimeError("disk full")

    failures: list[dict] = []

    class _FakeTrace:
        def write(self, event):
            failures.append(event)

    proxy = LoggingLLMClient(inner=inner, writer=_BoomWriter(), trace=_FakeTrace())
    msg, _ = await proxy.chat([{"role": "user", "content": "hi"}])
    assert msg["content"] == "ok"  # call still succeeds
    assert len(failures) == 1
    assert failures[0]["type"] == "llm_log_failed"
    assert "disk full" in failures[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_proxy_aclose_forwards_to_inner():
    closed = {"flag": False}

    class _FakeInner:
        async def chat(self, *a, **k):
            return {"content": "ok"}, None

        async def aclose(self):
            closed["flag"] = True

    proxy = LoggingLLMClient(inner=_FakeInner(), writer=None)  # writer optional in this path
    await proxy.aclose()
    assert closed["flag"] is True
```

**Step 2: Run test to verify it fails**

```bash
cd task2 && uv run pytest tests/unit/test_logging_llm_client.py -v
```

Expected: ImportError on `LoggingLLMClient`.

**Step 3: Write minimal implementation**

Append to `task2/src/agent/llm_trace.py`:

```python
import time
from typing import Any, Protocol


class _ChatLike(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]: ...

    async def aclose(self) -> None: ...


class _TraceLike(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


def _truncate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply truncation to each message's `content` (and tool_call args if
    present). Returns a deep-enough copy so the original passed to the LLM
    is unaffected."""
    out: list[dict[str, Any]] = []
    for m in messages:
        copy = dict(m)
        if "content" in copy:
            copy["content"] = truncate_for_log(copy["content"])
        if "tool_calls" in copy and isinstance(copy["tool_calls"], list):
            new_tcs = []
            for tc in copy["tool_calls"]:
                tc_copy = dict(tc)
                if "function" in tc_copy and isinstance(tc_copy["function"], dict):
                    fn_copy = dict(tc_copy["function"])
                    if "arguments" in fn_copy:
                        fn_copy["arguments"] = truncate_for_log(fn_copy["arguments"])
                    tc_copy["function"] = fn_copy
                new_tcs.append(tc_copy)
            copy["tool_calls"] = new_tcs
        out.append(copy)
    return out


class LoggingLLMClient:
    """Wraps an `LLMClient`-shaped object, forwarding `chat()` calls while
    writing a sidecar record per call. The inner client stays
    provider-agnostic and unaware of the logger. Logging failures are
    swallowed and reported into the main trace as `llm_log_failed`."""

    def __init__(
        self,
        *,
        inner: _ChatLike,
        writer: Any,  # LLMTraceWriter or duck-typed; None disables logging
        trace: _TraceLike | None = None,
    ):
        self._inner = inner
        self._writer = writer
        self._trace = trace
        self._call_idx = 0
        # Best-effort introspection so the sidecar can record where the
        # request was sent without poking inner-client internals at call time.
        self._model = getattr(inner, "_model", None)
        self._base_url = getattr(inner, "_base_url", None)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        idx = self._call_idx
        self._call_idx += 1
        t0 = time.monotonic()
        msg, usage = await self._inner.chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            temperature=temperature,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        if self._writer is not None:
            try:
                self._writer.write(
                    {
                        "call_idx": idx,
                        "latency_ms": latency_ms,
                        "request": {
                            "model": self._model,
                            "base_url": self._base_url,
                            "messages": _truncate_messages(messages),
                            "tools": tools,
                            "tool_choice": tool_choice,
                            "temperature": temperature,
                        },
                        "response": {
                            "message": msg,
                            "usage": usage,
                        },
                    }
                )
            except Exception as e:
                if self._trace is not None:
                    try:
                        self._trace.write(
                            {"type": "llm_log_failed", "payload": {"error": str(e)[:200]}}
                        )
                    except Exception:
                        pass
        return msg, usage

    async def aclose(self) -> None:
        await self._inner.aclose()
```

**Step 4: Run test to verify it passes**

```bash
cd task2 && uv run pytest tests/unit/test_logging_llm_client.py -v
```

Expected: 5 passed.

**Step 5: Lint + commit**

```bash
cd task2 && uv run ruff check . && uv run ruff format --check .
git add task2/src/agent/llm_trace.py task2/tests/unit/test_logging_llm_client.py
git commit -m "feat(task2): LoggingLLMClient proxy with per-call sidecar write"
```

---

## Task 3: Wire the proxy in `server.py` (red first)

**Files:**
- Modify: `task2/src/agent/server.py:42-72`
- Create: `task2/tests/integration/test_session_emits_llm_sidecar.py`

**Step 1: Inspect existing integration test patterns**

```bash
cd task2 && grep -l "run_sync\|/api/run_sync\|app_factory" tests/integration/ src/agent/server.py
```

Use `tests/integration/test_session_api.py` as the template for the new integration test (same fixture pattern, same TestClient setup).

**Step 2: Write the failing test**

Create `task2/tests/integration/test_session_emits_llm_sidecar.py`:

```python
"""Integration: a session run produces an `<sid>.llm.jsonl` sidecar
containing one record per LLM call, with usage passed through verbatim."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.server import app_factory


def _canned_responses():
    """Single-call session: model returns done() immediately."""
    yield {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "done",
                                "arguments": json.dumps(
                                    {"thought": "trivial", "status": "success", "answer": "ok"}
                                ),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {
            "prompt_tokens": 42,
            "completion_tokens": 7,
            "total_tokens": 49,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 12,
        },
    }
    # Distillation call (post-done): plain text response.
    while True:
        yield {
            "choices": [{"message": {"role": "assistant", "content": "- page-fact one"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


@pytest.fixture
def llm_transport():
    gen = _canned_responses()

    async def handler(request):
        return httpx.Response(200, json=next(gen))

    return httpx.MockTransport(handler)


def test_run_sync_writes_sidecar_with_usage_passthrough(
    tmp_path: Path, monkeypatch, llm_transport
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    app = app_factory(data_dir=tmp_path, llm_transport=llm_transport)
    client = TestClient(app)

    r = client.post("/api/run_sync", json={"goal": "say hi"})
    assert r.status_code == 200
    sid = r.json()["sid"]

    sidecar = tmp_path / "traces" / f"{sid}.llm.jsonl"
    assert sidecar.exists(), f"expected sidecar at {sidecar}"

    lines = sidecar.read_text().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert first["call_idx"] == 0
    assert first["request"]["model"] == "deepseek-chat"
    assert first["response"]["usage"]["prompt_cache_hit_tokens"] == 30
    assert first["response"]["usage"]["prompt_cache_miss_tokens"] == 12
```

> **Note:** if `app_factory` does not currently accept `llm_transport` or `data_dir` kwargs in the exact form shown, look at `tests/integration/test_session_api.py` for the actual surface and adjust the fixture wiring. The substance of the assertion does not change.

**Step 3: Run test to verify it fails**

```bash
cd task2 && uv run pytest tests/integration/test_session_emits_llm_sidecar.py -v
```

Expected: AssertionError that `sidecar.exists()` is False — the proxy is not yet wired in.

**Step 4: Wire the proxy in `server.py`**

Edit `task2/src/agent/server.py`:

1. Add import near the top, with the other `agent.*` imports:
   ```python
   from agent.llm_trace import LLMTraceWriter, LoggingLLMClient
   ```
2. Inside `run_loop` (currently `server.py:38-72`), after `agent_llm = LLMClient(...)` and **before** `loop = ReactLoop(llm=agent_llm, ...)`, insert:
   ```python
   llm_trace = LLMTraceWriter(data_dir / "traces" / f"{session_id}.llm.jsonl")
   agent_llm = LoggingLLMClient(inner=agent_llm, writer=llm_trace, trace=trace)
   ```
   (The `trace` here is the existing `TraceWriter`, so logger failures land in the main trace as `llm_log_failed`.)

**Step 5: Run test to verify it passes**

```bash
cd task2 && uv run pytest tests/integration/test_session_emits_llm_sidecar.py -v
```

Expected: PASS.

**Step 6: Run the full suite to catch regressions**

```bash
cd task2 && uv run pytest -x
```

Expected: all tests pass. If any test that constructs `LLMClient` directly is now breaking because it expects a bare `LLMClient`, that's fine — the proxy only wraps in the server layer; unit tests still get the raw client.

**Step 7: Lint + commit**

```bash
cd task2 && uv run ruff check . && uv run ruff format --check .
git add task2/src/agent/server.py task2/tests/integration/test_session_emits_llm_sidecar.py
git commit -m "feat(task2): wire LoggingLLMClient into per-session runs"
```

---

## Task 4: Cost analyzer — `scripts/cost_report.py` (red first)

**Files:**
- Create: `task2/scripts/cost_report.py`
- Create: `task2/tests/unit/test_cost_report.py`
- Create: `task2/tests/fixtures/sidecar_minimal.llm.jsonl`

**Step 1: Create the fixture sidecar**

Create `task2/tests/fixtures/sidecar_minimal.llm.jsonl` (two calls, mixed cache hit/miss, one truncated content):

```jsonl
{"ts":"2026-05-03T00:00:00+00:00","call_idx":0,"latency_ms":100,"request":{"model":"deepseek-chat","base_url":"https://api.deepseek.com","messages":[{"role":"system","content":"sys"},{"role":"user","content":"hi"}],"tools":null,"tool_choice":null,"temperature":0.2},"response":{"message":{"role":"assistant","content":"ok"},"usage":{"prompt_tokens":1000,"completion_tokens":100,"total_tokens":1100,"prompt_cache_hit_tokens":800,"prompt_cache_miss_tokens":200}}}
{"ts":"2026-05-03T00:00:01+00:00","call_idx":1,"latency_ms":200,"request":{"model":"deepseek-chat","base_url":"https://api.deepseek.com","messages":[{"role":"system","content":"sys"},{"role":"user","content":"hi"},{"role":"assistant","content":"ok"},{"role":"tool","content":{"truncated":"xxxxxxxxxx","original_chars":50000},"tool_call_id":"t1"}],"tools":null,"tool_choice":null,"temperature":0.2},"response":{"message":{"role":"assistant","content":"done"},"usage":{"prompt_tokens":2000,"completion_tokens":50,"total_tokens":2050,"prompt_cache_hit_tokens":1500,"prompt_cache_miss_tokens":500}}}
```

**Step 2: Write the failing test**

Create `task2/tests/unit/test_cost_report.py`:

```python
from pathlib import Path

from scripts.cost_report import (
    DEFAULT_PRICES,
    analyze_sidecar,
    price_usage,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sidecar_minimal.llm.jsonl"


def test_price_usage_splits_cache_hit_and_miss():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 200,
    }
    prices = {"input_miss": 0.27, "input_hit": 0.07, "output": 1.10}
    # 200 miss * 0.27 / 1e6 + 800 hit * 0.07 / 1e6 + 100 out * 1.10 / 1e6
    expected = (200 * 0.27 + 800 * 0.07 + 100 * 1.10) / 1_000_000
    assert price_usage(usage, prices) == pytest_approx(expected)


def test_price_usage_falls_back_to_prompt_tokens_when_no_cache_split():
    """Some providers don't return cache hit/miss — treat all as miss."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    prices = {"input_miss": 0.27, "input_hit": 0.07, "output": 1.10}
    expected = (1000 * 0.27 + 100 * 1.10) / 1_000_000
    assert price_usage(usage, prices) == pytest_approx(expected)


def test_analyze_sidecar_aggregates_calls_and_tokens():
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    assert report["calls"] == 2
    assert report["prompt_tokens"] == 3000
    assert report["completion_tokens"] == 150
    assert report["cache_hit_tokens"] == 2300
    assert report["cache_miss_tokens"] == 700
    assert report["usd"] > 0


def test_analyze_sidecar_role_attribution_sums_to_one():
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    pcts = report["role_pct"]
    assert set(pcts.keys()) >= {"system", "user", "assistant", "tool"}
    assert sum(pcts.values()) == pytest_approx(1.0, abs=1e-6)


def test_analyze_sidecar_uses_original_chars_for_truncated_content():
    """Truncated `tool` content must contribute its original_chars,
    not the truncated prefix length, to role attribution."""
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    # The fixture's only `tool` message has original_chars=50000.
    # The system messages total 6 chars (3 chars × 2 calls).
    # So `tool` should dominate role share.
    assert report["role_pct"]["tool"] > report["role_pct"]["system"]


# Pytest-approx shim so the test file is self-contained.
def pytest_approx(value, abs=None):  # noqa: A002
    import pytest

    return pytest.approx(value, abs=abs) if abs is not None else pytest.approx(value)
```

**Step 3: Run test to verify it fails**

```bash
cd task2 && uv run pytest tests/unit/test_cost_report.py -v
```

Expected: ImportError on `scripts.cost_report`.

**Step 4: Write minimal implementation**

Create `task2/scripts/__init__.py` (empty) so `scripts` is importable as a package by tests, then create `task2/scripts/cost_report.py`:

```python
"""Cost + token-attribution report from per-session LLM sidecars.

Usage:
    uv run python scripts/cost_report.py --session <sid>
    uv run python scripts/cost_report.py --all
    uv run python scripts/cost_report.py --session <sid> --prices prices.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Per 1M tokens. Verify against current DeepSeek pricing before quoting numbers
# externally — these are best-effort defaults for the analyzer fallback.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek/deepseek-chat": {
        "input_miss": 0.27,
        "input_hit": 0.07,
        "output": 1.10,
    },
}


def price_usage(usage: dict[str, Any], prices: dict[str, float]) -> float:
    """USD for one call's `usage` dict. Honors `prompt_cache_hit_tokens` /
    `prompt_cache_miss_tokens` when present; otherwise treats the full
    `prompt_tokens` as miss."""
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        hit_n, miss_n = 0, prompt
    else:
        hit_n = int(hit or 0)
        miss_n = int(miss or 0)
    return (
        miss_n * prices["input_miss"]
        + hit_n * prices["input_hit"]
        + completion * prices["output"]
    ) / 1_000_000


def _content_len(content: Any) -> int:
    """Return the original character length of a message's content,
    accounting for the truncation marker shape."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, dict) and "original_chars" in content:
        return int(content["original_chars"])
    if isinstance(content, list):
        return sum(_content_len(part.get("text", "")) for part in content if isinstance(part, dict))
    return 0


def _prices_for(model: str | None, base_url: str | None, table: dict) -> dict[str, float]:
    """Look up prices by `provider/model`. Provider derived from base_url
    host (first token before the first '.'). Falls back to deepseek-chat
    defaults if the lookup misses."""
    provider = "deepseek"
    if base_url:
        host = base_url.split("//", 1)[-1].split("/", 1)[0]
        if host:
            provider = host.split(".")[0]
    key = f"{provider}/{model}" if model else None
    if key and key in table:
        return table[key]
    return table.get("deepseek/deepseek-chat", {"input_miss": 0.0, "input_hit": 0.0, "output": 0.0})


def analyze_sidecar(path: Path, *, prices_table: dict) -> dict[str, Any]:
    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    hit_tokens = 0
    miss_tokens = 0
    usd = 0.0
    role_chars: dict[str, int] = {"system": 0, "user": 0, "assistant": 0, "tool": 0}

    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        calls += 1
        usage = rec.get("response", {}).get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        hit_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        miss_tokens += int(usage.get("prompt_cache_miss_tokens", 0) or 0)
        prices = _prices_for(
            rec.get("request", {}).get("model"),
            rec.get("request", {}).get("base_url"),
            prices_table,
        )
        usd += price_usage(usage, prices)
        for m in rec.get("request", {}).get("messages", []) or []:
            role = m.get("role") or "unknown"
            if role not in role_chars:
                role_chars[role] = 0
            role_chars[role] += _content_len(m.get("content"))

    total_chars = sum(role_chars.values()) or 1
    role_pct = {r: c / total_chars for r, c in role_chars.items()}

    return {
        "calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": hit_tokens,
        "cache_miss_tokens": miss_tokens,
        "usd": usd,
        "role_pct": role_pct,
    }


def _format_report(sid: str, report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Session {sid}")
    lines.append(
        f"  {report['calls']} calls · "
        f"{report['prompt_tokens']:,} prompt / {report['completion_tokens']:,} completion · "
        f"${report['usd']:.4f}"
    )
    p = report["prompt_tokens"] or 1
    hit_rate = report["cache_hit_tokens"] / p
    lines.append(
        f"  Cache hit: {hit_rate * 100:.1f}% "
        f"({report['cache_hit_tokens']:,} / {p:,} prompt tokens)"
    )
    lines.append("  Role attribution (by message-content chars):")
    for role in sorted(report["role_pct"], key=lambda r: -report["role_pct"][r]):
        pct = report["role_pct"][role] * 100
        lines.append(f"    {role:<12}: {pct:5.1f}%")
    return "\n".join(lines)


def _load_prices(path: str | None) -> dict:
    if not path:
        return DEFAULT_PRICES
    user_table = json.loads(Path(path).read_text())
    return {**DEFAULT_PRICES, **user_table}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="Session id (filename stem)")
    g.add_argument("--all", action="store_true", help="Report on every sidecar in data/traces/")
    ap.add_argument("--prices", help="Path to prices.json (overrides defaults per key)")
    ap.add_argument(
        "--traces-dir",
        default="data/traces",
        help="Directory containing <sid>.llm.jsonl files (default: data/traces)",
    )
    args = ap.parse_args()

    prices = _load_prices(args.prices)
    traces_dir = Path(args.traces_dir)

    if args.session:
        path = traces_dir / f"{args.session}.llm.jsonl"
        if not path.exists():
            print(f"No sidecar at {path}")
            return 1
        print(_format_report(args.session, analyze_sidecar(path, prices_table=prices)))
        return 0

    found = sorted(traces_dir.glob("*.llm.jsonl"))
    if not found:
        print(f"No sidecars in {traces_dir}")
        return 1
    for p in found:
        sid = p.name.removesuffix(".llm.jsonl")
        print(_format_report(sid, analyze_sidecar(p, prices_table=prices)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 5: Run test to verify it passes**

```bash
cd task2 && uv run pytest tests/unit/test_cost_report.py -v
```

Expected: 5 passed.

**Step 6: Smoke-test the CLI against the fixture**

```bash
cd task2 && uv run python scripts/cost_report.py --session sidecar_minimal --traces-dir tests/fixtures
```

Expected: a markdown-ish report with 2 calls, 3000 prompt tokens, role attribution where `tool` dominates.

**Step 7: Lint + commit**

```bash
cd task2 && uv run ruff check . && uv run ruff format --check .
git add task2/scripts/__init__.py task2/scripts/cost_report.py task2/tests/unit/test_cost_report.py task2/tests/fixtures/sidecar_minimal.llm.jsonl
git commit -m "feat(task2): cost_report.py — per-session sidecar analyzer with role attribution"
```

---

## Task 5: README pointer

**Files:**
- Modify: `task2/README.md`

**Step 1: Add a short subsection under "Storage" documenting the sidecar**

In `task2/README.md`, after the line describing `data/traces/<session_id>.jsonl`, add:

```markdown
- `data/traces/<session_id>.llm.jsonl` — one JSON line per LLM call (request, response, usage, latency). Developer-only; not read by the UI. Use `scripts/cost_report.py` to summarize cost and per-role token attribution: `uv run python scripts/cost_report.py --session <sid>` or `--all`.
```

**Step 2: Verify the file still renders sanely**

```bash
cd task2 && head -100 README.md
```

**Step 3: Commit**

```bash
git add task2/README.md
git commit -m "docs(task2): document llm.jsonl sidecar + cost_report.py in README"
```

---

## Task 6: Final verification

**Step 1: Run the full test suite**

```bash
cd task2 && uv run pytest
```

Expected: all green.

**Step 2: Lint + format check**

```bash
cd task2 && uv run ruff check . && uv run ruff format --check .
```

Expected: no findings.

**Step 3: End-to-end sanity (optional, requires a real DeepSeek key)**

If `DEEPSEEK_API_KEY` is set in the environment, run a real one-step session:

```bash
cd task2 && uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8000 &
sleep 2
curl -sS -X POST http://127.0.0.1:8000/api/run_sync \
  -H 'content-type: application/json' \
  -d '{"goal":"go to https://example.com and tell me the top heading"}' | tee /tmp/run.json
SID=$(jq -r .sid /tmp/run.json)
ls -la data/traces/$SID.*
uv run python scripts/cost_report.py --session $SID
```

Expected: `data/traces/$SID.jsonl` and `data/traces/$SID.llm.jsonl` both exist; the cost report prints non-zero `usd` and a role attribution where `system` and `user` together are <50%.

(Kill the uvicorn process when done.)

---

## Out of scope (do not implement here)

- No UI changes — sidecars are developer-only. (Even adding "show LLM call count" to the SPA bottom strip is a separate ticket.)
- No cost-reduction lever (sub-agent, tape compaction, smaller model for some call-sites). That decision waits on the data this analyzer produces.
- No retention / rotation of sidecar files.
- No exact-replay tooling. Truncation precludes exact replay; if needed later, that is a separate design.
