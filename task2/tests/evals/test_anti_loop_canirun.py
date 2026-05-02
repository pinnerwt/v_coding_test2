"""Replay-style eval. Drives the loop with a stub browser whose innerText
mimics the canirun.ai page after the GPU-filter step. Confirms the agent
doesn't loop more than 3 times on read at the same offset before either
auto-advancing past it or the tool being hidden."""

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter

CANIRUN_TEXT = (
    "CanIRun.ai\nupdated 26d ago\n[compare]\n[tier list]\n[docs]\n[why]\n"
    + ("filler line for the canirun.ai eval fixture.\n" * 400)
    + "Gemma 4 E4B IT\nGemma\n5d ago\n4.6\nGB\n19%\nRUNS GREAT\n96/100\n"
)


class _Page:
    url = "https://canirun.ai/device/rtx-3090"

    def __init__(self, text):
        self._t = text

    async def evaluate(self, _):
        return self._t


class _Browser:
    def __init__(self, text):
        self.page = _Page(text)


@pytest.mark.asyncio
async def test_canirun_no_read_loop(tmp_path):
    """If the agent calls read({offset:0}) ten times in a row, the new
    machinery must auto-advance every repeat. By the third repeat the
    served offset must be > 0."""
    browser = _Browser(CANIRUN_TEXT)

    # Script: 10 read({offset:0}) calls, then done().
    scripted = [("read", {"offset": 0, "thought": f"r{i}"}) for i in range(10)]
    scripted.append(("done", {"status": "success", "answer": "Gemma 4 E4B IT"}))

    it = iter(scripted)

    async def handler(request):
        nxt = next(it)
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

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)

    async def read(offset: int = 0, thought: str = ""):
        body = await browser.page.evaluate("")
        return body[offset : offset + 2000]

    reg.register(
        Tool(
            "read",
            "r",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "thought": {"type": "string"},
                },
            },
            read,
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
            meta["done"],
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=15,
    )
    await loop.run("RTX 3090 best LLM?")

    # By the third read in the tape, served offset must have advanced.
    read_steps = [s for s in loop.tape if s["action"] == "read"]
    assert len(read_steps) >= 3
    third = read_steps[2]
    assert third["args"]["offset"] > 0, (
        f"Third read should have been auto-advanced, but args were {third['args']}"
    )
