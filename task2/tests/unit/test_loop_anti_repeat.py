import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _StubPage:
    url = "https://x.test/"

    def __init__(self, text: str):
        self._text = text

    async def evaluate(self, script: str):
        # Only document.body.innerText is requested; return current text.
        return self._text


class _StubBrowser:
    def __init__(self, text: str):
        self.page = _StubPage(text)

    def set_text(self, text: str):
        self.page._text = text


def _mock_llm_calls(calls):
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


def _build_read_tool(browser, *, read_limit=1600):
    async def read(offset: int = 0, thought: str = ""):
        text = await browser.page.evaluate("document.body.innerText")
        return text[offset : offset + read_limit]

    return Tool(
        "read",
        "read",
        {
            "type": "object",
            "properties": {
                "offset": {"type": "integer"},
                "thought": {"type": "string"},
            },
        },
        read,
    )


@pytest.mark.asyncio
async def test_read_auto_advances_on_repeat(tmp_path):
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    browser = _StubBrowser(text)
    transport = _mock_llm_calls(
        [
            ("read", {"offset": 0, "thought": "first read"}),
            ("read", {"offset": 0, "thought": "second read at same offset"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
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
        notes=None,
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Step 0 obs is the 'A'*1600 window.
    # Step 1 (the second read at offset=0) MUST have been auto-advanced to 1600 → 'B'*1600.
    step1_obs = loop.tape[1]["obs"]
    assert step1_obs.startswith("[auto-advanced 0→1600")
    assert ("B" * 1600) in step1_obs
