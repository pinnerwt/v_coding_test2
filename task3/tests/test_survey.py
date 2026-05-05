from sec_toolbox.survey import (
    SLATE,
    pick_filing,
    sniff_kind,
)


def test_slate_has_15_filings_in_5_categories():
    assert len(SLATE) == 15
    cats = {f.category for f in SLATE}
    assert cats == {"A", "B", "C", "D", "E"}
    for c in cats:
        assert sum(1 for f in SLATE if f.category == c) == 3


def test_pick_filing_picks_most_recent_when_target_recent():
    submissions_json = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000-23-002", "0000-22-001", "0000-24-003"],
                "filingDate": ["2023-11-01", "2022-11-01", "2024-11-01"],
                "form": ["10-K", "10-K", "10-K"],
                "primaryDocument": ["c.htm", "b.htm", "a.htm"],
            }
        }
    }
    pick = pick_filing(submissions_json, target="recent")
    assert pick["accession"] == "0000-24-003"
    assert pick["primary_doc"] == "a.htm"


def test_pick_filing_picks_closest_year_when_target_year():
    submissions_json = {
        "filings": {
            "recent": {
                "accessionNumber": ["a", "b", "c"],
                "filingDate": ["1995-03-01", "2004-03-01", "2010-03-01"],
                "form": ["10-K", "10-K", "10-K"],
                "primaryDocument": ["a.txt", "b.htm", "c.htm"],
            }
        }
    }
    pick = pick_filing(submissions_json, target=2004)
    assert pick["accession"] == "b"
    pick = pick_filing(submissions_json, target=1995)
    assert pick["accession"] == "a"


def test_sniff_kind_inline_xbrl():
    body = b'<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">...</html>'
    assert sniff_kind(body, "text/html") == "inline_xbrl"


def test_sniff_kind_html():
    assert sniff_kind(b"<HTML><body>hi</body></HTML>", "text/html") == "html"


def test_sniff_kind_plain_text():
    assert sniff_kind(b"<SEC-DOCUMENT>...plain text...", "text/plain") == "plain_text"
