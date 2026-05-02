import asyncio

import pytest

from agent.notes_store import NotesStore
from agent.tools.meta import (
    LoopDone,
    QuestionChannel,
    build_meta_tool_list,
    build_meta_tools,
)


@pytest.mark.asyncio
async def test_done_raises_with_payload():
    tools = build_meta_tools(
        notes=None, current_url=lambda: "x", question_channel=QuestionChannel()
    )
    with pytest.raises(LoopDone) as exc:
        await tools["done"](status="success", answer="yay")
    assert exc.value.status == "success" and exc.value.answer == "yay"


@pytest.mark.asyncio
async def test_note_tool_is_not_in_registry():
    tools = build_meta_tools(
        notes=None,
        current_url=lambda: "x",
        question_channel=QuestionChannel(),
        reason_log=[],
    )
    assert "note" not in tools
    assert "reason" in tools
    tool_list = build_meta_tool_list(
        notes=None,
        current_url=lambda: "x",
        question_channel=QuestionChannel(),
        reason_log=[],
    )
    names = {t.name for t in tool_list}
    assert "note" not in names
    assert "reason" in names


@pytest.mark.asyncio
async def test_ask_user_question_blocks_until_answered():
    ch = QuestionChannel()
    tools = build_meta_tools(notes=None, current_url=lambda: "x", question_channel=ch)
    task = asyncio.create_task(tools["ask_user_question"](question="size?"))
    await asyncio.sleep(0)
    assert ch.pending() == "size?"
    ch.answer("M")
    result = await task
    assert result == "user said: M"


@pytest.mark.asyncio
async def test_reason_appends_to_session_log():
    log: list[str] = []
    tools = build_meta_tools(
        notes=None,
        current_url=lambda: "x",
        question_channel=QuestionChannel(),
        reason_log=log,
    )
    out = await tools["reason"](text="GPU dropdown id=94")
    assert out == "noted"
    assert log == ["GPU dropdown id=94"]


@pytest.mark.asyncio
async def test_reason_does_not_touch_notes_store(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    log: list[str] = []
    tools = build_meta_tools(
        notes=notes,
        current_url=lambda: "https://a.test/",
        question_channel=QuestionChannel(),
        reason_log=log,
    )
    await tools["reason"](text="ephemeral")
    assert notes.get("https://a.test/") == ""
