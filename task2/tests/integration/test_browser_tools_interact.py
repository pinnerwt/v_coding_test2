import base64
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

# Five anonymous selects (no accessible name) to expose the
# (role, name)+nth resolution bug that hit canirun.ai (case 113).
HTML_MANY_ANON_SELECTS = (
    "<!doctype html><html><body>"
    + "".join(
        f"<select><option>opt-{i}-A</option><option>opt-{i}-B</option></select>" for i in range(5)
    )
    + "</body></html>"
)


@pytest.mark.asyncio
async def test_click_type_select_press(tmp_path):
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
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
        await tools["click"](id=s_id, value="b")
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
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        combo = next(e for e in snap if e["role"] == "combobox")
        assert combo.get("options") == ["Google", "Custom", "RTX 3090 (24 GB)"]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_click_on_select_without_value_short_circuits():
    """Click on a real <select> with no value must produce an actionable
    error telling the LLM to pass `value` instead of looping. Mirrors the
    previous select_option-redirect behavior under the unified tool."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        out = await tools["click"](id=sid)
        assert out.startswith("ERROR:")
        assert "<select>" in out
        assert "value" in out
        assert "options" in out
        assert f"id={sid}" in out
        # Mirror the unit-test guard: the new unified-click error must not
        # mention `select_option`, since the LLM no longer has that tool.
        assert "select_option" not in out
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_interactive_ids_are_tagged_on_dom_nodes():
    """Each interactive element from list_interactive must carry a
    `data-agent-eid` attribute matching its id. Resolving locators by
    that attribute (instead of `get_by_role(role, name).nth(k)`) is what
    fixes the canirun.ai (case 113) bug: the AX-tree enumeration order
    can diverge from Playwright's role-matcher order when the AX tree
    contains synthetic nodes get_by_role doesn't see, and nth() then
    targets the wrong DOM node."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        # Every entry must have its eid stamped on the actual DOM element.
        for entry in snap:
            eid = entry["id"]
            tagged = await s.page.evaluate(
                'id => !!document.querySelector(`[data-agent-eid="${id}"]`)',
                eid,
            )
            assert tagged, f"id={eid} ({entry['role']}) has no data-agent-eid"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_stale_eid_attributes_cleared_between_snapshots():
    """Two pages back-to-back: tags from the first must not leak DOM
    state into the second snapshot's resolution. Re-snapshotting the
    same page is allowed to renumber from zero, so we just check that
    after the second snapshot, every surviving id resolves uniquely."""
    s = BrowserSession()
    await s.start()
    try:
        url1 = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        url2 = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url1)
        await tools["list_interactive"]()
        await tools["goto"](url=url2)
        await tools["list_interactive"]()
        snap2 = await s.snapshot()
        # Each id in the second snapshot must resolve to exactly one DOM node.
        for entry in snap2:
            count = await s.page.evaluate(
                'id => document.querySelectorAll(`[data-agent-eid="${id}"]`).length',
                entry["id"],
            )
            assert count == 1, f"id={entry['id']} resolves to {count} nodes"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_each_id_resolves_to_distinct_dom_node():
    """Bug from canirun.ai (case 113): list_interactive numbered N
    anonymous comboboxes 0..N-1 by AX-tree order, but select_option
    dispatched against `get_by_role('combobox').nth(k)` which counts
    by Playwright's role-matcher — a different ordering when the AX
    tree and role-matcher don't align. Result: the wrong <select> got
    mutated.

    Contract: setting a unique option on each id must mutate exactly
    that select, never another one. Equivalent to "each id resolves
    to a distinct DOM element"."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_MANY_ANON_SELECTS.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        combos = [e for e in snap if e["role"] == "combobox"]
        assert len(combos) == 5, f"expected 5 combos, got {len(combos)}"
        # Set the i-th select's value to opt-i-B via list_interactive id.
        for i, c in enumerate(combos):
            out = await tools["click"](id=c["id"], value=f"opt-{i}-B")
            assert out.startswith("selected"), f"combo {i}: {out}"
        # Each select must have its own option B set, not duplicates.
        values = await s.page.evaluate(
            "() => Array.from(document.querySelectorAll('select')).map(el => el.value)"
        )
        assert values == [f"opt-{i}-B" for i in range(5)], values
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_click_on_select_with_bad_value_fails_fast():
    """Default Playwright timeout is 30s — three failures = 90s of dead
    time in a 50-step budget. Cap to ~5s so the loop recovers fast."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        sid = next(e["id"] for e in snap if e["role"] == "combobox")
        t0 = time.monotonic()
        out = await tools["click"](id=sid, value="NotAnOption")
        elapsed = time.monotonic() - t0
        assert out.startswith("ERROR:")
        assert elapsed < 10.0, f"click took {elapsed:.1f}s; expected <10s"
    finally:
        await s.close()


