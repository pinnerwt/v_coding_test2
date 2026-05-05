from pathlib import Path

from extract_agent.state import SessionState
from extract_agent.tools import REGISTRY, clean_and_load


def test_registered():
    assert "clean_and_load" in REGISTRY
    assert REGISTRY["clean_and_load"].SCHEMA["function"]["name"] == "clean_and_load"


def test_run_loads_html_and_returns_text_id(tmp_path: Path):
    p = tmp_path / "f.html"
    p.write_text("<p>Hello</p><p>World</p>")
    state = SessionState()
    result = clean_and_load.run(state, {"html_path": str(p)})
    assert "text_id" in result
    assert result["length"] > 0
    text = state.get_text(result["text_id"])
    assert "Hello" in text and "World" in text


def test_run_applies_extra_strip_patterns(tmp_path: Path):
    p = tmp_path / "f.html"
    p.write_text("<p>Apple Inc. | 2023 Form 10-K | 17</p><p>Body</p>")
    state = SessionState()
    result = clean_and_load.run(
        state,
        {
            "html_path": str(p),
            "extra_strip_patterns": [r"\nApple Inc\.\s*\|\s*2023 Form 10-K\s*\|\s*\d+\s*\n"],
        },
    )
    text = state.get_text(result["text_id"])
    assert "Form 10-K" not in text
