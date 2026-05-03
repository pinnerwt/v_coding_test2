"""Integration: a session run produces an `<sid>.llm.jsonl` sidecar
containing one record per LLM call, with usage passed through verbatim."""

import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


def _canned_responses():
    """Single-call session: model returns done() immediately."""
    yield {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "done",
                                "arguments": json.dumps(
                                    {"thought": "trivial", "status": "success", "answer": "ok"}
                                ),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {
            "prompt_tokens": 42,
            "completion_tokens": 7,
            "total_tokens": 49,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 12,
        },
    }
    # Distillation call (post-done): plain text response.
    while True:
        yield {
            "choices": [{"message": {"role": "assistant", "content": "- page-fact one"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


@pytest.fixture
def llm_transport():
    gen = _canned_responses()

    async def handler(request):
        return httpx.Response(200, json=next(gen))

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_run_sync_writes_sidecar_with_usage_passthrough(
    tmp_path: Path, monkeypatch, llm_transport
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=llm_transport)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "say hi"})
        assert r.status_code == 200

    # /api/run_sync does not return sid; look up the sole session trace on disk.
    traces = sorted((tmp_path / "traces").glob("*.jsonl"))
    main_traces = [p for p in traces if not p.name.endswith(".llm.jsonl")]
    assert len(main_traces) == 1, f"expected exactly one session trace, got {main_traces}"
    sid = main_traces[0].stem

    sidecar = tmp_path / "traces" / f"{sid}.llm.jsonl"
    assert sidecar.exists(), f"expected sidecar at {sidecar}"

    lines = sidecar.read_text().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert first["call_idx"] == 0
    assert first["request"]["model"] == "deepseek-chat"
    assert first["response"]["usage"]["prompt_cache_hit_tokens"] == 30
    assert first["response"]["usage"]["prompt_cache_miss_tokens"] == 12


@pytest.mark.asyncio
async def test_run_sync_does_not_create_phantom_sidecar_session(
    tmp_path: Path, monkeypatch, llm_transport
):
    """Regression: /api/sessions must not list <sid>.llm.jsonl sidecars
    as separate sessions."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=llm_transport)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "say hi"})
        assert r.status_code == 200
        r2 = await ac.get("/api/sessions")
        assert r2.status_code == 200
        sessions = r2.json()

    sids = [s["sid"] for s in sessions]
    # Exactly one session row for the run we just did. No phantom `*.llm` row.
    assert len(sessions) == 1, f"unexpected extra sessions: {sids}"
    assert not any(sid.endswith(".llm") for sid in sids), sids
