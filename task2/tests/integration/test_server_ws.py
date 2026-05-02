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


@pytest.mark.asyncio
async def test_ws_streams_done(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "5")
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_llm_mock_done("42"))
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "g"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success" and body["answer"] == "42"
