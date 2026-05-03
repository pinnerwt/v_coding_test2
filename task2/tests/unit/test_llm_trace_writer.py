import json
from pathlib import Path

from agent.llm_trace import LLMTraceWriter, truncate_for_log


def test_truncate_short_string_passes_through():
    assert truncate_for_log("hello") == "hello"


def test_truncate_long_string_returns_dict_with_original_size():
    s = "x" * 15000
    out = truncate_for_log(s)
    assert isinstance(out, dict)
    assert out["truncated"] == "x" * 12000
    assert out["original_chars"] == 15000


def test_truncate_respects_custom_cap():
    out = truncate_for_log("y" * 50, max_chars=10)
    assert out == {"truncated": "y" * 10, "original_chars": 50}


def test_writer_creates_parent_dir_and_appends_jsonl(tmp_path: Path):
    p = tmp_path / "deep" / "nested" / "abc.llm.jsonl"
    w = LLMTraceWriter(p)
    w.write({"call_idx": 0, "request": {"model": "m"}})
    w.write({"call_idx": 1, "request": {"model": "m"}})

    lines = p.read_text().splitlines()
    assert len(lines) == 2
    e0 = json.loads(lines[0])
    e1 = json.loads(lines[1])
    assert e0["call_idx"] == 0
    assert e1["call_idx"] == 1
    # ts auto-stamped if missing
    assert "ts" in e0 and "ts" in e1


def test_writer_preserves_explicit_ts(tmp_path: Path):
    p = tmp_path / "x.llm.jsonl"
    w = LLMTraceWriter(p)
    w.write({"call_idx": 0, "ts": "2026-01-01T00:00:00+00:00"})
    line = p.read_text().splitlines()[0]
    assert json.loads(line)["ts"] == "2026-01-01T00:00:00+00:00"
