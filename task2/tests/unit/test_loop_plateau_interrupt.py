"""Plateau interrupt: at PLATEAU_INTERRUPT consecutive non-novel obs,
force the agent to call `reason` (or done/ask) before the
NO_PROGRESS_GIVEUP backstop fires at 9. Splits one stuck-spiral into
1 reflection step + at-most-5 recovery attempts."""

import json

import httpx
import pytest

from agent.llm import LLMClient
from agent.loop import NO_PROGRESS_GIVEUP, PLATEAU_INTERRUPT, ReactLoop
from agent.tools.meta import QuestionChannel
from agent.tools.registry import Tool, ToolRegistry
from agent.trace import TraceWriter


def test_plateau_interrupt_threshold_below_giveup():
    """PLATEAU_INTERRUPT must fire strictly before NO_PROGRESS_GIVEUP,
    leaving room for the agent to recover after the forced reason."""
    assert PLATEAU_INTERRUPT < NO_PROGRESS_GIVEUP
    assert PLATEAU_INTERRUPT == 4


class _Browser:
    class _Page:
        url = "https://a.test/"

        async def evaluate(self, *_a, **_k):  # noqa: D401 - test stub
            return ""

    page = _Page()


def _ok_response(name: str) -> httpx.Response:
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


@pytest.mark.asyncio
async def test_plateau_interrupt_restricts_tools_at_threshold(tmp_path):
    """At streak == PLATEAU_INTERRUPT, the next LLM call's `tools`
    array must contain only {reason, done, ask_user_question}."""
    seen_tool_sets: list[set[str]] = []

    async def handler(request):
        body = json.loads(request.content)
        names = {t["function"]["name"] for t in body.get("tools", [])}
        seen_tool_sets.append(names)
        # Always call noopA — same obs each time, drives the streak up.
        return _ok_response("noopA")

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=20,
    )
    await loop.run("g")

    # The first 4 turns: full tool set offered. The 5th turn (after
    # streak reaches 4 from turns 1-4 of repeated "same" obs) must be
    # restricted to {reason, done, ask_user_question}.
    assert "noopA" in seen_tool_sets[0]
    restricted_turn = next(
        (i for i, s in enumerate(seen_tool_sets) if "noopA" not in s),
        None,
    )
    assert restricted_turn is not None, f"no restricted turn seen: {seen_tool_sets}"
    assert restricted_turn == 4, f"expected restriction at turn 4, got {restricted_turn}"
    assert seen_tool_sets[restricted_turn] == {"reason", "done", "ask_user_question"}


@pytest.mark.asyncio
async def test_reason_clears_pending_and_does_not_touch_streak(tmp_path):
    """After 4 stale obs the next turn is restricted; the LLM picks
    `reason`; the pending flag clears and the streak does not change.
    A subsequent stale obs should push streak to 5 (not 1, not reset)."""
    actions_to_play = ["noopA", "noopA", "noopA", "noopA", "reason", "noopA", "noopA"]
    idx = {"i": 0}

    async def handler(request):
        i = idx["i"]
        idx["i"] += 1
        # max_steps=8 triggers a final coerce_done LLM call (step_idx==7);
        # return a benign sentinel that LLMClient will reject as
        # ToolNameNotAllowed → coerce_done falls back to placeholder.
        if i >= len(actions_to_play):
            return _ok_response("noopA")
        return _ok_response(actions_to_play[i])

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=8,
    )
    await loop.run("g")

    # Tape after run: noopA, noopA, noopA, noopA, reason, noopA, noopA
    actions = [s["action"] for s in loop.tape]
    assert actions[:7] == ["noopA"] * 4 + ["reason", "noopA", "noopA"], actions
    # After reason at index 4, pending flag must be False again.
    # The streak after the final noopA tells us reason did NOT reset it
    # (would be 1 if it had).
    assert loop.no_progress_streak >= 5, loop.no_progress_streak


@pytest.mark.asyncio
async def test_plateau_interrupt_message_appears_in_system_prompt(tmp_path):
    """When the plateau triggers, the system prompt sent to the LLM on
    that turn must include the one-shot interrupt instruction."""
    seen_systems: list[str] = []

    async def handler(request):
        body = json.loads(request.content)
        seen_systems.append(body["messages"][0]["content"])
        return _ok_response("noopA")

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return ""

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=8,
    )
    await loop.run("g")

    # Turn at index 4 (after 4 stale obs) must carry the interrupt text.
    assert "no new information" in seen_systems[4].lower(), seen_systems[4]
    assert "reason" in seen_systems[4].lower()
    # Earlier turns must NOT carry it.
    for i in range(4):
        assert "no new information" not in seen_systems[i].lower(), (i, seen_systems[i])


@pytest.mark.asyncio
async def test_plateau_interrupt_clears_on_novel_obs_after_ask(tmp_path):
    """When the LLM picks `ask_user_question` at the restricted turn and
    the user's reply is novel, `_plateau_interrupt_pending` must clear
    so the next turn offers the full toolset again. Otherwise the agent
    is permanently locked into {reason, done, ask_user_question}."""
    # 4 stale noopA → arm plateau; restricted turn picks ask_user_question;
    # the mocked _ask returns "" which is novel vs prior "same" obs → streak
    # resets to 0. The very next turn must NOT be restricted.
    actions_to_play = [
        "noopA",
        "noopA",
        "noopA",
        "noopA",
        "ask_user_question",
        "noopA",
        "noopA",
    ]
    seen_tool_sets: list[set[str]] = []
    idx = {"i": 0}

    async def handler(request):
        body = json.loads(request.content)
        names = {t["function"]["name"] for t in body.get("tools", [])}
        seen_tool_sets.append(names)
        i = idx["i"]
        idx["i"] += 1
        if i >= len(actions_to_play):
            return _ok_response("noopA")
        return _ok_response(actions_to_play[i])

    llm = LLMClient("http://t/v1", "m", transport=httpx.MockTransport(handler))
    reg = ToolRegistry()

    async def noopA():
        return "same"

    async def reason(text: str = ""):
        return "noted"

    async def _done(status: str = "failed", answer: str = ""):
        return ""

    async def _ask(question: str = ""):
        return "user-novel-reply"

    reg.register(Tool("noopA", "x", {"type": "object", "properties": {}}, noopA))
    reg.register(
        Tool(
            "reason",
            "x",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            reason,
        )
    )
    reg.register(Tool("done", "done", {"type": "object", "properties": {}}, _done))
    reg.register(
        Tool(
            "ask_user_question",
            "x",
            {"type": "object", "properties": {"question": {"type": "string"}}},
            _ask,
        )
    )

    qc = QuestionChannel()
    trace = TraceWriter(tmp_path / "t.jsonl")
    loop = ReactLoop(
        llm=llm,
        registry=reg,
        notes=None,
        trace=trace,
        browser=_Browser(),
        question_channel=qc,
        max_steps=8,
    )
    await loop.run("g")

    # Turn 4 is the restricted turn (only reason/done/ask offered).
    assert seen_tool_sets[4] == {"reason", "done", "ask_user_question"}, seen_tool_sets[4]
    # Turn 5: after ask returned a novel obs, the full toolset must be back.
    assert "noopA" in seen_tool_sets[5], (
        f"expected unrestricted toolset on turn 5, got {seen_tool_sets[5]}"
    )
    # And the pending flag must be False at this point.
    assert loop._plateau_interrupt_pending is False
