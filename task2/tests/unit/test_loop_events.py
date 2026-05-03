import json
from unittest.mock import AsyncMock

import pytest

from agent.loop import ReactLoop
from agent.tools.meta import LoopDone, QuestionChannel
from agent.tools.registry import Tool, ToolRegistry


class _FakeBrowser:
    class _P:
        url = ""

    page = _P()


class _FakeTrace:
    def __init__(self):
        self.events = []

    def write(self, ev):
        self.events.append(ev)


@pytest.mark.asyncio
async def test_loop_emits_usage_and_llm_call_start():
    llm = AsyncMock()
    llm.chat.return_value = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {
                        "name": "done",
                        "arguments": json.dumps(
                            {"status": "success", "answer": "ok", "reason": "done"}
                        ),
                    },
                }
            ],
        },
        {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    )

    reg = ToolRegistry()

    async def _done(status, answer):
        raise LoopDone(status, answer)

    reg.register(
        Tool(
            "done",
            "finish",
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["status", "answer"],
            },
            _done,
        )
    )

    trace = _FakeTrace()
    transients = []

    async def send_transient(ev):
        transients.append(ev)

    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_FakeBrowser(),
        question_channel=QuestionChannel(),
        max_steps=3,
        send_transient=send_transient,
    )
    result = await loop.run("g")
    assert result["status"] == "success"

    types = [e["type"] for e in trace.events]
    assert "usage" in types
    usage_ev = next(e for e in trace.events if e["type"] == "usage")
    assert usage_ev["payload"] == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }

    assert transients == [{"type": "llm_call_start"}]
