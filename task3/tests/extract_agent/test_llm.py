import pytest
from pytest_httpx import HTTPXMock

from extract_agent.llm import LLMClient, ToolNameNotAllowed


@pytest.mark.asyncio
async def test_chat_sends_correct_payload(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.example.com/chat/completions",
        json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        },
    )
    client = LLMClient(base_url="https://api.example.com", model="x", api_key="k")
    msg, usage = await client.chat([{"role": "user", "content": "hello"}])
    assert msg["content"] == "hi"
    assert usage["total_tokens"] == 6
    req = httpx_mock.get_requests()[0]
    body = req.read().decode()
    assert '"model":"x"' in body or '"model": "x"' in body
    assert "Bearer k" in req.headers["Authorization"]
    await client.aclose()


@pytest.mark.asyncio
async def test_tool_name_not_allowed_raises(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.example.com/chat/completions",
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {"name": "bogus", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
    )
    client = LLMClient(base_url="https://api.example.com", model="x", api_key=None)
    with pytest.raises(ToolNameNotAllowed):
        await client.chat(
            [],
            tools=[{"type": "function", "function": {"name": "real", "parameters": {}}}],
        )
    await client.aclose()
