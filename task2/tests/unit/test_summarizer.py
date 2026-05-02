import httpx
import pytest

from agent.llm import LLMClient
from agent.notes_store import NotesStore
from agent.summarizer import Summarizer


def _mock(content: str):
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_appends_bullets(tmp_path):
    llm = LLMClient(
        base_url="http://t/v1",
        model="m",
        transport=_mock("- found search at id 12\n- login button hidden behind cookie banner"),
    )
    notes = NotesStore(tmp_path / "n.db")
    s = Summarizer(llm, notes)
    await s.maybe_summarize(
        "goto",
        prior_url="https://a.test/",
        tape_slice=[{"action": "click", "obs": "ERROR"}],
        existing_notes="",
    )
    body = notes.get("https://a.test/")
    assert "found search at id 12" in body
    assert "login button" in body
