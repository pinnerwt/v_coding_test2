import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter, read_trace


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


@pytest.mark.asyncio
async def test_loop_runs_done(tmp_path):
    transport = _mock_calls([("done", {"status": "success", "answer": "42", "reason": "ok"})])
    llm = LLMClient("http://t/v1", "m", transport=transport)
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
        notes=None,
        trace=trace,
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    out = await loop.run("get the answer")
    assert out == {"status": "success", "answer": "42"}
    events = read_trace(tmp_path / "t.jsonl")
    assert events[-1]["type"] == "done"


def test_react_loop_accepts_anti_loop_config_kwargs(tmp_path):
    from agent.loop import ReactLoop

    # No-op stub for everything; we're only checking the constructor signature.
    class _Stub:
        page = type("P", (), {"url": "https://a.test/"})()

    loop = ReactLoop(
        llm=None,
        registry=None,
        notes=None,
        trace=None,
        browser=_Stub(),
        question_channel=None,
        max_steps=1,
        small_diff_threshold=200,
        max_auto_advance_hops=8,
        diff_inject_max_lines=4,
    )
    assert loop._small_diff_threshold == 200
    assert loop._max_auto_advance_hops == 8
    assert loop._diff_inject_max_lines == 4
