import base64

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
