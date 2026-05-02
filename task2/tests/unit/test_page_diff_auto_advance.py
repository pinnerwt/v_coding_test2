from agent.page_diff import OffsetCache, plan_read

READ_LIMIT = 1600
MAX_HOPS = 32


def test_plan_read_no_cache_returns_requested_offset():
    text = "X" * 5000
    cache = OffsetCache()
    plan = plan_read(
        text=text, requested_offset=0, cache=cache, read_limit=READ_LIMIT, max_hops=MAX_HOPS
    )
    assert plan.served_offset == 0
    assert plan.served_text == text[0:1600]
    assert plan.advanced_from is None
    assert plan.exhausted is False


def test_plan_read_advances_when_window_already_served():
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])  # already saw 'A'*1600

    plan = plan_read(
        text=text, requested_offset=0, cache=cache, read_limit=READ_LIMIT, max_hops=MAX_HOPS
    )
    assert plan.served_offset == 1600
    assert plan.served_text == text[1600:3200]
    assert plan.advanced_from == 0
    assert plan.exhausted is False


def test_plan_read_walks_multiple_hops_until_changed():
    # offsets 0 and 1600 both already served as their current content
    text = ("A" * 1600) + ("B" * 1600) + ("C" * 1600)
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])
    cache.record(offset=1600, served=text[1600:3200])

    plan = plan_read(
        text=text, requested_offset=0, cache=cache, read_limit=READ_LIMIT, max_hops=MAX_HOPS
    )
    assert plan.served_offset == 3200
    assert plan.served_text == text[3200:4800]
    assert plan.advanced_from == 0


def test_plan_read_exhausted_when_past_end():
    text = "A" * 1600
    cache = OffsetCache()
    cache.record(offset=0, served=text[0:1600])

    plan = plan_read(
        text=text, requested_offset=0, cache=cache, read_limit=READ_LIMIT, max_hops=MAX_HOPS
    )
    assert plan.exhausted is True
    assert plan.served_text == ""
    assert plan.advanced_from == 0


def test_plan_read_respects_max_hops():
    text = "A" * 1600 * 100  # 100 windows of identical content
    cache = OffsetCache()
    for off in range(0, len(text), 1600):
        cache.record(offset=off, served=text[off : off + 1600])

    plan = plan_read(text=text, requested_offset=0, cache=cache, read_limit=1600, max_hops=4)
    assert plan.exhausted is True  # ran out of hops without finding new content


def test_plan_read_truncates_to_text_length():
    text = "ABC"
    cache = OffsetCache()
    plan = plan_read(text=text, requested_offset=0, cache=cache, read_limit=1600, max_hops=MAX_HOPS)
    assert plan.served_text == "ABC"
    assert plan.exhausted is False  # served what's available
