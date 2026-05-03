import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app

CANNED = [
    ("goto", {"url": "PLACEHOLDER", "reason": "open site"}),
    ("list_interactive", {"reason": "see elements"}),
    ("type", {"id": 0, "text": "hello", "submit": False, "reason": "type"}),
    ("click", {"id": 1, "reason": "search"}),
    ("read", {"reason": "verify"}),
    (
        "done",
        {
            "status": "success",
            "answer": "you searched: hello",
            "evidence": "you searched: hello",
            "reason": "done",
        },
    ),
]


def _scripted_llm(url):
    canned = [(n, {**a, "url": url} if n == "goto" else a) for n, a in CANNED]
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
    # The fixture site binds to 127.0.0.1, which the production goto guard
    # blocks (loopback / SSRF protection). Bypass the guard so the LLM-mocked
    # eval can drive a real browser against the local fixture.
    monkeypatch.setattr("agent.tools.browser.is_safe_goto_url", lambda _u: (True, ""))
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path, llm_transport=_scripted_llm(url))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "search for hello"})
        body = r.json()
        assert body["status"] == "success"
        assert "hello" in body["answer"]
