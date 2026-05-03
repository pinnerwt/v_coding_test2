from agent.trace import TraceWriter, read_trace


def test_round_trip(tmp_path):
    p = tmp_path / "t.jsonl"
    w = TraceWriter(p)
    w.write({"type": "step", "payload": {"n": 1, "action": "goto", "args": {"url": "x"}}})
    w.write({"type": "done", "payload": {"status": "success", "answer": "ok"}})
    events = read_trace(p)
    assert [e["type"] for e in events] == ["step", "done"]
    assert events[0]["payload"]["action"] == "goto"
    assert all("ts" in e for e in events)
