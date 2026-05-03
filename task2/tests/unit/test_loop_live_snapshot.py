"""When list_interactive is in tape[-3:], the loop must call session.snapshot()
again at message-build time and pass the result to build_messages as
interactive_elements.

When list_interactive is older than 3 actions, no extra snapshot, no section."""
from __future__ import annotations

import json

import pytest

from agent.loop import _maybe_live_interactive_payload


def _li(offset=0, limit=50):
    return {
        "action": "list_interactive",
        "args": {"offset": offset, "limit": limit},
        "obs": "ack",
        "url": "",
        "reason": "",
    }


def _read(obs="x"):
    return {"action": "read", "args": {}, "obs": obs, "url": "", "reason": ""}


class _RecordingSession:
    def __init__(self, entries):
        self._entries = entries
        self.snapshot_calls: list[dict] = []

    async def snapshot(self, *, offset=0, limit=50):
        self.snapshot_calls.append({"offset": offset, "limit": limit})
        return self._entries


@pytest.mark.asyncio
async def test_no_snapshot_when_list_interactive_absent_from_last_three():
    sess = _RecordingSession([])
    # list_interactive is at index 0; last 3 are reads.
    tape = [_li(), _read("x"), _read("y"), _read("z")]
    payload = await _maybe_live_interactive_payload(sess, tape)
    assert payload is None
    assert sess.snapshot_calls == []


@pytest.mark.asyncio
async def test_snapshot_when_list_interactive_in_last_three():
    sess = _RecordingSession([{"id": 0, "role": "link", "name": "Home"}])
    tape = [_read("x"), _li(), _read("y")]
    payload = await _maybe_live_interactive_payload(sess, tape)
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed == [{"id": 0, "role": "link", "name": "Home"}]
    assert sess.snapshot_calls == [{"offset": 0, "limit": 50}]


@pytest.mark.asyncio
async def test_snapshot_uses_most_recent_list_interactive_args():
    sess = _RecordingSession([])
    tape = [_li(0, 50), _li(50, 25), _read("z")]
    await _maybe_live_interactive_payload(sess, tape)
    assert sess.snapshot_calls == [{"offset": 50, "limit": 25}]


@pytest.mark.asyncio
async def test_snapshot_failure_returns_none_not_raises():
    """If the live re-snapshot fails (e.g., page navigated mid-flight),
    return None and let the loop continue without the section, rather than
    crashing the whole turn."""
    class _Boom:
        async def snapshot(self, **_):
            raise RuntimeError("boom")
    tape = [_li()]
    payload = await _maybe_live_interactive_payload(_Boom(), tape)
    assert payload is None
