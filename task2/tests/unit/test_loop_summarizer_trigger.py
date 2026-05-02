from unittest.mock import AsyncMock

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.notes_store import NotesStore
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _Browser:
    def __init__(self):
        self.page = type("P", (), {})()
        self.page.url = "https://a.test/"


@pytest.mark.asyncio
async def test_summarizer_called_on_error(tmp_path):
    async def handler(request):
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
                                        "name": "fail_tool",
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

    async def fail_tool():
        return "ERROR: kaboom"

    reg.register(Tool("fail_tool", "x", {"type": "object", "properties": {}}, fail_tool))

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    qc = QuestionChannel()
    notes = NotesStore(tmp_path / "n.db")
    summarizer = AsyncMock()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=notes,
        summarizer=summarizer,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=2,
    )
    await loop.run("g")
    assert summarizer.maybe_summarize.await_count >= 1
    args = summarizer.maybe_summarize.await_args.kwargs
    assert args["trigger"] == "error"
