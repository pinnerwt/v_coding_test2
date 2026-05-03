import json
from typing import Any

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
    async def read(offset: int = 0, reason: str = ""):
        text = await browser.page.evaluate("document.body.innerText")
        return text[offset : offset + read_limit]

    return Tool(
        "read",
        "read",
        {
            "type": "object",
            "properties": {
                "offset": {"type": "integer"},
                "reason": {"type": "string"},
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
            ("read", {"offset": 0, "reason": "first read"}),
            ("read", {"offset": 0, "reason": "second read at same offset"}),
            ("done", {"status": "success", "answer": "ok", "reason": "x"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
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
            ("read", {"offset": 0, "reason": "first read"}),
            ("read", {"offset": 0, "reason": "second read; should auto-advance and exhaust"}),
            ("done", {"status": "success", "answer": "ok", "reason": "x"}),
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
    meta = build_meta_tools(question_channel=qc)
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
                                            "arguments": json.dumps({"id": 1, "reason": "click"}),
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
                                                {
                                                    "status": "success",
                                                    "answer": "ok",
                                                    "reason": "done",
                                                }
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
    meta = build_meta_tools(question_channel=qc)

    async def click(id: int, reason: str = ""):
        browser.set_text(text_v2)  # mutate page
        return f"clicked id={id}"

    reg.register(
        Tool(
            "click",
            "click",
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
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
    meta = build_meta_tools(question_channel=qc)

    async def press_key(key: str, reason: str = ""):
        return f"pressed {key}"  # does not mutate browser text

    reg.register(
        Tool(
            "press_key",
            "press",
            {
                "type": "object",
                "properties": {"key": {"type": "string"}, "reason": {"type": "string"}},
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

    async def list_interactive(offset: int = 0, limit: int = 50, reason: str = ""):
        idx = offset // limit
        if idx >= len(pages):
            return ""
        return pages[idx]

    text = "irrelevant"
    browser = _StubBrowser(text)
    transport = _mock_llm_calls(
        [
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "first"}),
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "second"}),
            ("done", {"status": "success", "answer": "ok", "reason": "x"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(
        Tool(
            "list_interactive",
            "li",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "reason": {"type": "string"},
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

    async def list_interactive(offset: int = 0, limit: int = 50, reason: str = ""):
        raise RuntimeError("simulated tool failure")

    transport = _mock_llm_calls(
        [
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "first"}),
            ("done", {"status": "failed", "answer": "tool errored", "reason": "x"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(
        Tool(
            "list_interactive",
            "li",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "reason": {"type": "string"},
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

    async def read_grep(
        pattern: str,
        context: int = 80,
        max_matches: int = 10,
        offset: int = 0,
        reason: str = "",
    ):
        from agent.tools.browser import build_browser_tools

        class _S:
            def __init__(self, b):
                self.page = b.page

        fns = build_browser_tools(_S(browser), restrict_goto=False)
        return await fns["read_grep"](
            pattern=pattern,
            context=context,
            max_matches=max_matches,
            offset=offset,
        )

    transport = _mock_llm_calls(
        [
            ("read_grep", {"pattern": "needle", "reason": "first grep"}),
            ("read_grep", {"pattern": "needle", "reason": "same pattern again"}),
            ("done", {"status": "success", "answer": "ok", "reason": "x"}),
        ]
    )
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(
        Tool(
            "read_grep",
            "rg",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "context": {"type": "integer"},
                    "max_matches": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "reason": {"type": "string"},
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    step1 = loop.tape[1]
    assert step1["action"] == "read_grep"
    assert step1["obs"].startswith("DUPLICATE:")


@pytest.mark.asyncio
async def test_list_interactive_hidden_when_hop_cap_exhausts(tmp_path):
    """When list_interactive auto-advance hits max_auto_advance_hops with the
    snapshot still cache-equal at every offset, the loop must:
      1. Emit a synthetic end-of-list obs (not an `[auto-advanced ...]` slice).
      2. Drop `list_interactive` from the next turn's tool list.
    """
    browser = _StubBrowser("irrelevant")

    async def list_interactive(offset: int = 0, limit: int = 50, reason: str = ""):
        # Constant snapshot regardless of offset → every advance is a cache hit.
        return "static-snap"

    # max_auto_advance_hops=2 → call 1 records {0}; call 2 records {1}; call 3
    # walks 0→1 (hops 1,2) and exhausts the cap with all visited offsets cached.
    transport = _mock_llm_calls(
        [
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "first"}),
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "second"}),
            ("list_interactive", {"offset": 0, "limit": 1, "reason": "third"}),
            ("done", {"status": "success", "answer": "ok", "reason": "x"}),
        ]
    )
    captured_tools: list[list[str]] = []

    async def capturing_handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        return await transport.handler(request)

    capturing_transport = httpx.MockTransport(capturing_handler)
    llm = LLMClient("http://t/v1", "m", transport=capturing_transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(
        Tool(
            "list_interactive",
            "li",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "reason": {"type": "string"},
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=6,
        max_auto_advance_hops=2,
    )
    await loop.run("anything")

    # Step 2 (third list_interactive call) should hit hop cap and emit
    # the synthetic end-of-list obs, not an auto-advanced slice.
    step2 = loop.tape[2]
    assert step2["action"] == "list_interactive"
    assert "end of interactive list" in step2["obs"].lower()
    assert not step2["obs"].startswith("[auto-advanced")
    # Fourth LLM turn (index 3) must not see `list_interactive` in its tool list.
    assert "list_interactive" not in captured_tools[3]


@pytest.mark.asyncio
async def test_wall_banner_injected_after_two_consecutive_walls(tmp_path):
    """Two consecutive turns landing on a Cloudflare-shaped page should
    cause the *third* turn's user prompt to carry a wall-banner block.
    The first turn must not have it (single occurrence is not enough)."""
    cf_text = (
        "dictionary.cambridge.org | Performing security verification |  | "
        "This website uses a security service to protect against malicious bots."
    )
    browser = _StubBrowser(cf_text)

    transport = _mock_llm_calls(
        [
            ("read", {"offset": 0, "reason": "first read"}),
            ("read", {"offset": 0, "reason": "second read, still on wall"}),
            ("done", {"status": "failed", "answer": "blocked", "reason": "x"}),
        ]
    )
    captured_user: list[str] = []

    async def capturing_handler(request):
        body = json.loads(request.content.decode())
        for m in body["messages"]:
            if m["role"] == "user":
                captured_user.append(m["content"])
        return await transport.handler(request)

    capturing_transport = httpx.MockTransport(capturing_handler)
    llm = LLMClient("http://t/v1", "m", transport=capturing_transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # First turn user prompt: no wall banner (only one wall observation so far,
    # actually zero — the current_text grab is the very first thing).
    assert "BLOCKED" not in captured_user[0]
    # Third turn (index 2): after two consecutive wall pages, banner present.
    assert "BLOCKED" in captured_user[2]
    assert "Cloudflare" in captured_user[2] or "cloudflare" in captured_user[2].lower()


@pytest.mark.asyncio
async def test_only_done_exposed_on_final_step(tmp_path):
    """At step_idx == max_steps-1, the tool list passed to the LLM must
    contain only `done` — forcing the agent to commit on its last turn."""
    browser = _StubBrowser("plain text page")

    transport = _mock_llm_calls(
        [
            ("read", {"offset": 0, "reason": "first"}),
            ("done", {"status": "success", "answer": "best guess on final step", "reason": "x"}),
        ]
    )
    captured_tools: list[list[str]] = []

    async def capturing_handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        return await transport.handler(request)

    capturing_transport = httpx.MockTransport(capturing_handler)
    llm = LLMClient("http://t/v1", "m", transport=capturing_transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=2,  # iter 0 normal; iter 1 = final → only done exposed
    )
    await loop.run("anything")

    # Turn 0: full tool list (read available).
    assert "read" in captured_tools[0]
    # Turn 1 (final step): only `done`.
    assert captured_tools[1] == ["done"]


@pytest.mark.asyncio
async def test_list_interactive_empty_treated_as_exhausted_on_arrival(tmp_path):
    """When list_interactive returns "[]", treat as exhausted-on-arrival:
    inject the synthetic 'end of interactive list' obs and hide the tool
    from the next turn — same as the hop-cap-hide arm."""
    browser = _StubBrowser("body text")

    captured_tools: list[list[str]] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tools.append([t["function"]["name"] for t in body.get("tools", [])])
        # Step 0: list_interactive(offset=0) (will return "[]"); step 1: done.
        idx = len(captured_tools) - 1
        if idx == 0:
            tc_name, tc_args = (
                "list_interactive",
                {"offset": 0, "limit": 50, "reason": "list"},
            )
        else:
            tc_name, tc_args = (
                "done",
                {
                    "status": "success",
                    "answer": "ok",
                    "reason": "done",
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
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
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
    meta = build_meta_tools(question_channel=qc)

    async def list_interactive(offset: int = 0, limit: int = 50, reason: str = ""):
        return "[]"

    reg.register(
        Tool(
            "list_interactive",
            "list_interactive",
            {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "reason": {"type": "string"},
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    await loop.run("anything")

    # Step 0 obs is the exhausted-on-arrival synthetic message.
    assert loop.tape[0]["obs"].startswith("(end of interactive list")
    # Turn 1 (the next LLM call) MUST NOT have list_interactive in tools.
    assert "list_interactive" not in captured_tools[1]


@pytest.mark.asyncio
async def test_force_done_at_max_steps_replaces_max_steps_fallback(tmp_path):
    """At step_idx == max_steps - 1, the loop must call coerce_done_via_llm
    and use its return value, not fall through to the "max steps" placeholder."""
    text = "A" * 1600
    browser = _StubBrowser(text)

    captured_tool_choice: list[Any] = []
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tool_choice.append(body.get("tool_choice"))
        call_count["n"] += 1
        # max_steps=3 → step_idx 0, 1; final step is the forced-done call (3rd).
        if call_count["n"] in (1, 2):
            tc_name, tc_args = "read", {"offset": 0}
        else:
            tc_name, tc_args = "done", {"status": "success", "answer": "extracted"}
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
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
    meta = build_meta_tools(question_channel=qc)
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
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=3,
    )
    result = await loop.run("anything")

    # The final result must come from the forced-done LLM call, not the placeholder.
    assert result == {"status": "success", "answer": "extracted"}
    # The forced call (3rd) must use named tool_choice.
    assert captured_tool_choice[2] == {"type": "function", "function": {"name": "done"}}


@pytest.mark.asyncio
async def test_regular_turns_use_tool_choice_required(tmp_path):
    """Every regular (non-coercion) LLM call from the loop must use
    tool_choice="required" so the model cannot respond with prose or
    fabricate a tool name outside the filtered tools list."""
    text = "A" * 1600
    browser = _StubBrowser(text)

    captured_tool_choice: list[Any] = []
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        captured_tool_choice.append(body.get("tool_choice"))
        call_count["n"] += 1
        # First (and only) regular turn: terminate via done.
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": "done",
                                        "arguments": json.dumps(
                                            {"status": "success", "answer": "k"}
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
    build_meta_tools(question_channel=qc)
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
            (lambda **kw: __import__("asyncio").sleep(0)),
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=10,
    )
    await loop.run("anything")

    assert captured_tool_choice, "expected at least one regular LLM call"
    assert captured_tool_choice[0] == "required", (
        f"regular turn must use tool_choice='required', got {captured_tool_choice[0]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hallucinated_name",
    ["ghost", "note", "definitely_not_a_tool"],
)
async def test_loop_recovers_from_hallucinated_tool_name(tmp_path, hallucinated_name):
    """When the model emits a tool_call name not in the filtered tools
    list (e.g., picks a masked tool, or the deleted ``note`` tool), the
    LLM client raises ToolNameNotAllowed; the loop must catch it,
    synthesize a feedback step into the tape, and continue — not crash
    the run."""
    text = "A" * 1600
    browser = _StubBrowser(text)
    call_count = {"n": 0}
    captured_tape_actions: list[str] = []

    async def handler(request):
        call_count["n"] += 1
        # First call: model fabricates a tool name not in the list.
        # Second call: model returns a valid done() to terminate.
        if call_count["n"] == 1:
            tc_name, tc_args = hallucinated_name, {}
        else:
            tc_name, tc_args = "done", {"status": "success", "answer": "ok"}
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
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
    build_meta_tools(question_channel=qc)
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
            (lambda **kw: __import__("asyncio").sleep(0)),
        )
    )
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
    )
    result = await loop.run("anything")

    captured_tape_actions = [t["action"] for t in loop.tape]
    assert hallucinated_name in captured_tape_actions, (
        f"expected hallucinated {hallucinated_name!r} to be recorded as a "
        f"feedback step, got tape actions {captured_tape_actions!r}"
    )
    feedback_step = next(t for t in loop.tape if t["action"] == hallucinated_name)
    assert "not available" in feedback_step["obs"]
    assert result == {"status": "success", "answer": "ok"}


@pytest.mark.asyncio
async def test_loop_reason_call_lands_on_reason_log(tmp_path):
    """Agent calls reason("hello"); next loop iteration sees it on
    self.reason_log AND in the rendered user message."""
    browser = _StubBrowser("page text")
    call_count = {"n": 0}
    captured_user_messages: list[str] = []

    async def handler(request):
        body = json.loads(request.content.decode())
        for m in body["messages"]:
            if m["role"] == "user":
                captured_user_messages.append(m["content"])
        call_count["n"] += 1
        if call_count["n"] == 1:
            tc_name, tc_args = "reason", {"text": "hello", "reason": "remember this"}
        else:
            tc_name, tc_args = (
                "done",
                {
                    "status": "success",
                    "answer": "ok",
                    "reason": "exit",
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
                                    "id": f"c{call_count['n']}",
                                    "type": "function",
                                    "function": {
                                        "name": tc_name,
                                        "arguments": json.dumps(tc_args),
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
    # Shared reason_log between meta-tool closure and ReactLoop.
    reason_log: list[str] = []
    from agent.tools.meta import build_meta_tool_list

    for t in build_meta_tool_list(
        question_channel=qc,
        reason_log=reason_log,
    ):
        reg.register(t)
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=5,
        reason_log=reason_log,
    )
    result = await loop.run("anything")

    assert loop.reason_log == ["hello"]
    assert loop.reason_log is reason_log
    assert result == {"status": "success", "answer": "ok"}
    # The second user message (turn after reason() call) must show the entry.
    assert "Reasoning so far:" in captured_user_messages[1]
    assert "- hello" in captured_user_messages[1]


# ---------------------------------------------------------------------------
# Per-line hash dedup for read_grep (Task 3)
# ---------------------------------------------------------------------------

def _build_real_read_grep_tool(browser):
    """Wrap the real read_grep impl from agent.tools.browser onto the stub
    browser, exposing the new schema (pattern, context, max_matches, offset)."""
    from agent.tools.browser import build_browser_tools

    class _S:
        def __init__(self, b):
            self.page = b.page

    fns = build_browser_tools(_S(browser), restrict_goto=False)

    async def read_grep(
        pattern: str,
        context: int = 80,
        max_matches: int = 10,
        offset: int = 0,
        reason: str = "",
    ):
        return await fns["read_grep"](
            pattern=pattern,
            context=context,
            max_matches=max_matches,
            offset=offset,
        )

    return Tool(
        "read_grep",
        "rg",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "context": {"type": "integer"},
                "max_matches": {"type": "integer"},
                "offset": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["pattern"],
        },
        read_grep,
    )


def _done_tool(meta):
    return Tool(
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


def _build_loop_with_grep(browser, calls, *, tmp_path, max_steps=8, extra_tools=None):
    transport = _mock_llm_calls(calls)
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
    reg.register(_build_real_read_grep_tool(browser))
    reg.register(_build_read_tool(browser))
    reg.register(_done_tool(meta))
    for t in extra_tools or []:
        reg.register(t)
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=browser,
        question_channel=qc,
        max_steps=max_steps,
    )
    return loop


@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_fires_synthetic_when_all_lines_seen(tmp_path):
    text = "alpha needle beta needle gamma"
    browser = _StubBrowser(text)
    loop = _build_loop_with_grep(
        browser,
        [
            ("read_grep", {"pattern": "needle", "reason": "first"}),
            ("read_grep", {"pattern": "needle", "reason": "again"}),
            ("done", {"status": "failed", "answer": "stop", "reason": "x"}),
        ],
        tmp_path=tmp_path,
    )
    await loop.run("find needle in page")
    assert loop.tape[1]["obs"].startswith("DUPLICATE:")


@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_passes_partial_overlap_with_note(tmp_path):
    """Two paginated calls overlap on snippet lines but the second adds a
    new header — output keeps new lines and notes the hidden count."""
    text = "XYZ XYZ XYZ XYZ"  # 4 occurrences
    browser = _StubBrowser(text)
    loop = _build_loop_with_grep(
        browser,
        [
            ("read_grep", {"pattern": "XYZ", "max_matches": 4, "offset": 0, "reason": "1"}),
            ("read_grep", {"pattern": "XYZ", "max_matches": 4, "offset": 2, "reason": "2"}),
            ("done", {"status": "failed", "answer": "stop", "reason": "x"}),
        ],
        tmp_path=tmp_path,
    )
    await loop.run("find XYZ markers in page")
    obs2 = loop.tape[1]["obs"]
    assert not obs2.startswith("DUPLICATE:")
    assert "lines hidden" in obs2


@pytest.mark.asyncio
async def test_read_grep_per_line_dedup_does_not_fire_when_offset_yields_new_lines(tmp_path):
    text = ("XYZ " * 20).strip()
    browser = _StubBrowser(text)
    loop = _build_loop_with_grep(
        browser,
        [
            ("read_grep", {"pattern": "XYZ", "max_matches": 5, "offset": 0, "reason": "p1"}),
            ("read_grep", {"pattern": "XYZ", "max_matches": 5, "offset": 5, "reason": "p2"}),
            ("done", {"status": "failed", "answer": "stop", "reason": "x"}),
        ],
        tmp_path=tmp_path,
    )
    await loop.run("scan XYZ markers across page")
    obs2 = loop.tape[1]["obs"]
    assert not obs2.startswith("DUPLICATE:")
    assert "Showing matches 5-9" in obs2


@pytest.mark.asyncio
async def test_read_grep_dedup_resets_on_page_mutation(tmp_path):
    """After a page mutation, the line cache is cleared — same pattern
    re-issued returns a fresh, non-DUPLICATE obs."""
    browser = _StubBrowser("alpha needle beta")

    async def goto(url: str = "", reason: str = ""):
        # Mutate the page text — the loop's mutation hook clears the cache.
        browser.set_text("alpha needle beta v2")
        return f"navigated to {url}"

    goto_tool = Tool(
        "goto",
        "go",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["url"],
        },
        goto,
    )
    loop = _build_loop_with_grep(
        browser,
        [
            ("read_grep", {"pattern": "needle", "reason": "first"}),
            ("goto", {"url": "https://x.test/2", "reason": "navigate"}),
            ("read_grep", {"pattern": "needle", "reason": "post-nav"}),
            ("done", {"status": "failed", "answer": "stop", "reason": "x"}),
        ],
        tmp_path=tmp_path,
        extra_tools=[goto_tool],
    )
    await loop.run("find needle in page")
    assert not loop.tape[2]["obs"].startswith("DUPLICATE:")
