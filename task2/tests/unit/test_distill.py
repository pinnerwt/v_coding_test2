import json

import httpx
import pytest

from agent.distill import distill_page_knowledge
from agent.llm import LLMClient
from agent.notes_store import NotesStore


def _llm_with_response(content: str) -> LLMClient:
    async def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    return LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )


def _llm_that_fails() -> LLMClient:
    async def handler(request):
        return httpx.Response(500, text="boom")

    return LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )


class _RecordingTrace:
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_distill_replaces_url_row_after_success(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "stale page-fact from prior run")
    llm = _llm_with_response(
        "- GPU dropdown is at id=94\n- Selecting a GPU updates the score column"
    )
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="best LLM for an RTX 3090",
        status="success",
        answer="Llama 3.1 8B",
        tape=[
            {
                "action": "click",
                "args": {"id": 94, "value": "RTX 3090"},
                "obs": "selected 'RTX 3090' on id=94",
            }
        ],
        reason_log=["GPU dropdown id=94"],
        trace=trace,
    )
    out = notes.get("https://a.test/")
    assert "GPU dropdown is at id=94" in out
    assert "stale page-fact from prior run" not in out


@pytest.mark.asyncio
async def test_distill_runs_on_failed_status(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    llm = _llm_with_response("- Cloudflare challenge wall on every navigation")
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="anything",
        status="failed",
        answer="blocked by Cloudflare",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    assert "Cloudflare" in notes.get("https://a.test/")


@pytest.mark.asyncio
async def test_distill_failure_emits_trace_event_and_leaves_row(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "prior-row-content")
    llm = _llm_that_fails()
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=trace,
    )
    assert notes.get("https://a.test/") == "prior-row-content"
    assert any(e["type"] == "distill_failed" for e in trace.events)
    ev = next(e for e in trace.events if e["type"] == "distill_failed")
    assert isinstance(ev["payload"]["error"], str)
    assert ev["payload"]["error"]  # non-empty


@pytest.mark.asyncio
async def test_distill_short_circuits_when_notes_is_none():
    llm = _llm_that_fails()  # would raise if invoked
    trace = _RecordingTrace()
    await distill_page_knowledge(
        llm=llm,
        notes=None,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=trace,
    )
    assert trace.events == []


@pytest.mark.asyncio
async def test_distill_caps_oversize_output(tmp_path):
    notes = NotesStore(tmp_path / "n.db")
    huge = "\n".join(["- " + "x" * 200] * 100)
    llm = _llm_with_response(huge)
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    assert len(notes.get("https://a.test/")) <= 2048


@pytest.mark.asyncio
async def test_distill_passes_prior_note_into_prompt(tmp_path):
    """The merge happens LLM-side, so the prior row must reach the
    prompt verbatim."""
    notes = NotesStore(tmp_path / "n.db")
    notes.set("https://a.test/", "prior-fact-XYZ")
    captured = {}

    async def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "- new fact"}}]})

    llm = LLMClient(
        base_url="http://test/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    await distill_page_knowledge(
        llm=llm,
        notes=notes,
        url="https://a.test/",
        goal="g",
        status="success",
        answer="a",
        tape=[],
        reason_log=[],
        trace=_RecordingTrace(),
    )
    body_text = json.dumps(captured["body"])
    assert "prior-fact-XYZ" in body_text
