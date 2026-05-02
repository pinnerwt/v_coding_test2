import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _Browser:
    class _Page:
        url = "https://a.test/"

    page = _Page()


@pytest.mark.asyncio
async def test_replan_hint_after_3_repeats(tmp_path):
    seen_hints = []

    async def handler(request):
        body = json.loads(request.content)
        sys_msg = body["messages"][0]["content"]
        seen_hints.append("REPLAN" in sys_msg)
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
                                        "name": "noop",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noop():
        return "same"

    reg.register(Tool("noop", "x", {"type": "object", "properties": {}}, noop))

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("g")
    assert seen_hints[:3] == [False, False, False]
    assert seen_hints[3] is True
