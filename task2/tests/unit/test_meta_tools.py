import asyncio

import pytest

from agent.notes_store import NotesStore
from agent.tools.meta import LoopDone, QuestionChannel, build_meta_tools


@pytest.mark.asyncio
async def test_done_raises_with_payload():
    tools = build_meta_tools(
        notes=None, current_url=lambda: "x", question_channel=QuestionChannel()
    )
    with pytest.raises(LoopDone) as exc:
        await tools["done"](status="success", answer="yay")
    assert exc.value.status == "success" and exc.value.answer == "yay"


@pytest.mark.asyncio
async def test_note_appends(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    tools = build_meta_tools(
        notes=notes,
        current_url=lambda: "https://a.test/",
        question_channel=QuestionChannel(),
    )
    out = await tools["note"](text="learned X")
    assert out == "noted"
    assert "learned X" in notes.get("https://a.test/")


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
