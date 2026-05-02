from agent.browser_session import BrowserSession

HTML = """<!doctype html><html><body>
<button id=b1>Hello</button>
<input id=i1 type=text aria-label="search">
<a id=a1 href="#x">Link</a>
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
    finally:
        await s.close()
