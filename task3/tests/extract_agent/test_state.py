import pytest

from extract_agent.state import SessionState


def test_text_store_round_trip():
    s = SessionState()
    tid = s.store_text("hello world")
    assert s.get_text(tid) == "hello world"


def test_text_ids_are_unique():
    s = SessionState()
    a = s.store_text("a")
    b = s.store_text("b")
    assert a != b


def test_records_default_none():
    s = SessionState()
    assert s.records is None


def test_cost_accumulates():
    s = SessionState()
    s.add_cost(0.10)
    s.add_cost(0.05)
    assert s.cost_usd == pytest.approx(0.15)


def test_step_counter():
    s = SessionState()
    assert s.steps == 0
    s.bump_step()
    s.bump_step()
    assert s.steps == 2
