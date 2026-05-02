from agent.page_diff import GlobalTextCache, format_small_diff


def test_global_cache_initial_state_no_previous():
    g = GlobalTextCache()
    assert g.previous() is None


def test_global_cache_update_returns_diff_size():
    g = GlobalTextCache()
    g.update("hello world")
    assert g.previous() == "hello world"
    g.update("hello brave new world")
    assert g.previous() == "hello brave new world"


def test_format_small_diff_renders_unified_added_removed():
    out = format_small_diff(
        previous="line A\nline B\nline C\n",
        current="line A\nline B2\nline C\n",
        max_lines=10,
    )
    assert "Page changes since last turn" in out
    assert "+ " in out and "line B2" in out
    assert "- " in out and "line B" in out


def test_format_small_diff_caps_at_max_lines():
    prev = "\n".join(f"old {i}" for i in range(50))
    curr = "\n".join(f"new {i}" for i in range(50))
    out = format_small_diff(previous=prev, current=curr, max_lines=4)
    body_lines = [ln for ln in out.splitlines() if ln.startswith(("  + ", "  - "))]
    assert 1 <= len(body_lines) <= 4


def test_format_small_diff_returns_empty_when_no_change():
    out = format_small_diff(previous="same", current="same", max_lines=10)
    assert out == ""
