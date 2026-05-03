import pytest

from agent.browser_session import BrowserSession
from agent.tools.browser import build_browser_tools

HTML = """<!doctype html><html><head><title>T</title></head><body>
<h1>Welcome</h1><p>The quick brown fox jumps over the lazy dog. Needle here. End.</p>
<button>Go</button></body></html>"""


@pytest.mark.asyncio
async def test_goto_read_grep_list(tmp_path):
    s = BrowserSession()
    await s.start()
    try:
        # Serve HTML via data URL
        url = "data:text/html;base64," + __import__("base64").b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)

        obs = await tools["goto"](url=url)
        assert "navigated" in obs.lower()

        text = await tools["read"]()
        assert "Welcome" in text and len(text) <= 2000

        grep = await tools["read_grep"](pattern="needle", context=20)
        assert "Needle" in grep
        assert "[@" in grep

        listing = await tools["list_interactive"]()
        assert "snapshot taken" in listing
        snap = await s.snapshot()
        assert any(e["role"] == "button" and "Go" in e.get("name", "") for e in snap)
    finally:
        await s.close()
