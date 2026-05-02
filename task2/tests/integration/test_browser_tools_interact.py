import base64
import json
import time

import pytest

from agent.browser_session import BrowserSession
from agent.tools.browser import build_browser_tools

HTML = """<!doctype html><html><body>
<button id=b onclick="document.title='clicked'">Go</button>
<input id=i type=text>
<select id=s><option>a</option><option value=b>B</option></select>
<script>
document.getElementById('i').addEventListener('keydown', e => {
  if (e.key==='Enter') document.title='submitted';
});
</script>
</body></html>"""

HTML_SELECT = """<!doctype html><html><body>
<select id=gpu>
  <option>Google</option>
  <option value=custom>Custom</option>
  <option>RTX 3090 (24 GB)</option>
</select>
</body></html>"""


@pytest.mark.asyncio
async def test_click_type_select_press(tmp_path):
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s)
        await tools["goto"](url=url)
        snap = await s.snapshot()
        by_role = {(e["role"], e["name"]): e["id"] for e in snap}

        b_id = by_role[("button", "Go")]
        await tools["click"](id=b_id)
        assert await s.page.title() == "clicked"

        i_id = by_role[("textbox", "")]
        await tools["type"](id=i_id, text="hello", submit=True)
        assert await s.page.title() == "submitted"
        assert await s.page.input_value("#i") == "hello"

        s_id = by_role[("combobox", "")]
        await tools["select_option"](id=s_id, value="b")
        assert await s.page.eval_on_selector("#s", "el => el.value") == "b"

        await tools["press_key"](key="Escape")
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_list_interactive_emits_options_for_select():
    """Without `options`, the model has to *guess* what value to pass to
    select_option. canirun.ai bench (case 113) showed it then waits 30s
    on a non-matching value. Surfacing options closes that gap."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        combo = next(e for e in snap if e["role"] == "combobox")
        assert combo.get("options") == ["Google", "Custom", "RTX 3090 (24 GB)"]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_click_on_select_short_circuits():
    """Clicking a real <select> never produces a DOM-visible dropdown in
    Playwright. Short-circuit with an instructive error so the model
    switches to select_option immediately instead of looping."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        out = await tools["click"](id=sid)
        assert out.startswith("ERROR:")
        assert "select_option" in out
        assert f"id={sid}" in out
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_select_option_failure_fast():
    """Default Playwright timeout is 30s — three failures = 90s of dead
    time in a 50-step budget. Cap to ~5s so the loop recovers fast."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s)
        await tools["goto"](url=url)
        snap = json.loads(await tools["list_interactive"]())
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        t0 = time.monotonic()
        out = await tools["select_option"](id=sid, value="NotAnOption")
        elapsed = time.monotonic() - t0
        assert out.startswith("ERROR:")
        assert elapsed < 10.0, f"select_option took {elapsed:.1f}s; expected <10s"
    finally:
        await s.close()
