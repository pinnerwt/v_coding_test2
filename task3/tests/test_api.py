"""POST /extract integration tests for the FastAPI surface."""

import pathlib

import pytest
from fastapi.testclient import TestClient

from sec_toolbox.api import build_app

FIXTURES = pathlib.Path(__file__).parent.parent / "data/raw/archive"
APPLE = FIXTURES / "320193/000032019323000106/aapl-20230930.htm"


@pytest.mark.skipif(not APPLE.exists(), reason="Apple fixture not cached")
def test_extract_endpoint_returns_items(monkeypatch):
    apple_html = APPLE.read_bytes()
    monkeypatch.setattr(
        "sec_toolbox.api._fetch_filing_bytes",
        lambda cik, accession, filename: (apple_html, "aapl-20230930.htm"),
    )
    monkeypatch.setattr(
        "sec_toolbox.status._llm_read",
        lambda t: "reserved" if "Reserved" in t else "substantive",
    )
    monkeypatch.setattr("sec_toolbox.status._READ_CACHE", {})

    client = TestClient(build_app())
    resp = client.post(
        "/extract",
        json={
            "cik": "320193",
            "accession": "0000320193-23-000106",
            "filename": "aapl-20230930.htm",
            "fiscal_year": 2023,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    nums = [i["item_number"] for i in data["items"]]
    assert "1A" in nums
    assert "1" in nums
    # Verification block surfaces schedule check.
    assert "verification" in data


def test_extract_endpoint_validates_required_fields():
    client = TestClient(build_app())
    resp = client.post("/extract", json={"cik": "320193"})
    assert resp.status_code == 422
