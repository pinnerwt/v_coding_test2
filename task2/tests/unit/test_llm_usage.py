import httpx
import pytest

from agent.llm import LLMClient


@pytest.mark.asyncio
async def test_chat_returns_usage_alongside_message():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    client = LLMClient("http://x/v1", "m", transport=httpx.MockTransport(handler))
    msg, usage = await client.chat([{"role": "user", "content": "hello"}])
    assert msg["content"] == "hi"
    assert usage == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_usage_is_none_when_upstream_omits_it():
    async def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        )

    client = LLMClient("http://x/v1", "m", transport=httpx.MockTransport(handler))
    msg, usage = await client.chat([{"role": "user", "content": "hi"}])
    assert msg["content"] == "hi"
    assert usage is None
    await client.aclose()
