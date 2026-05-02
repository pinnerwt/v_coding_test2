from agent.tools.browser import _extract_urls


def test_extract_urls_finds_http_and_https():
    s = "see https://en.wikipedia.org/wiki/Tokyo and http://x.test/a?b=c here"
    assert _extract_urls(s) == [
        "https://en.wikipedia.org/wiki/Tokyo",
        "http://x.test/a?b=c",
    ]


def test_extract_urls_strips_trailing_punctuation():
    s = "From https://arxiv.org. Visit https://github.com/repo) now."
    assert _extract_urls(s) == ["https://arxiv.org", "https://github.com/repo"]


def test_extract_urls_handles_empty():
    assert _extract_urls("") == []
    assert _extract_urls("no urls here") == []
