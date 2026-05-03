"""Tests for the post-`done()` distillation step in ReactLoop.

The distill_page_knowledge call must:
  1. Run after a successful done() and overwrite prior notes for the URL.
  2. Run after a failed done() too.
  3. Be isolated: a failure in the distiller must NOT corrupt the user-visible
     result and must emit a `distill_failed` trace event.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.notes_store import NotesStore
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _StubPage:
    url = "https://x.test/"

    def __init__(self, text: str):
        self._text = text

    async def evaluate(self, script: str):
        return self._text


class _StubBrowser:
    def __init__(self, text: str):
        self.page = _StubPage(text)


def _build_read_tool(browser, *, read_limit=1600):
    async def read(offset: int = 0, reason: str = ""):
        text = await browser.page.evaluate("document.body.innerText")
        return text[offset : offset + read_limit]

    return Tool(
        "read",
        "read",
        {
            "type": "object",
            "properties": {
                "offset": {"type": "integer"},
                "reason": {"type": "string"},
            },
        },
        read,
    )


def _scripted_transport(tool_calls, distill_text: str | None, distill_status: int = 200):
    """Transport that serves N tool-call responses (one per item in tool_calls)
    and then a single content-only response (the distillation call). If
    `distill_status` != 200 the distill call returns an error response."""
    it = iter(tool_calls)

    async def handler(request):
        body = json.loads(request.content.decode())
        # Distillation call uses no `tools` key (free-form). Tool-loop calls
        # always have `tools` set.
        has_tools = "tools" in body and body.get("tools")
        if not has_tools:
            if distill_status != 200:
                return httpx.Response(distill_status, text="boom")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": distill_text or ""}}]
                },
            )
        try:
            nxt = next(it)
        except StopIteration as e:
            raise AssertionError("ran out of scripted tool calls") from e
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": nxt[0],
                                        "arguments": json.dumps(nxt[1]),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def _make_loop(*, llm, notes, browser, tmp_path, reason_log=None):
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=notes,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
        reason_log=reason_log if reason_log is not None else [],
    )
    return loop, trace


@pytest.mark.asyncio
async def test_distill_runs_after_done_success(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://x.test/", "stale prior content")
    browser = _StubBrowser("page body")
    transport = _scripted_transport(
        [
            ("read", {"offset": 0, "reason": "look"}),
            ("done", {"status": "success", "answer": "the value", "reason": "x"}),
        ],
        distill_text="- distilled fact A\n- distilled fact B",
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    loop, _trace = _make_loop(llm=llm, notes=notes, browser=browser, tmp_path=tmp_path)
    result = await loop.run("what is the value")
    assert result == {"status": "success", "answer": "the value"}
    out = notes.get("https://x.test/")
    assert "distilled fact A" in out
    assert "distilled fact B" in out
    assert "stale prior content" not in out


@pytest.mark.asyncio
async def test_distill_runs_after_done_failed(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    browser = _StubBrowser("page body")
    transport = _scripted_transport(
        [
            ("done", {"status": "failed", "answer": "blocked", "reason": "x"}),
        ],
        distill_text="- this URL is unreachable behind a wall",
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    loop, _trace = _make_loop(llm=llm, notes=notes, browser=browser, tmp_path=tmp_path)
    result = await loop.run("anything")
    assert result == {"status": "failed", "answer": "blocked"}
    assert "unreachable" in notes.get("https://x.test/")


@pytest.mark.asyncio
async def test_distill_failure_does_not_corrupt_result(tmp_path):
    """The distiller's LLM raises → user-visible result stays byte-identical to
    a no-distill baseline AND a `distill_failed` trace event is emitted."""
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://x.test/", "prior-content")
    browser = _StubBrowser("page body")
    transport = _scripted_transport(
        [
            ("done", {"status": "success", "answer": "kept", "reason": "x"}),
        ],
        distill_text=None,
        distill_status=500,
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    trace_path = tmp_path / "t.jsonl"
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(trace_path)
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=notes,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    result = await loop.run("anything")
    assert result == {"status": "success", "answer": "kept"}
    # Prior notes preserved (distiller failed before set()).
    assert notes.get("https://x.test/") == "prior-content"
    # Trace contains a distill_failed event.
    events = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    assert any(e["type"] == "distill_failed" for e in events)


# --- Force-done distillation tests ---
#
# Distillation must run after EVERY done() — including the synthetic done()
# produced by `coerce_done_via_llm` on the force-done paths. The natural
# LoopDone handler in run() already distills; these tests pin the same
# behavior on the five force-done sites so failure-mode page knowledge
# (Cloudflare walls, CAPTCHAs, dead-ends) is captured.


@pytest.mark.asyncio
async def test_distill_runs_after_max_steps_force_done(tmp_path):
    """max_steps trigger: with max_steps=2, step 0 runs a tool, step 1 (the
    final allowed step) short-circuits to coerce_done_via_llm. Distillation
    must fire on the synthesized done(...) result."""
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://x.test/", "stale prior content")
    browser = _StubBrowser("page body")
    transport = _scripted_transport(
        [
            # Step 0: regular tool call.
            ("read", {"offset": 0, "reason": "look"}),
            # Step 1 = max_steps - 1: coerce_done_via_llm forces done().
            ("done", {"status": "failed", "answer": "ran out of steps", "reason": "x"}),
        ],
        distill_text="- max-steps distilled fact",
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=notes,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=2,
    )
    result = await loop.run("what is the value")
    assert result == {"status": "failed", "answer": "ran out of steps"}
    out = notes.get("https://x.test/")
    assert "max-steps distilled fact" in out
    assert "stale prior content" not in out


@pytest.mark.asyncio
async def test_distill_runs_after_no_progress_force_done(tmp_path):
    """no_progress trigger: alternating noopA/noopB with identical observations
    drives no_progress_streak past NO_PROGRESS_GIVEUP. The force-done synthesis
    must be followed by distillation."""
    notes = NotesStore(tmp_path / "n.db")
    browser = _StubBrowser("page body")

    distill_payload = "- no-progress distilled fact"
    call_idx = {"i": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        has_tools = "tools" in body and body.get("tools")
        if not has_tools:
            # Distillation call (free-form, no tools).
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": distill_payload}}]},
            )
        # Did force-done call coerce_done_via_llm? It pins tool_choice to
        # the named done function. Use that as the discriminator.
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("function", {}).get("name") == "done":
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
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "done",
                                            "arguments": json.dumps(
                                                {
                                                    "status": "failed",
                                                    "answer": "stuck",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        # Regular tool-loop turn: alternate noopA/noopB to defeat
        # _last_three_match while still keeping observations identical.
        name = "noopA" if call_idx["i"] % 2 == 0 else "noopB"
        call_idx["i"] += 1
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": name, "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)

    async def noopA():
        return "same"

    async def noopB():
        return "same"

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(Tool("noopB", "x", {"type": "object", "properties": {}}, noopB))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=notes,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=30,
    )
    result = await loop.run("g")
    assert result == {"status": "failed", "answer": "stuck"}
    out = notes.get("https://x.test/")
    assert "no-progress distilled fact" in out
