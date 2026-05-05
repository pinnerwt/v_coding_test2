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


def test_run_returns_head_and_tail_previews(tmp_path: Path):
    # The agent uses head/tail previews to detect tail-cross-ref index pages
    # without 5+ probe round-trips. Both should be present and trimmed to a
    # reasonable cap.
    p = tmp_path / "f.html"
    head = "TOP " * 1000  # 4000 chars
    middle = "MID " * 5000  # 20000 chars
    tail_marker = "10-K CROSS-REFERENCE INDEX\nITEM 1. Business\nITEM 2. Properties\n"
    body = head + middle + tail_marker
    p.write_text(f"<pre>{body}</pre>")
    state = SessionState()
    result = clean_and_load.run(state, {"html_path": str(p)})
    assert "head_preview" in result
    assert "tail_preview" in result
    assert result["head_preview"].startswith("TOP")
    assert "CROSS-REFERENCE INDEX" in result["tail_preview"]
    # Cap previews — large docs should not blow context.
    assert len(result["head_preview"]) <= 3000
    assert len(result["tail_preview"]) <= 3000


def test_run_short_doc_previews_dont_overflow(tmp_path: Path):
    # If full text < 2 * preview cap, head/tail still return cleanly without
    # error and may overlap.
    p = tmp_path / "f.html"
    p.write_text("<p>tiny doc body</p>")
    state = SessionState()
    result = clean_and_load.run(state, {"html_path": str(p)})
    assert "tiny" in result["head_preview"]
    assert "body" in result["tail_preview"]
