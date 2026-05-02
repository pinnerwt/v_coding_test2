import pytest

from agent.tools.registry import Tool, ToolRegistry


@pytest.mark.asyncio
async def test_register_and_call():
    r = ToolRegistry()

    async def handler(*, x: int) -> int:
        return x + 1

    r.register(
        Tool(
            name="inc",
            description="increment",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
            handler=handler,
        )
    )
    out = r.to_openai_tools()
    assert out[0]["function"]["name"] == "inc"
    assert out[0]["function"]["parameters"]["required"] == ["x"]
    result = await r.call("inc", {"x": 1})
    assert result == 2


@pytest.mark.asyncio
async def test_unknown_tool_raises():
    r = ToolRegistry()
    with pytest.raises(KeyError):
        await r.call("nope", {})
