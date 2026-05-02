import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop, _check_read_grep_grounding
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter, read_trace


class _StubBrowser:
    class _Page:
        url = "https://a.test/"

    page = _Page()


def test_pattern_in_goal_allowed():
    assert _check_read_grep_grounding("2018", "Who won gold in 2018?", None) is None


def test_pattern_not_in_goal_no_last_read_blocked():
    err = _check_read_grep_grounding("9", "What is the integral of x squared from 0 to 3?", None)
    assert err is not None
    assert "list_interactive" in err
    assert "read" in err


def test_pattern_in_last_read_obs_allowed_for_other_language():
    # EN goal, JP page just read.
    err = _check_read_grep_grounding(
        "人口",
        "What is the population of Tokyo?",
        "東京の人口は約1400万人です。",
    )
    assert err is None


def test_pattern_not_in_goal_not_in_last_read_blocked():
    err = _check_read_grep_grounding(
        "9",
        "What is the integral of x squared from 0 to 3?",
        "Definite integral: Step-by-step solution Visual representation",
    )
    assert err is not None


def test_case_insensitive_against_goal():
    assert _check_read_grep_grounding("PARIS", "What is the capital of France?", None) is not None
    assert _check_read_grep_grounding("paris", "What is the capital of PARIS region?", None) is None


def _mock_calls(calls):
    it = iter(calls)

    async def handler(request):
        nxt = next(it)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "thinking",
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


@pytest.mark.asyncio
async def test_loop_blocks_ungrounded_read_grep_and_skips_dispatch(tmp_path):
    """Case-112 regression: read_grep('9') with no '9' in goal or prior read obs
    must not reach the browser tool; obs is a synthetic error routing the agent
    to read/list_interactive."""
    called = {"n": 0}

    async def fake_read_grep(pattern: str, window: int = 200) -> str:
        called["n"] += 1
        return "should not be called"

    async def fake_done(status: str, answer: str):
        from agent.tools.meta import LoopDone

        raise LoopDone(status=status, answer=answer)

    transport = _mock_calls(
        [
            ("read_grep", {"pattern": "9", "thought": "looking for the answer"}),
            ("done", {"status": "failed", "answer": "blocked"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    reg.register(
        Tool(
            "read_grep",
            "grep",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "window": {"type": "integer", "default": 200},
                    "thought": {"type": "string"},
                },
                "required": ["pattern"],
            },
            fake_read_grep,
        )
    )
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
            fake_done,
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    qc = QuestionChannel()
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    out = await loop.run(
        "What is the integral of x squared from 0 to 3 according to Wolfram Alpha?"
    )
    assert called["n"] == 0, "read_grep must be intercepted before dispatch"
    assert out["status"] == "failed"
    events = [e for e in read_trace(tmp_path / "t.jsonl") if e["type"] == "step"]
    grep_step = events[0]
    assert grep_step["payload"]["action"] == "read_grep"
    assert "list_interactive" in grep_step["payload"]["obs"]


@pytest.mark.asyncio
async def test_loop_allows_grounded_read_grep_via_goal(tmp_path):
    called = {"n": 0}

    async def fake_read_grep(pattern: str, window: int = 200) -> str:
        called["n"] += 1
        return f"... {pattern} context ..."

    async def fake_done(status: str, answer: str):
        from agent.tools.meta import LoopDone

        raise LoopDone(status=status, answer=answer)

    transport = _mock_calls(
        [
            ("read_grep", {"pattern": "2018", "thought": "jump to 2018"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    reg.register(
        Tool(
            "read_grep",
            "grep",
            {"type": "object", "properties": {"pattern": {"type": "string"}}},
            fake_read_grep,
        )
    )
    reg.register(
        Tool(
            "done",
            "done",
            {"type": "object", "properties": {"status": {"type": "string"}}},
            fake_done,
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    qc = QuestionChannel()
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    out = await loop.run("Who won gold in 2018 at the IOC tournament?")
    assert called["n"] == 1, "read_grep must dispatch when grounded by goal"
    assert out["status"] == "success"
