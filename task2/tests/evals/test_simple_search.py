import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app

CANNED = [
    ("goto", {"url": "PLACEHOLDER", "thought": "open site"}),
    ("list_interactive", {"thought": "see elements"}),
    ("type", {"id": 0, "text": "hello", "submit": False, "thought": "type"}),
    ("click", {"id": 1, "thought": "search"}),
    ("read", {"thought": "verify"}),
    (
        "done",
        {
            "status": "success",
            "answer": "you searched: hello",
            "thought": "done",
        },
    ),
]


def _scripted_llm(url):
    canned = [
        (n, {**a, "url": url} if n == "goto" else a) for n, a in CANNED
    ]
    it = iter(canned)

    async def handler(request):
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
                                    "function": {
                                        "name": n,
                                        "arguments": json.dumps(a),
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
async def test_simple_search_eval(tmp_path, monkeypatch, http_fixture_server):
    url = http_fixture_server("simple/index.html")
    monkeypatch.setenv("MAX_STEPS", "10")
    app = build_app(
        cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_scripted_llm(url)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "search for hello"})
        body = r.json()
        assert body["status"] == "success"
        assert "hello" in body["answer"]
