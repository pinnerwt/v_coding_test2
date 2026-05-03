import json
from typing import Any

import httpx
import pytest

from agent.force_done import coerce_done_via_llm
from agent.llm import LLMClient

_DONE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "done",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["status", "answer"],
        },
    },
}


def _llm_returning(name: str, args: dict) -> LLMClient:
    async def handler(request):
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
                                        "name": name,
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_coerce_done_returns_llm_done_tool_call():
    llm = _llm_returning("done", {"status": "success", "answer": "the answer"})
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="any goal",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "success", "answer": "the answer"}


def _capturing_llm():
    captured: dict[str, Any] = {}

    async def handler(request):
        body = json.loads(request.content.decode())
        captured["payload"] = body
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
                                            {"status": "failed", "answer": "x"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler)), captured


@pytest.mark.asyncio
async def test_coerce_done_passes_named_tool_choice():
    llm, captured = _capturing_llm()
    await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert captured["payload"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "done"},
    }
    assert [t["function"]["name"] for t in captured["payload"]["tools"]] == ["done"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger,needle",
    [
        ("max_steps", "final allowed step"),
        ("no_progress", "no novel observation"),
        ("asked_after_clarification", "User clarification"),
    ],
)
async def test_coerce_done_includes_trigger_specific_instruction(trigger, needle):
    llm, captured = _capturing_llm()
    await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger=trigger,
        n_no_progress=12 if trigger == "no_progress" else None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert needle in combined


@pytest.mark.asyncio
async def test_coerce_done_appends_read_content_dump():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "goto", "args": {"url": "https://x.test/"}, "obs": "navigated"},
        {"action": "read", "args": {"offset": 0}, "obs": "first read content"},
        {"action": "read", "args": {"offset": 1600}, "obs": "second read content"},
        {"action": "read", "args": {"offset": 3200}, "obs": "third read content"},
    ]
    await coerce_done_via_llm(
        llm=llm,
        tape=tape,
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert "Read content captured so far (chronological):" in combined
    assert "read(offset=0)" in combined
    assert "first read content" in combined
    assert "read(offset=1600)" in combined
    assert "second read content" in combined
    assert "read(offset=3200)" in combined
    assert "third read content" in combined
    assert combined.index("first read content") < combined.index("second read content")
    assert combined.index("second read content") < combined.index("third read content")


@pytest.mark.asyncio
async def test_coerce_done_handles_empty_reads():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "goto", "args": {"url": "https://x.test/"}, "obs": "navigated"},
        {"action": "list_interactive", "args": {}, "obs": "[]"},
    ]
    await coerce_done_via_llm(
        llm=llm,
        tape=tape,
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert "(none — no read() calls in tape)" in combined


@pytest.mark.asyncio
async def test_coerce_done_truncates_long_read_obs():
    llm, captured = _capturing_llm()
    tape = [
        {"action": "read", "args": {"offset": 0}, "obs": "X" * 5000},
    ]
    await coerce_done_via_llm(
        llm=llm,
        tape=tape,
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    # Verify _read_content_dump's truncation specifically (its block starts
    # with "Read content captured so far"). The raw obs may also surface
    # untruncated via the last-3-obs section in build_messages, so don't
    # combine all user messages here.
    dump = next(m for m in user_msgs if m.startswith("Read content captured so far"))
    assert "X" * 800 in dump
    assert "X" * 801 not in dump


@pytest.mark.asyncio
async def test_coerce_done_falls_back_on_empty_tool_calls():
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "no tool"}}]},
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "failed", "answer": "max steps"}


@pytest.mark.asyncio
async def test_coerce_done_falls_back_on_wrong_tool_name():
    llm = _llm_returning("read", {"offset": 0})
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="asked_after_clarification",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {"status": "failed", "answer": "stuck after user clarification"}


@pytest.mark.asyncio
async def test_coerce_done_forwards_reason_log_into_messages():
    """The forced-done call must surface the agent's reason() entries to the
    LLM so the commit answer is informed by the same scratchpad as a normal
    turn."""
    llm, captured = _capturing_llm()
    await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="max_steps",
        n_no_progress=None,
        done_tool_schema=_DONE_TOOL_SCHEMA,
        reason_log=["picked id=42 because it's the only Submit button"],
    )
    user_msgs = [m["content"] for m in captured["payload"]["messages"] if m["role"] == "user"]
    combined = "\n".join(user_msgs)
    assert "picked id=42" in combined
    assert "Reasoning so far:" in combined


@pytest.mark.asyncio
async def test_coerce_done_no_progress_placeholder_interpolates_n():
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "x"}}]},
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    result = await coerce_done_via_llm(
        llm=llm,
        tape=[],
        goal="g",
        qa=[],
        url="https://x.test/",
        url_notes="",
        page_header="URL=https://x.test/",
        trigger="no_progress",
        n_no_progress=14,
        done_tool_schema=_DONE_TOOL_SCHEMA,
    )
    assert result == {
        "status": "failed",
        "answer": "stuck: no novel observation for 14 consecutive steps",
    }
