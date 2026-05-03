import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel, build_meta_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter, read_trace


class _StubBrowser:
    class _Page:
        url = "https://a.test/"

    page = _Page()


def _mock_calls(calls):
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


@pytest.mark.asyncio
async def test_loop_runs_done(tmp_path):
    transport = _mock_calls([("done", {"status": "success", "answer": "42", "reason": "ok"})])
    llm = LLMClient("http://t/v1", "m", transport=transport)
    reg = ToolRegistry()
    qc = QuestionChannel()
    meta = build_meta_tools(question_channel=qc)
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
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    out = await loop.run("get the answer")
    assert out == {"status": "success", "answer": "42"}
    events = read_trace(tmp_path / "t.jsonl")
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_max_steps_is_only_backstop(tmp_path):
    """Post-T7: no done(), no stuck-detection. The agent keeps issuing
    `noop` tool calls; the loop must run exactly `max_steps` LLM turns and
    return whatever the final-step coerce path emits (a forced done())."""
    # Step 0..3 = regular noop turns. Step 4 (max_steps - 1) = forced-done
    # call constrained to {tool_choice: name=done}; the script returns done().
    call_count = {"n": 0}

    async def handler(request):
        body = json.loads(request.content.decode())
        call_count["n"] += 1
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("function", {}).get("name") == "done":
            tc_name, tc_args = (
                "done",
                {"status": "failed", "answer": "ran out", "reason": "x"},
            )
        else:
            tc_name, tc_args = "noop", {"reason": "still going"}
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

    async def noop():
        return "same"

    reg.register(Tool("noop", "noop", {"type": "object", "properties": {}}, noop))
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
        browser=_StubBrowser(),
        question_channel=qc,
        max_steps=5,
    )
    result = await loop.run("anything")
    # Steps 0..3 ran the regular noop path → 4 tape entries. Step 4 is the
    # max-steps short-circuit, which calls coerce_done_via_llm and returns
    # without appending to tape.
    assert len(loop.tape) == 4
    # The final result is whatever the forced-done LLM call produced.
    assert result == {"status": "failed", "answer": "ran out"}


def test_react_loop_accepts_anti_loop_config_kwargs(tmp_path):
    from agent.loop import ReactLoop

    # No-op stub for everything; we're only checking the constructor signature.
    class _Stub:
        page = type("P", (), {"url": "https://a.test/"})()

    loop = ReactLoop(
        llm=None,
        registry=None,
        notes=None,
        trace=None,
        browser=_Stub(),
        question_channel=None,
        max_steps=1,
        small_diff_threshold=200,
        max_auto_advance_hops=8,
        diff_inject_max_lines=4,
    )
    assert loop._small_diff_threshold == 200
    assert loop._max_auto_advance_hops == 8
    assert loop._diff_inject_max_lines == 4
