import json
from pathlib import Path

from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, write_output


def test_registered():
    assert "write_output" in REGISTRY
    assert REGISTRY["write_output"].SCHEMA["function"]["name"] == "write_output"


def test_returns_error_when_no_records(tmp_path: Path):
    state = SessionState()
    out = tmp_path / "x.json"
    result = write_output.run(state, {"json_path": str(out)})
    assert "error" in result
    assert not out.exists()


def test_writes_json_round_trip(tmp_path: Path):
    state = SessionState()
    state.records = [
        {
            "part": "I",
            "item_number": "1",
            "item_title": "Business",
            "content_text": "body",
            "char_range": [0, 4],
            "status": "extracted",
        }
    ]
    out = tmp_path / "sub" / "x.json"  # parent doesn't exist
    result = write_output.run(state, {"json_path": str(out)})
    assert result["path"] == str(out)
    assert result["count"] == 1
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded == state.records
