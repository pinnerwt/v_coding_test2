import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _StubBrowser:
    class _Page:
        url = "https://a.test/"

    page = _Page()


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


def _build_registry():
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["status", "answer", "reason"],
            },
            meta["done"],
        )
    )

    async def noopA(**kwargs):
        return "ok"

    reg.register(
        Tool(
            "noopA",
            "no-op tool",
            {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
            noopA,
        )
    )
    return reg, qc


@pytest.mark.asyncio
async def test_reason_extracted_from_args_into_tape(tmp_path):
    """When the LLM emits a tool call with `reason: "X"` in args, the tape entry
    has tape[i]['reason'] == "X" and reason is removed from args."""
    transport = _mock_calls(
        [
            ("noopA", {"reason": "I want to test something."}),
            ("done", {"status": "success", "answer": "42", "reason": "exit"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg, qc = _build_registry()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    result = await loop.run("test")
    assert result["status"] == "success"
    assert loop.tape[0]["reason"] == "I want to test something."
    assert "reason" not in loop.tape[0]["args"]


@pytest.mark.asyncio
async def test_missing_reason_produces_error_obs(tmp_path):
    """If the LLM emits a tool call without `reason`, the loop appends an error
    obs to the tape and continues. The next turn must succeed."""
    transport = _mock_calls(
        [
            ("noopA", {}),
            ("done", {"status": "success", "answer": "ok", "reason": "test exit"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg, qc = _build_registry()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    result = await loop.run("test")
    assert result["status"] == "success"
    assert result["answer"] == "ok"
    assert "reason" in loop.tape[0]["obs"].lower()
    # First tape entry is the error obs from the missing-reason call;
    # done() raises LoopDone and does not append to tape, so the loop did not
    # crash if we got here with a success result.
    assert loop.tape[0]["action"] == "noopA"
    assert loop.tape[0]["reason"] == ""
