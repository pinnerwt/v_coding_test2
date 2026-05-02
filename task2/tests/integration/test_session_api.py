import json

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


@pytest.mark.asyncio
async def test_sessions_lists_disk_traces(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    sid = "abc123"
    (traces / f"{sid}.jsonl").write_text(
        json.dumps(
            {
                "type": "session_started",
                "payload": {
                    "sid": sid,
                    "goal": "find a flight",
                    "started_at": "2026-05-02T00:00:00+00:00",
                },
            }
        )
        + "\n"
        + json.dumps({"type": "done", "payload": {"status": "success", "answer": "x"}})
        + "\n"
    )

    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert any(
            s["sid"] == sid and s["goal"] == "find a flight" and s["status"] == "success"
            for s in data
        )


@pytest.mark.asyncio
async def test_sessions_handles_missing_traces_dir(tmp_path):
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []


@pytest.mark.asyncio
async def test_trace_returns_events(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    sid = "x1"
    (traces / f"{sid}.jsonl").write_text(
        json.dumps(
            {"type": "session_started", "payload": {"sid": sid, "goal": "g", "started_at": "t"}}
        )
        + "\n"
        + json.dumps(
            {
                "type": "step",
                "payload": {"n": 0, "action": "goto", "args": {"url": "u"}, "obs": "ok"},
            }
        )
        + "\n"
        + json.dumps({"type": "done", "payload": {"status": "success", "answer": "a"}})
        + "\n"
    )
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get(f"/api/trace/{sid}")
        assert r.status_code == 200
        evs = r.json()
        assert len(evs) == 3
        assert evs[0]["type"] == "session_started"
        assert evs[2]["type"] == "done"


@pytest.mark.asyncio
async def test_trace_404_for_missing(tmp_path):
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/trace/nope")
        assert r.status_code == 404
