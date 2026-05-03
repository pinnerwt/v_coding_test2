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


def test_to_openai_tools_filtered_excludes_named_tools():
    from agent.tools.registry import Tool, ToolRegistry

    async def noop(**_):
        return ""

    reg = ToolRegistry()
    reg.register(Tool("read", "r", {"type": "object"}, noop))
    reg.register(Tool("press_key", "p", {"type": "object"}, noop))
    reg.register(Tool("click", "c", {"type": "object"}, noop))

    full = reg.to_openai_tools()
    filtered = reg.to_openai_tools_filtered(exclude={"press_key"})
    assert {t["function"]["name"] for t in full} == {"read", "press_key", "click"}
    assert {t["function"]["name"] for t in filtered} == {"read", "click"}


def test_to_openai_tools_filtered_empty_exclude_returns_all():
    from agent.tools.registry import Tool, ToolRegistry

    async def noop(**_):
        return ""

    reg = ToolRegistry()
    reg.register(Tool("read", "r", {"type": "object"}, noop))
    assert reg.to_openai_tools_filtered(exclude=set()) == reg.to_openai_tools()


def test_every_tool_schema_requires_reason():
    """Every tool — browser + meta — must have `reason` in both `properties`
    and `required`. This is the structural contract that the loop relies on
    to extract the agent's verbalised intent for each action."""
    from agent.browser_session import BrowserSession  # noqa: F401
    from agent.tools.browser import build_browser_tool_list
    from agent.tools.meta import QuestionChannel, build_meta_tool_list

    qc = QuestionChannel()
    # Use None for session - we're only inspecting schemas, not invoking handlers.
    browser_tools = build_browser_tool_list(session=None)
    meta_tools = build_meta_tool_list(question_channel=qc, reason_log=[])

    for tool in browser_tools + meta_tools:
        params = tool.parameters
        assert "reason" in params.get("properties", {}), (
            f"{tool.name}: missing reason in properties"
        )
        assert params["properties"]["reason"]["type"] == "string", (
            f"{tool.name}: reason field must be type=string"
        )
        assert "reason" in params.get("required", []), (
            f"{tool.name}: reason must be in required list"
        )
        assert "thought" not in params.get("properties", {}), (
            f"{tool.name}: legacy `thought` field should be removed"
        )
