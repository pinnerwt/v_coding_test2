"""list_interactive returns a short ack string, not the JSON snapshot.

The snapshot data flows to the LLM via build_messages's interactive_elements
section, re-rendered fresh each turn. The tool's return value is the obs that
lands in the tape — kept small so it doesn't bloat the recent-3-obs window."""
import pytest

from agent.tools.browser import build_browser_tools


class _FakeSession:
    def __init__(self, entries):
        self._entries = entries
        self.snapshot_calls: list[dict] = []

    async def snapshot(self, *, offset=0, limit=50):
        self.snapshot_calls.append({"offset": offset, "limit": limit})
        return self._entries

    @property
    def page(self):
        raise RuntimeError("not used in this test")


@pytest.mark.asyncio
async def test_list_interactive_returns_ack_not_json():
    fake = _FakeSession([{"id": 0, "role": "link", "name": "x"}] * 5)
    tools = build_browser_tools(fake, restrict_goto=False)
    obs = await tools["list_interactive"](offset=0, limit=50)
    # The obs must NOT be the JSON snapshot — that would defeat the purpose.
    assert not obs.startswith("[")
    # Must mention it took a snapshot and how many elements.
    assert "snapshot taken" in obs
    assert "5 elements" in obs
    # Must point the LLM at the live section.
    assert "Interactive elements" in obs


@pytest.mark.asyncio
async def test_list_interactive_still_calls_snapshot():
    """Eids must be tagged on the live DOM right now — same-turn click safety."""
    fake = _FakeSession([])
    tools = build_browser_tools(fake, restrict_goto=False)
    obs = await tools["list_interactive"](offset=2, limit=10)
    assert fake.snapshot_calls == [{"offset": 2, "limit": 10}]
    assert "offset=2" in obs and "limit=10" in obs
