"""Spec: a session that waits on the concurrency semaphore receives a
'queued' event over its live channel before session_started fires.

Today the semaphore is acquired silently inside run_loop, so a queued caller
cannot tell the difference between "running but slow" and "blocked behind 5
others." We fix that by:

1. Pre-allocating the session id BEFORE the semaphore.
2. Emitting a `queued` event via `send_event` (the live channel) — NOT into
   the trace file — when the in-flight counter is at or above capacity.
3. Including `queued_for_ms` in the run_sync response body.

Trace files keep their existing contract: line 1 is session_started.
The 'queued' signal lives on the live channel only.
"""

import asyncio

import pytest

from agent.config import Config
from agent.server import _acquire_with_queue_notify, build_app


@pytest.mark.asyncio
async def test_in_flight_counter_starts_zero(tmp_path):
    app = build_app(cfg=Config.from_env(), data_dir=tmp_path)
    assert getattr(app.state, "sessions_in_flight", None) == 0


@pytest.mark.asyncio
async def test_acquire_skips_queued_event_when_capacity_available():
    sem = asyncio.Semaphore(5)
    events: list[dict] = []

    async def send_event(ev):
        events.append(ev)

    waited_ms = await _acquire_with_queue_notify(
        sem=sem,
        capacity=5,
        sid="abc",
        in_flight=0,
        send_event=send_event,
    )
    assert events == []
    assert waited_ms < 50
    sem.release()


@pytest.mark.asyncio
async def test_acquire_emits_queued_when_saturated():
    sem = asyncio.Semaphore(2)
    # Saturate.
    await sem.acquire()
    await sem.acquire()
    events: list[dict] = []

    async def send_event(ev):
        events.append(ev)

    async def releaser():
        await asyncio.sleep(0.05)
        sem.release()

    asyncio.create_task(releaser())

    waited_ms = await _acquire_with_queue_notify(
        sem=sem,
        capacity=2,
        sid="abc123",
        in_flight=2,
        send_event=send_event,
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "queued"
    assert ev["payload"]["sid"] == "abc123"
    assert ev["payload"]["ahead"] >= 1
    assert waited_ms >= 40
    sem.release()
    sem.release()


@pytest.mark.asyncio
async def test_run_loop_injects_sid_into_result():
    """Under parallel runs, the bench harness can't map cases→traces by
    mtime. The server must return the session id in the response body.
    Structural test — guards against regression. Behavioral coverage via
    the live sweep through scripts/bench_webvoyager.py.
    """
    import inspect

    from agent.server import build_app

    src = inspect.getsource(build_app)
    assert 'result["sid"] = session_id' in src, (
        "run_loop must inject sid into the result dict for parallel runs"
    )


async def test_acquire_emits_event_before_blocking():
    """Event must fire BEFORE the acquire blocks, so a UI can show 'queued'
    immediately rather than only after the semaphore releases."""
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    events: list[dict] = []
    event_seen = asyncio.Event()

    async def send_event(ev):
        events.append(ev)
        event_seen.set()

    acquire_task = asyncio.create_task(
        _acquire_with_queue_notify(
            sem=sem,
            capacity=1,
            sid="x",
            in_flight=1,
            send_event=send_event,
        )
    )
    # The queued event should be sent before the semaphore is released.
    await asyncio.wait_for(event_seen.wait(), timeout=0.5)
    assert len(events) == 1
    assert events[0]["type"] == "queued"
    assert not acquire_task.done(), "acquire must still be blocked on the sem"
    sem.release()
    await acquire_task
    sem.release()
