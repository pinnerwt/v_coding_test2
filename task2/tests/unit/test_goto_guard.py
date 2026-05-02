from agent.tools.browser import _extract_urls, _is_goto_allowed


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


def test_is_goto_allowed_substring_of_observed():
    # observed url is longer; requested is a prefix → allowed
    assert _is_goto_allowed(
        "https://en.wikipedia.org",
        ["https://en.wikipedia.org/wiki/Tokyo"],
    )


def test_is_goto_allowed_observed_is_substring_of_request():
    # observed is a prefix of request → allowed
    assert _is_goto_allowed(
        "https://en.wikipedia.org/wiki/Tokyo",
        ["https://en.wikipedia.org"],
    )


def test_is_goto_allowed_case_insensitive():
    assert _is_goto_allowed(
        "https://Arxiv.org/abs/1406.2661",
        ["https://arxiv.org/abs/1406.2661"],
    )


def test_is_goto_allowed_blocked_when_unrelated():
    assert not _is_goto_allowed(
        "https://arxiv.org/abs/1406.2661",
        ["https://en.wikipedia.org"],  # different domain entirely
    )


def test_is_goto_allowed_empty_allowlist_blocks():
    assert not _is_goto_allowed("https://x.test", [])
