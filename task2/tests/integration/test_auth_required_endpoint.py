"""`/api/auth_required` lets the SPA discover whether to prompt for a
passcode before any other API call. It MUST stay unauthenticated — the
SPA needs to probe it pre-login — but it must not leak the token itself.
"""

from __future__ import annotations

import dataclasses

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Config
from agent.server import build_app


def _cfg(token: str | None) -> Config:
    return dataclasses.replace(Config.from_env(), agent_auth_token=token)


@pytest.mark.asyncio
async def test_reports_false_when_no_token_configured(tmp_path):
    app = build_app(cfg=_cfg(None), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/auth_required")
        assert r.status_code == 200
        assert r.json() == {"required": False}


@pytest.mark.asyncio
async def test_reports_true_when_token_configured(tmp_path):
    app = build_app(cfg=_cfg("sekret"), data_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # No bearer header — endpoint must still answer.
        r = await ac.get("/api/auth_required")
        assert r.status_code == 200
        body = r.json()
        assert body == {"required": True}
        # The token itself MUST NOT appear anywhere in the response.
        assert "sekret" not in r.text
