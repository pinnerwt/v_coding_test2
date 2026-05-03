from agent.page_diff import OffsetCache


def test_offset_cache_records_what_was_served():
    c = OffsetCache()
    c.record(offset=0, served="hello world")
    assert c.was_served(offset=0, candidate="hello world") is True
    assert c.was_served(offset=0, candidate="hello WORLD") is False


def test_offset_cache_independent_per_offset():
    c = OffsetCache()
    c.record(offset=0, served="A")
    c.record(offset=1600, served="B")
    assert c.was_served(offset=0, candidate="A") is True
    assert c.was_served(offset=1600, candidate="B") is True
    assert c.was_served(offset=0, candidate="B") is False


def test_offset_cache_clear_resets_all():
    c = OffsetCache()
    c.record(offset=0, served="A")
    c.record(offset=1600, served="B")
    c.clear()
    assert c.was_served(offset=0, candidate="A") is False
    assert c.was_served(offset=1600, candidate="B") is False


def test_offset_cache_empty_lookup_returns_false():
    c = OffsetCache()
    assert c.was_served(offset=0, candidate="anything") is False
