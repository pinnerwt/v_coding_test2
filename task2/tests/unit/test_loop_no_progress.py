"""Stuck-detection on observation novelty, not just three-in-a-row equality.

Regression for the canirun.ai trace (session b2969e64…): the model alternated
click(id=94) with list_interactive/read/press_key for 40 steps. Each pair of
adjacent steps differed, so `_last_three_match` never fired and the loop
burned the full step budget. The novelty detector fires on any cycle where
the recent window produced no new observations."""

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


class _Browser:
    class _Page:
        url = "https://a.test/"

    page = _Page()


@pytest.mark.asyncio
async def test_replan_hint_fires_on_alternating_actions_with_stale_obs(tmp_path):
    """Model alternates noopA / noopB; both return the same obs. The
    three-in-a-row detector cannot see this loop, but the novelty detector
    must — REPLAN should appear in the system prompt before max_steps."""
    seen_hints: list[bool] = []
    call_n = {"i": 0}

    async def handler(request):
        body = json.loads(request.content)
        sys_msg = body["messages"][0]["content"]
        seen_hints.append("REPLAN" in sys_msg)
        # Alternate noopA / noopB so _last_three_match never fires.
        name = "noopA" if call_n["i"] % 2 == 0 else "noopB"
        call_n["i"] += 1
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
                                    "id": "c",
                                    "type": "function",
                                    "function": {"name": name, "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def noopB():
        return "same"

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(Tool("noopB", "x", {"type": "object", "properties": {}}, noopB))

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=15,
    )
    await loop.run("g")
    # Hint must have fired at least once well before max_steps.
    assert any(seen_hints), "REPLAN never appeared — novelty detector did not fire"
    # And it must fire within the first 12 turns (loop should not need 15 to notice).
    assert any(seen_hints[:12]), f"REPLAN fired too late: {seen_hints}"


@pytest.mark.asyncio
async def test_no_progress_streak_forces_done_failed(tmp_path):
    """Even when the model keeps inventing new (action, args) variants,
    if the *observation* hasn't been novel for N consecutive steps the
    loop must force a done(failed). This is the safety net the canirun
    case 113 trace was missing — the agent burned 50 steps because no
    such ceiling existed."""
    call_n = {"i": 0}

    async def handler(request):
        # Alternate noopA / noopB to defeat _last_three_match.
        name = "noopA" if call_n["i"] % 2 == 0 else "noopB"
        call_n["i"] += 1
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
                                    "id": "c",
                                    "type": "function",
                                    "function": {"name": name, "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def noopB():
        return "same"

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(Tool("noopB", "x", {"type": "object", "properties": {}}, noopB))

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        summarizer=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=30,
    )
    result = await loop.run("g")
    assert result["status"] == "failed"
    # Must have given up well before max_steps. With streak threshold 12
    # and one warm-up step, expect ≤16 LLM calls (some slack for the
    # interplay with the existing hint state machine).
    assert call_n["i"] <= 16, (
        f"loop called LLM {call_n['i']} times before giving up — streak "
        "force-done did not fire"
    )
    assert "stuck" in result["answer"].lower(), result["answer"]
