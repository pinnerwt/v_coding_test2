import base64
import json

import httpx
import pytest

from agent.config import Config
from agent.server import build_app


def _llm_mock_done(answer="ok"):
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
                                    "id": "c",
                                    "type": "function",
                                    "function": {
                                        "name": "done",
                                        "arguments": json.dumps(
                                            {"status": "success", "answer": answer}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def _scripted_llm(steps):
    """Replays scripted (tool_name, args) tuples for tool calls; returns
    benign empty content for non-tool LLM calls (e.g. distill)."""
    it = iter(steps)

    async def handler(request):
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": ""}}]},
            )
        n, a = next(it)
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
                                    "function": {"name": n, "arguments": json.dumps(a)},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_session_started_persisted_first(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "5")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_llm_mock_done("ok"))
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "buy soap"})
        assert r.status_code == 200

    traces = sorted(
        p for p in (tmp_path / "traces").glob("*.jsonl") if not p.name.endswith(".llm.jsonl")
    )
    assert len(traces) == 1
    first_line = traces[0].read_text().splitlines()[0]
    ev = json.loads(first_line)
    assert ev["type"] == "session_started"
    assert ev["payload"]["goal"] == "buy soap"
    assert "sid" in ev["payload"]
    assert "started_at" in ev["payload"]


@pytest.mark.asyncio
async def test_ws_streams_done(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "5")
    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "false")
    html = b"<!doctype html><html><body><p>The answer is 42 to the question.</p></body></html>"
    data_url = "data:text/html;base64," + base64.b64encode(html).decode()
    transport = _scripted_llm(
        [
            ("goto", {"url": data_url, "reason": "open"}),
            ("read", {"reason": "verify"}),
            (
                "done",
                {
                    "status": "success",
                    "answer": "42",
                    "evidence": "answer is 42 to the question",
                    "reason": "done",
                },
            ),
        ]
    )
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=transport)
    from httpx import ASGITransport, AsyncClient

    asgi = ASGITransport(app=app)
    async with AsyncClient(transport=asgi, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "g"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success" and body["answer"] == "42"
