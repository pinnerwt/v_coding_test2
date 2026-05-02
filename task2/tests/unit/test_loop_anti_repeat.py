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


@pytest.mark.asyncio
async def test_list_interactive_auto_advances(tmp_path):
    """list_interactive at the same offset twice on an unchanged page should
    auto-advance just like read."""
    # Snapshot is a JSON list. We approximate by returning fixed strings.
    pages = ["snap-A", "snap-B", "snap-C"]
    # Each "offset" maps to a different page in our stub.

    async def list_interactive(offset: int = 0, limit: int = 50, thought: str = ""):
        idx = offset // limit
        if idx >= len(pages):
            return ""
        return pages[idx]

    text = "irrelevant"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls(
        [
            ("list_interactive", {"offset": 0, "limit": 1, "thought": "first"}),
            ("list_interactive", {"offset": 0, "limit": 1, "thought": "second"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(
        Tool(
            "list_interactive",
            "li",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "thought": {"type": "string"},
                },
            },
            list_interactive,
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

    # Step 1 should have been auto-advanced.
    step1 = loop.tape[1]
    assert step1["action"] == "list_interactive"
    assert step1["obs"].startswith("[auto-advanced 0→1")  # advanced by `limit` of 1
    assert "snap-B" in step1["obs"]


@pytest.mark.asyncio
async def test_list_interactive_auto_advance_handles_tool_error(tmp_path):
    """If the underlying list_interactive tool raises during auto-advance,
    the error must be captured as an ERROR obs rather than crashing the loop."""
    browser = _StubBrowser("irrelevant")

    async def list_interactive(offset: int = 0, limit: int = 50, thought: str = ""):
        raise RuntimeError("simulated tool failure")

    transport = _mock_llm_calls(
        [
            ("list_interactive", {"offset": 0, "limit": 1, "thought": "first"}),
            ("done", {"status": "failed", "answer": "tool errored"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(
        Tool(
            "list_interactive",
            "li",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "thought": {"type": "string"},
                },
            },
            list_interactive,
        )
    )
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
    result = await loop.run("anything")
    # Loop did not crash; first-step obs is an ERROR string from the tool.
    assert loop.tape[0]["action"] == "list_interactive"
    assert loop.tape[0]["obs"].startswith("ERROR:")
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_read_grep_dedup_returns_synthetic_when_pattern_repeats(tmp_path):
    text = "the quick brown fox jumps over the lazy dog needle here\n"
    browser = _StubBrowser(text)

    async def read_grep(pattern: str, window: int = 200, thought: str = ""):
        body = await browser.page.evaluate("document.body.innerText")
        idx = body.lower().find(pattern.lower())
        if idx < 0:
            return f"NOT FOUND: {pattern!r}"
        return body[max(0, idx - window) : idx + len(pattern) + window]

    transport = _mock_llm_calls(
        [
            ("read_grep", {"pattern": "needle", "thought": "first grep"}),
            ("read_grep", {"pattern": "needle", "thought": "same pattern again"}),
            ("done", {"status": "success", "answer": "ok"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(notes=None, current_url=lambda: "https://x.test/", question_channel=qc)
    reg.register(
        Tool(
            "read_grep",
            "rg",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "window": {"type": "integer"},
                    "thought": {"type": "string"},
                },
                "required": ["pattern"],
            },
            read_grep,
        )
    )
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

    step1 = loop.tape[1]
    assert step1["action"] == "read_grep"
    assert "already searched" in step1["obs"].lower()
