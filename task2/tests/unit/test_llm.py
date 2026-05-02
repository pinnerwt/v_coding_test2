import json

import pytest

from agent.llm import LLMClient


class _FakeTransport:
    def __init__(self):
        self.last_request = None

    async def handle_async_request(self, request):
        import httpx

        self.last_request = request
        body = json.loads(request.content)
        # Echo so test can assert on it
        return httpx.Response(
            200,
            json={
                "_echo": body,
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            },
        )


@pytest.mark.asyncio
async def test_reasoning_disabled_by_default():
    import httpx

    transport = _FakeTransport()
    client = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    msg, _ = await client.chat([{"role": "user", "content": "hi"}])
    sent = transport.last_request
    body = json.loads(sent.content)
    assert body["model"] == "m"
    assert body["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert msg["content"] == "ok"


@pytest.mark.asyncio
async def test_tools_passed_through():
    import httpx

    transport = _FakeTransport()
    client = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    await client.chat([{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto")
    body = json.loads(transport.last_request.content)
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
