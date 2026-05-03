"""Bearer-token auth on the public endpoints.

The agent runs on Zeabur as a public service; without auth, anyone can
drive a real Chromium against any target, drain the LLM budget, and read
every prior session's trace (which contains scraped page content). When
`Config.agent_auth_token` is set, every `/api/*` request and the `/ws`
upgrade must present `Authorization: Bearer <token>`. When unset (local
dev), the API stays open.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


def _cfg(token: str | None) -> Config:
    base = Config.from_env()
    return dataclasses.replace(base, agent_auth_token=token)


@pytest.mark.asyncio
async def test_no_token_set_endpoints_are_open(tmp_path):
    app = build_app(cfg=_cfg(None), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        assert (await ac.get("/api/sessions")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/sessions", "/api/llm_health", "/api/trace/abc"])
async def test_token_set_missing_header_rejected(tmp_path, path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get(path)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_set_wrong_token_rejected(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/sessions", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_set_correct_token_accepted(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/sessions", headers={"Authorization": "Bearer sekret"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_sync_post_requires_token(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/run_sync", json={"goal": "hi"})
        assert r.status_code == 401


def test_index_page_remains_public(tmp_path):
    """The HTML shell at `/` is just static markup — gating it doesn't add
    any security (the API is what actually does work). Keep `/` reachable
    so the operator can load the UI and then supply the token."""
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    client = TestClient(app)
    assert client.get("/").status_code == 200


def test_ws_rejects_without_token(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    client = TestClient(app)
    with (
        pytest.raises(Exception),  # noqa: B017,PT011
        client.websocket_connect("/ws"),
    ):
        pass


def test_ws_accepts_with_token_query(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    client = TestClient(app)
    # Should accept the upgrade; we close immediately to avoid driving the
    # full agent loop in this auth test.
    with client.websocket_connect("/ws?token=sekret"):
        pass
