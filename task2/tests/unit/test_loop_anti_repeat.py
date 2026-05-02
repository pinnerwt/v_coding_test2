import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _StubPage:
    url = "https://x.test/"

    def __init__(self, text: str):
        self._text = text

    async def evaluate(self, script: str):
        # Only document.body.innerText is requested; return current text.
        return self._text


class _StubBrowser:
    def __init__(self, text: str):
        self.page = _StubPage(text)

    def set_text(self, text: str):
        self.page._text = text


def _mock_llm_calls(calls):
    it = iter(calls)

    async def handler(request):
        nxt = next(it)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "thinking",
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

    return httpx.MockTransport(handler)


def _build_read_tool(browser, *, read_limit=1600):
    async def read(offset: int = 0, thought: str = ""):
        text = await browser.page.evaluate("document.body.innerText")
        return text[offset : offset + read_limit]

    return Tool(
        "read",
        "read",
        {
            "type": "object",
            "properties": {
                "offset": {"type": "integer"},
                "thought": {"type": "string"},
            },
        },
        read,
    )


@pytest.mark.asyncio
async def test_read_auto_advances_on_repeat(tmp_path):
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    browser = _StubBrowser(text)
    transport = _mock_llm_calls(
        [
            ("read", {"offset": 0, "thought": "first read"}),
            ("read", {"offset": 0, "thought": "second read at same offset"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(_build_read_tool(browser))
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
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Step 0 obs is the 'A'*1600 window.
    # Step 1 (the second read at offset=0) MUST have been auto-advanced to 1600 → 'B'*1600.
    step1_obs = loop.tape[1]["obs"]
    assert step1_obs.startswith("[auto-advanced 0→1600")
    assert ("B" * 1600) in step1_obs


@pytest.mark.asyncio
async def test_read_hidden_after_end_of_page_until_mutation(tmp_path):
    """When auto-advance walks past len(text), the synthetic obs is
    returned AND `read` must be removed from next turn's tool list. After
    a state-changing tool produces a global diff, `read` is exposed again."""
    short_text = "A" * 1600
    browser = _StubBrowser(short_text)
    transport = _mock_llm_calls(
        [
            ("read", {"offset": 0, "thought": "first read"}),
            ("read", {"offset": 0, "thought": "second read; should auto-advance and exhaust"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    # The third LLM call should NOT see `read` in its tool list. We capture
    # the tool list from the third request via the mock transport.
    captured_tools: list[list[str]] = []

    async def capturing_handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        # Reuse the scripted call sequence:
        return await transport.handler(request)

    capturing_transport = httpx.MockTransport(capturing_handler)

    llm = LLMClient("http://t/v1", "m", transport=capturing_transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(_build_read_tool(browser))
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
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
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Third turn (index 2) should not have `read` in tools.
    assert "read" not in captured_tools[2]


@pytest.mark.asyncio
async def test_small_diff_injected_after_state_change(tmp_path):
    text_v1 = "Sort: Score\nlist of items\n"
    text_v2 = "Sort: Params\nlist of items\n"
    browser = _StubBrowser(text_v1)

    captured_user_prompts: list[str] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        for m in body["messages"]:
            if m["role"] == "user":
                captured_user_prompts.append(m["content"])
        # Scripted: click then done. After click, switch the page text.
        idx = len(captured_user_prompts) - 1
        if idx == 0:
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
                                            "name": "click",
                                            "arguments": json.dumps({"id": 1}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        elif idx == 1:
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
                                            "name": "done",
                                            "arguments": json.dumps(
                                                {"status": "success", "answer": "ok"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)

    async def click(id: int, thought: str = ""):
        browser.set_text(text_v2)  # mutate page
        return f"clicked id={id}"

    reg.register(
        Tool(
            "click",
            "click",
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "thought": {"type": "string"}},
                "required": ["id"],
            },
            click,
        )
    )
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
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
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # The second user prompt (turn index 1) should include the small diff.
    second_prompt = captured_user_prompts[1]
    assert "Page changes since last turn" in second_prompt
    assert "Sort: Params" in second_prompt


@pytest.mark.asyncio
async def test_press_key_hidden_when_no_global_diff(tmp_path):
    text = "static text only\n"
    browser = _StubBrowser(text)

    captured_tools: list[list[str]] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        idx = len(captured_tools) - 1
        if idx == 0:
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
                                            "name": "press_key",
                                            "arguments": json.dumps({"key": "Home"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
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
                                        "name": "done",
                                        "arguments": json.dumps(
                                            {"status": "success", "answer": "ok"}
                                        ),
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
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)

    async def press_key(key: str, thought: str = ""):
        return f"pressed {key}"  # does not mutate browser text

    reg.register(
        Tool(
            "press_key",
            "press",
            {
                "type": "object",
                "properties": {"key": {"type": "string"}, "thought": {"type": "string"}},
                "required": ["key"],
            },
            press_key,
        )
    )
    reg.register(
        Tool(
            "done",
            "done",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}, "answer": {"type": "string"}},
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
        summarizer=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Turn 0: press_key was available. Turn 1: press_key must be hidden because
    # its previous call produced zero global diff.
    assert "press_key" in captured_tools[0]
    assert "press_key" not in captured_tools[1]
