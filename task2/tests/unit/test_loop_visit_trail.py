"""ReactLoop fires `on_visit(url)` once per turn at the top of the iteration,
deduping consecutive identical URLs. The server uses this to maintain a
visited-URL trail."""

from __future__ import annotations

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _Browser:
    def __init__(self, urls: list[str]):
        self._urls = urls
        self._i = 0
        self.page = self  # quack: page.url and page.evaluate

    @property
    def url(self) -> str:
        u = self._urls[min(self._i, len(self._urls) - 1)]
        self._i += 1
        return u

    async def evaluate(self, _expr: str) -> str:
        return ""


@pytest.mark.asyncio
async def test_on_visit_invoked_each_turn_with_dedup(tmp_path):
    seen: list[str] = []
    call_n = {"i": 0}

    async def handler(request):
        call_n["i"] += 1
        # First two turns: no-op tool. Third turn: done().
        name = "noop" if call_n["i"] < 3 else "done"
        args = "{}" if name == "noop" else json.dumps({"status": "success", "answer": "ok"})
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
                                    "function": {"name": name, "arguments": args},
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
        return "noop-obs"

    async def done(status: str, answer: str):
        from agent.tools.meta import LoopDone

        raise LoopDone(status, answer)

    reg.register(Tool("noop", "x", {"type": "object", "properties": {}}, noop))
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
            done,
        )
    )

    # url stream: A, A, B → expect dedup to record [A, B] (consecutive A
    # collapses to one).
    browser = _Browser(["https://a.test/", "https://a.test/", "https://b.test/"])

    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=TraceWriter(tmp_path / "t.jsonl"),
        browser=browser,
        question_channel=QuestionChannel(),
        max_steps=10,
        on_visit=seen.append,
    )
    await loop.run("g")
    assert seen == ["https://a.test/", "https://b.test/"]
