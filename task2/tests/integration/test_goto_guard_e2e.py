import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


def _scripted_llm(steps):
    """steps: list[(tool_name, args_dict)] — replays in order for agent (tool) calls.

    Non-tool LLM calls (e.g. distillation) get an empty assistant content reply and
    do NOT consume the script.
    """
    it = iter(steps)

    async def handler(request):
        body = json.loads(request.content)
        # Distillation / non-tool calls: respond benign, don't consume script.
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
async def test_goto_blocked_when_url_not_in_goal_or_tape(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "true")
    monkeypatch.setenv("MAX_STEPS", "4")

    transport = _scripted_llm(
        [
            # Try to jump to an unobserved URL
            ("goto", {"url": "https://arxiv.org/abs/1406.2661"}),
            # Then give up
            ("done", {"status": "failed", "answer": "blocked"}),
        ]
    )

    app = build_app(
        cfg=Config.from_env(),
        data_dir=tmp_path,
        llm_transport=transport,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/run_sync",
            json={"goal": "Go to https://wikipedia.org and find X"},
        )
        assert r.status_code == 200

    # Trace must contain a step whose obs starts with the block marker
    traces = [p for p in (tmp_path / "traces").glob("*.jsonl") if not p.name.endswith(".llm.jsonl")]
    assert traces, "no trace written"
    blocked = False
    for line in traces[0].read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "step" and ev["payload"].get("obs", "").startswith(
            "ERROR: blocked goto"
        ):
            blocked = True
    assert blocked, "expected a blocked-goto step in trace"

    # Also expect a distinct goto_blocked event for bench/UI scoring
    saw_event = False
    for line in traces[0].read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "goto_blocked":
            assert ev["payload"]["url"] == "https://arxiv.org/abs/1406.2661"
            saw_event = True
    assert saw_event, "expected a goto_blocked trace event"