HTML_LINKS = """<!doctype html><html><body>
<a href="/foo">Foo</a>
<a href="https://example.com/bar">Bar</a>
<a>NoHref</a>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_link_exposes_href():
    """Link entries must expose `href` resolved against the page URL.
    Without this, the goto-grounding guard cannot allow a goto to a URL
    that only ever appears as an anchor target (case 107: HF autocomplete
    rendered model name as text but URL was never in any obs)."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_LINKS.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        links = {e["name"]: e for e in snap if e["role"] == "link"}
        assert links["Foo"]["href"].endswith("/foo"), links["Foo"]
        assert links["Bar"]["href"] == "https://example.com/bar", links["Bar"]
        # Anchor with no href must not crash and must not invent a value.
        assert "NoHref" not in links, links
    finally:
        await s.close()


HTML_PLACEHOLDERS = """<!doctype html><html><body>
<input type=text placeholder="Search models, datasets, users…">
<input type=text>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_textbox_placeholder():
    """Textbox/searchbox entries must expose `placeholder` so the agent
    can pick the right input on the first try (HF homepage has 3+ text
    inputs; without placeholders the agent has to guess by id ordering)."""
    s = BrowserSession()
    await s.start()
    try:
        url = (
            "data:text/html;charset=utf-8;base64,"
            + base64.b64encode(HTML_PLACEHOLDERS.encode()).decode()
        )
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        textboxes = [e for e in snap if e["role"] in ("textbox", "searchbox")]
        with_ph = [e for e in textboxes if "placeholder" in e]
        assert any(e["placeholder"] == "Search models, datasets, users…" for e in with_ph), (
            textboxes
        )
        # An input with no placeholder attribute must not have the field.
        bare = [e for e in textboxes if "placeholder" not in e]
        assert bare, "expected at least one textbox without placeholder"
    finally:
        await s.close()


HTML_STATES = """<!doctype html><html><body>
<button aria-expanded="true">Menu</button>
<button disabled>Submit</button>
<input type=checkbox checked>
<input type=radio>
<select>
  <option selected>A</option>
  <option>B</option>
</select>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_state_booleans():
    """expanded/disabled/checked/selected must surface from the AX tree
    so the agent can tell that a menu is already open, a submit is
    greyed out, a box is already ticked, etc."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_STATES.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        by_name = {e["name"]: e for e in snap if e["name"]}
        assert by_name["Menu"].get("expanded") is True, by_name.get("Menu")
        assert by_name["Submit"].get("disabled") is True, by_name.get("Submit")
        cb = next(e for e in snap if e["role"] == "checkbox")
        assert cb.get("checked") is True, cb
        radio = next(e for e in snap if e["role"] == "radio")
        # Unchecked radio must NOT have checked: true. May omit the field
        # OR set it to false; pick one and stick with it for stability.
        assert not radio.get("checked"), radio
    finally:
        await s.close()


HTML_AUTOCOMPLETE = """<!doctype html><html><body>
<div role="combobox" aria-expanded="true" aria-controls="lb">
  <input type="text" value="bert">
</div>
<ul id="lb" role="listbox">
  <li role="option" tabindex="0">google-bert/bert-base-uncased</li>
  <li role="option" tabindex="0">nlpaueb/legal-bert-base-uncased</li>
</ul>
<div role="listbox" aria-expanded="false">
  <div role="option">should-not-appear</div>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_snapshot_listbox_options_appear_when_expanded():
    """Case-107 fix: when a listbox is expanded (autocomplete dropdown
    visible), its `option` children must be enumerated as clickable
    interactive entries. Collapsed listboxes' options stay hidden."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_AUTOCOMPLETE.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        options = [e for e in snap if e["role"] == "option"]
        names = {e["name"] for e in options}
        assert "google-bert/bert-base-uncased" in names, options
        assert "nlpaueb/legal-bert-base-uncased" in names, options
        assert "should-not-appear" not in names, options
    finally:
        await s.close()


HTML_SELECT_AND_LINK = """<!doctype html><html><body>
<select id=field><option>Title</option><option>Author</option></select>
<a id=ln href="/next">Next</a>
</body></html>"""


@pytest.mark.asyncio
async def test_click_with_value_on_link_flags_stale_eid():
    """Case-104 prevention: if the LLM thinks an eid is a <select> but it
    has been re-bound to <a> across snapshots, pass `value` and the tool
    must surface a stale-eid ERROR rather than silently clicking the link."""
    s = BrowserSession()
    await s.start()
    try:
        url = "data:text/html;base64," + base64.b64encode(HTML_SELECT_AND_LINK.encode()).decode()
        tools = build_browser_tools(s, restrict_goto=False)
        await tools["goto"](url=url)
        await tools["list_interactive"]()
        snap = await s.snapshot()
        link_id = next(e["id"] for e in snap if e["role"] == "link")
        out = await tools["click"](id=link_id, value="Title")
        assert out.startswith("ERROR:")
        assert "not a <select>" in out
        assert "list_interactive" in out
        # Page must NOT have navigated.
        assert s.page.url == url, s.page.url
    finally:
        await s.close()
