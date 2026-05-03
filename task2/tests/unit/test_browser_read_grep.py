import pytest

from agent.tools.browser import build_browser_tools


class _FakePage:
    def __init__(self, text: str):
        self._text = text
        self.url = "https://x.test/"

    async def evaluate(self, script: str):
        return self._text


class _FakeSession:
    def __init__(self, text: str):
        self.page = _FakePage(text)


def _make(text: str):
    return build_browser_tools(_FakeSession(text))["read_grep"]


@pytest.mark.asyncio
async def test_read_grep_no_match_explicit_with_page_length_and_vocab():
    text = "Definite integral:\nStep-by-step solution\nIndefinite integral:\n"
    grep = _make(text)
    obs = await grep(pattern="x = 9")
    assert obs.startswith("NO MATCH")
    assert f"({len(text)} chars)" in obs
    assert "Definite integral" in obs or "Indefinite integral" in obs


@pytest.mark.asyncio
async def test_read_grep_single_match_marks_offset_and_count():
    text = "the quick brown fox at needle position\n"
    grep = _make(text)
    obs = await grep(pattern="needle")
    assert obs.startswith("1 match for")
    assert "[@" in obs and "needle" in obs


@pytest.mark.asyncio
async def test_read_grep_many_matches_lists_with_offsets_and_count():
    text = "alpha 2018 one\nbeta 2018 two\ngamma 2018 three\n"
    grep = _make(text)
    obs = await grep(pattern="2018")
    assert "3 matches for" in obs
    assert obs.count("[@") == 3


@pytest.mark.asyncio
async def test_read_grep_caps_at_max_matches_and_indicates_more():
    text = ("XYZ " * 20).strip()
    grep = _make(text)
    obs = await grep(pattern="XYZ", max_matches=5)
    assert "20 matches for" in obs
    assert "Showing matches 0-4 of 20" in obs
    assert "more matches" in obs
    assert "offset=5" in obs


@pytest.mark.asyncio
async def test_read_grep_offset_pages_through_matches():
    text = ("XYZ " * 20).strip()
    grep = _make(text)
    obs = await grep(pattern="XYZ", max_matches=5, offset=5)
    assert "Showing matches 5-9 of 20" in obs
    assert obs.count("[@") == 5


@pytest.mark.asyncio
async def test_read_grep_offset_past_end_is_explicit():
    text = ("XYZ " * 3).strip()
    grep = _make(text)
    obs = await grep(pattern="XYZ", offset=10)
    assert obs.startswith("NO MORE MATCHES")
    assert "3 total" in obs


@pytest.mark.asyncio
async def test_read_grep_context_controls_snippet_width_only_not_match_count():
    text = "AAA needle BBB needle CCC needle DDD"
    grep = _make(text)
    narrow = await grep(pattern="needle", context=4)
    wide = await grep(pattern="needle", context=20)
    assert "3 matches for" in narrow and "3 matches for" in wide
    assert len(wide) > len(narrow)
