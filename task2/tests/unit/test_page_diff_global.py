from agent.page_diff import (
    GlobalTextCache,
    diff_char_size,
    format_small_diff,
    should_inject_diff,
)


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


def test_diff_char_size_counts_added_plus_removed():
    size = diff_char_size(previous="abc def\nghi\n", current="abc XYZ\nghi\n")
    # one removed line "abc def" (7 chars) + one added "abc XYZ" (7 chars) = 14
    assert size == 14


def test_should_inject_diff_below_threshold():
    assert should_inject_diff(previous="A", current="AB", threshold=10) is True


def test_should_inject_diff_above_threshold_returns_false():
    huge_prev = "x" * 5000
    huge_curr = "y" * 5000
    assert should_inject_diff(previous=huge_prev, current=huge_curr, threshold=500) is False


def test_should_inject_diff_no_change_returns_false():
    assert should_inject_diff(previous="same", current="same", threshold=500) is False
