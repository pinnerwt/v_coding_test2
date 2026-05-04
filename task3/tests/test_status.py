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


def test_short_slice_routed_to_ibr(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _t: "incorporated_by_reference")
    s = make_slice(
        content_text=(
            "The information required by Item 11 is hereby incorporated by reference "
            "to the 2026 Proxy Statement."
        ),
        item_number="11",
    )
    assert classify(s).status == "incorporated_by_reference"


def test_short_slice_not_applicable(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _t: "not_applicable")
    s = make_slice(content_text="None.")
    assert classify(s).status == "not_applicable"


def test_short_slice_reserved(monkeypatch):
    monkeypatch.setattr("sec_toolbox.status._llm_read", lambda _t: "reserved")
    s = make_slice(content_text="[Reserved]", item_number="6")
    assert classify(s).status == "reserved"


def test_llm_read_calls_client_with_prompt(monkeypatch):
    """The real _llm_read should use the prompt file and the LLM client."""
    captured = {}

    class FakeClient:
        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            return "incorporated_by_reference"

    monkeypatch.setattr("sec_toolbox.status._build_client", lambda: FakeClient())
    # Clear the cache so this test isn't satisfied by a previous call
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    from sec_toolbox.status import _llm_read

    label = _llm_read("Item 11 incorporated by reference to the 2026 proxy.")
    assert label == "incorporated_by_reference"
    # Prompt must include the slice text
    msgs = captured["messages"]
    full = " ".join(m["content"] for m in msgs)
    assert "Item 11" in full
    # Prompt must mention the four labels
    for label_name in ["substantive", "incorporated_by_reference", "not_applicable", "reserved"]:
        assert label_name in full


def test_llm_read_caches_by_content_hash(monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        def chat(self, messages, **kwargs):
            calls["n"] += 1
            return "substantive"

    monkeypatch.setattr("sec_toolbox.status._build_client", lambda: FakeClient())
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})
    from sec_toolbox.status import _llm_read

    _llm_read("identical text")
    _llm_read("identical text")
    assert calls["n"] == 1, "second call should be served from cache"
