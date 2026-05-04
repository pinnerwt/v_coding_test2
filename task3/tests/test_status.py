import pytest

from sec_toolbox.segment import Slice
from sec_toolbox.status import classify
from sec_toolbox.toc import TOCEntry


def make_slice(content_text: str, item_number: str = "1") -> Slice:
    entry = TOCEntry(text=f"Item {item_number}.", target=None, source_start=0, text_start=0)
    return Slice(
        item_title="Test",
        content_text=content_text,
        char_range=(0, len(content_text.encode("utf-8"))),
        text_range=(0, len(content_text)),
        entry=entry,
        part="I",
        item_number=item_number,
        canonical_title="Test",
    )


def test_long_slice_is_extracted_without_llm(monkeypatch):
    long_slice = make_slice(content_text="word " * 1000)  # ~1000 whitespace tokens

    def fail_llm(_text):
        pytest.fail("LLM should not be called for long slices")

    monkeypatch.setattr("sec_toolbox.status._llm_read", fail_llm)
    result = classify(long_slice)
    assert result.status == "extracted"


def test_short_slice_calls_llm(monkeypatch):
    captured = {}

    def fake_read(text):
        captured["text"] = text
        return "substantive"

    monkeypatch.setattr("sec_toolbox.status._llm_read", fake_read)
    s = make_slice(content_text="Short blurb.")
    result = classify(s)
    assert result.status == "extracted"
    assert "Short blurb" in captured["text"]
