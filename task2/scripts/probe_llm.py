"""Send the exact payload the agent's first turn would send, dump the result."""

import asyncio
import json
import sys

import httpx

from agent.context import build_messages
from agent.llm import LLMClient
from agent.tools.browser import build_browser_tool_list
from agent.tools.meta import QuestionChannel, build_meta_tool_list
from agent.tools.registry import ToolRegistry


async def main():
    # Skip browser, just get the schemas
    class _FakeBrowser:
        class _P:
            url = ""

        page = _P()

    reg = ToolRegistry()
    for t in build_browser_tool_list(_FakeBrowser()):
        reg.register(t)
    for t in build_meta_tool_list(question_channel=QuestionChannel()):
        reg.register(t)
    tools = reg.to_openai_tools()
    print(f"tool count: {len(tools)}", file=sys.stderr)
    print(f"tool names: {[t['function']['name'] for t in tools]}", file=sys.stderr)

    msgs = build_messages(
        system=(
            "You are a web-browsing ReAct agent. Each turn, pick exactly one tool to call. "
            "Always include a brief `reason` argument explaining your choice. Element IDs "
            "come from list_interactive — never invent CSS selectors. Call done(status, "
            "answer) when the user goal is satisfied or impossible."
        ),
        goal="Go to CanIRun.ai and recommend me the best model that I can host locally",
        qa=[],
        url_notes="",
        tape=[],
        page_header="URL=",
        replan_hint=None,
    )
    print(f"messages: {len(msgs)}", file=sys.stderr)
    print(f"system message len: {len(msgs[0]['content'])} chars", file=sys.stderr)

    payload = {
        "model": "qwen3.5-27b",
        "messages": msgs,
        "temperature": 0.2,
        "tools": tools,
        "tool_choice": "auto",
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }

    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post("http://localhost:8090/v1/chat/completions", json=payload)
        print(f"HTTP {r.status_code}", file=sys.stderr)
        body = r.text
        print(body[:2000])

    # Also try via LLMClient
    llm = LLMClient("http://localhost:8090/v1", "qwen3.5-27b")
    try:
        msg, usage = await llm.chat(msgs, tools=tools, tool_choice="auto")
        print("LLMClient OK:", json.dumps(msg)[:500], file=sys.stderr)
    except Exception as e:
        print(f"LLMClient ERROR: {e}", file=sys.stderr)
    await llm.aclose()


asyncio.run(main())
