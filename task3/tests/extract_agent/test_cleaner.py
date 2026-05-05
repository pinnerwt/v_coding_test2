from extract_agent.cleaner import clean_html


def test_strips_script_and_style():
    raw = "<html><script>x=1</script><p>Hi</p><style>p{}</style></html>"
    assert "x=1" not in clean_html(raw)
    assert "p{}" not in clean_html(raw)
    assert "Hi" in clean_html(raw)


def test_strips_inline_xbrl_hidden_and_header():
    raw = "<ix:hidden>SECRET</ix:hidden><p>Visible</p><ix:header>HDR</ix:header>"
    out = clean_html(raw)
    assert "SECRET" not in out
    assert "HDR" not in out
    assert "Visible" in out


def test_inserts_newlines_at_block_tags():
    raw = "<p>A</p><p>B</p><div>C</div>"
    out = clean_html(raw)
    assert "A\nB" in out or "A\n\nB" in out
    assert "C" in out


def test_decodes_entities_and_normalises_nbsp():
    raw = "<p>A&nbsp;B&amp;C</p>"
    out = clean_html(raw)
    assert "A B&C" in out
    assert "\xa0" not in out


def test_collapses_horizontal_whitespace():
    raw = "<p>A   B</p>"
    assert "A B" in clean_html(raw)


def test_collapses_three_plus_newlines_to_two():
    raw = "<p>A</p><p></p><p></p><p></p><p>B</p>"
    out = clean_html(raw)
    assert "\n\n\n" not in out


def test_extra_strip_patterns_applied():
    raw = "<p>Apple Inc. | 2023 Form 10-K | 17</p><p>Body</p>"
    out = clean_html(
        raw,
        extra_strip_patterns=[r"\nApple Inc\.\s*\|\s*2023 Form 10-K\s*\|\s*\d+\s*\n"],
    )
    assert "Form 10-K" not in out
    assert "Body" in out
