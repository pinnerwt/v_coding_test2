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
