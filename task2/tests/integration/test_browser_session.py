from agent.browser_session import BrowserSession

HTML = """<!doctype html><html><body>
<button id=b1 onclick="document.title='clicked-hello'">Hello</button>
<input id=i1 type=text aria-label="search">
<a id=a1 href="#x">Link</a>
</body></html>"""

DUP_HTML = """<!doctype html><html><body>
<button id=b1 onclick="document.body.setAttribute('data-first','1')">Save</button>
<button id=b2 onclick="document.body.setAttribute('data-second','1')">Save</button>
</body></html>"""


async def test_snapshot_assigns_ids():
    s = BrowserSession()
    await s.start()
    try:
        await s.page.set_content(HTML)
        snap = await s.snapshot()
        names = {e["name"] for e in snap}
        roles = {e["role"] for e in snap}
        assert "Hello" in names
        assert "search" in names or "" in names  # input may have empty name
        assert "button" in roles
        assert "link" in roles
        # IDs are unique ints
        ids = [e["id"] for e in snap]
        assert ids == sorted(set(ids))

        # Click round-trip: locate the button by id and confirm the side effect.
        button_id = next(e["id"] for e in snap if e["role"] == "button" and e["name"] == "Hello")
        await s.locator(button_id).click()
        assert await s.page.title() == "clicked-hello"
    finally:
        await s.close()


async def test_duplicate_role_name_resolves_distinctly():
    s = BrowserSession()
    await s.start()
    try:
        await s.page.set_content(DUP_HTML)
        snap = await s.snapshot()
        save_buttons = [e for e in snap if e["role"] == "button" and e["name"] == "Save"]
        assert len(save_buttons) == 2, snap

        # Click each one; each must trigger its own distinct side effect under strict mode.
        await s.locator(save_buttons[0]["id"]).click()
        await s.locator(save_buttons[1]["id"]).click()

        first = await s.page.evaluate("document.body.getAttribute('data-first')")
        second = await s.page.evaluate("document.body.getAttribute('data-second')")
        assert first == "1"
        assert second == "1"
    finally:
        await s.close()
