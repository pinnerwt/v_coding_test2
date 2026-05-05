import httpx

from sec_toolbox.client import SECClient
from sec_toolbox.endpoints import (
    archive,
    full_text_search,
    submissions,
    xbrl_company_facts,
)


class _NoopThrottle:
    def acquire(self, n: float = 1.0) -> None:
        pass


def make_client(handler) -> SECClient:
    return SECClient(transport=httpx.MockTransport(handler), throttle=_NoopThrottle())


def test_submissions_pads_cik_and_hits_correct_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, json={"name": "Apple"}, headers={"content-type": "application/json"}
        )

    client = make_client(handler)
    body, ctype, ext, _ = submissions(client, cik="320193")
    assert seen == ["https://data.sec.gov/submissions/CIK0000320193.json"]
    assert b"Apple" in body
    assert ctype.startswith("application/json")
    assert ext == "json"


def test_full_text_search_passes_query_and_form_params():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"hits": []}, headers={"content-type": "application/json"})

    client = make_client(handler)
    full_text_search(client, q="climate risk", forms="10-K")
    assert seen[0].host == "efts.sec.gov"
    assert seen[0].path == "/LATEST/search-index"
    qs = dict(seen[0].params.multi_items())
    assert qs["q"] == "climate risk"
    assert qs["forms"] == "10-K"


def test_xbrl_company_facts_pads_cik():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"facts": {}}, headers={"content-type": "application/json"})

    client = make_client(handler)
    xbrl_company_facts(client, cik="789019")
    assert seen == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"]


def test_archive_normalizes_accession_and_uses_correct_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            content=b"<html>ok</html>",
            headers={"content-type": "text/html"},
        )

    client = make_client(handler)
    body, ctype, ext, rel = archive(
        client,
        cik="320193",
        accession="0000320193-23-000106",
        filename="aapl-20230930.htm",
    )
    assert seen == [
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    ]
    assert body == b"<html>ok</html>"
    assert ext == "htm"
    assert rel == "raw/archive/320193/000032019323000106/aapl-20230930.htm"


def test_archive_accepts_no_dash_accession():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})

    client = make_client(handler)
    archive(client, cik="320193", accession="000032019323000106", filename="a.htm")
    assert "/000032019323000106/" in seen[0]
