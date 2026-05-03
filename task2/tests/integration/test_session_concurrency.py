"""Spec: build_app exposes a session-concurrency semaphore on app.state with capacity 5.

The bench harness assumes the server can run multiple WebVoyager sessions in
parallel. When the limit was 1 the server silently queued every additional
caller — the queued caller had no way to tell, since the semaphore is acquired
before the session id / session_started event is emitted (see server.py
run_loop). This test pins the capacity so a regression to 1 (or an accidental
3) trips immediately.
"""

import pytest

from agent.config import Config
from agent.server import build_app


@pytest.mark.asyncio
async def test_session_semaphore_capacity_is_five(tmp_path):
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    sem = getattr(app.state, "session_semaphore", None)
    assert sem is not None, "build_app must expose app.state.session_semaphore"
    # asyncio.Semaphore exposes initial capacity via _value when no waiters
    # have acquired it. Cast through getattr to keep this resilient if a
    # wrapper class is introduced later that exposes a `.capacity` attribute.
    capacity = getattr(sem, "capacity", None) or sem._value
    assert capacity == 5, f"expected 5 concurrent sessions, got {capacity}"
