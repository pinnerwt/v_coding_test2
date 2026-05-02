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
async def test_basic_chat_no_extra_body():
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
    # extra_body was vLLM-specific (Qwen thinking-mode) and is dropped
    # so hosted providers (DeepSeek, OpenAI) don't reject it.
    assert "extra_body" not in body
    assert msg["content"] == "ok"


@pytest.mark.asyncio
async def test_api_key_sets_authorization_header():
    import httpx

    transport = _FakeTransport()
    client = LLMClient(
        base_url="http://test/v1",
        model="m",
        api_key="sk-test-abc",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    await client.chat([{"role": "user", "content": "hi"}])
    sent = transport.last_request
    assert sent.headers.get("authorization") == "Bearer sk-test-abc"


@pytest.mark.asyncio
async def test_no_api_key_means_no_authorization_header():
    import httpx

    transport = _FakeTransport()
    client = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    await client.chat([{"role": "user", "content": "hi"}])
    sent = transport.last_request
    assert "authorization" not in {k.lower() for k in sent.headers}


@pytest.mark.asyncio
async def test_http_error_surfaces_response_body():
    import httpx

    body_text = (
        '{"error":{"message":"Messages with role tool must respond to tool_calls",'
        '"code":"invalid_request_error"}}'
    )

    async def handler(request):
        return httpx.Response(400, text=body_text)

    client = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.chat([{"role": "user", "content": "hi"}])
    assert "400" in str(exc_info.value)
    assert "Messages with role tool" in str(exc_info.value)


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
