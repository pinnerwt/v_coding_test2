import json

import httpx
import pytest

from agent.force_done import coerce_done_via_llm
from agent.llm import LLMClient

_DONE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "done",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["status", "answer"],
        },
    },
}


def _llm_returning(name: str, args: dict) -> LLMClient:
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
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_coerce_done_returns_llm_done_tool_call():
    llm = _llm_returning("done", {"status": "success", "answer": "the answer"})
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="any goal",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "success", "answer": "the answer"}
