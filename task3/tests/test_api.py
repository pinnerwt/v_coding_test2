"""Tests for the FastAPI extraction service.

The service stitches `sec_toolbox.fetch.Fetcher` (network/cache) to
`extract_agent.run_loop` (LLM agent). Both are heavy dependencies; the
tests stub them out and only exercise the wiring + request validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sec_toolbox import api as api_mod


@pytest.fixture
def sample_records() -> list[dict]:
    return [
        {
            "part": "I",
            "item_number": "1",
            "item_title": "Business",
            "content_text": "We make widgets.",
            "char_range": [0, 16],
            "status": "extracted",
        }
    ]


@pytest.fixture
def stub_app(monkeypatch, tmp_path: Path, sample_records: list[dict]):
    """A TestClient with fetch + agent stubbed to be deterministic."""

    out_dir = tmp_path / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(api_mod, "DATA_ROOT", tmp_path)

    class _FakeEntry:
        path = tmp_path / "fake.htm"

    class _FakeFetcher:
        def submissions(self, *, cik: str):
            sub_path = tmp_path / "sub.json"
            sub_path.write_text(
                json.dumps(
                    {
                        "filings": {
                            "recent": {
                                "accessionNumber": ["0000320193-23-000106"],
                                "primaryDocument": ["aapl-20230930.htm"],
                            }
                        }
                    }
                )
            )

            class _E:
                path = sub_path

            return _E()

        def archive(self, *, cik: str, accession: str, filename: str):
            (tmp_path / "fake.htm").write_text("<html></html>")
            return _FakeEntry()

    monkeypatch.setattr(api_mod, "_build_fetcher", lambda: _FakeFetcher())

    async def _fake_agent(html_path: Path, out_path: Path):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(sample_records))

        class _State:
            cost_usd = 0.0123
            steps = 7

        return {"status": "done", "state": _State()}

    monkeypatch.setattr(api_mod, "_run_agent", _fake_agent)

    return TestClient(api_mod.app)


def test_health(stub_app: TestClient) -> None:
    r = stub_app.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_extract_by_cik_and_accession(stub_app: TestClient, sample_records) -> None:
    r = stub_app.post(
        "/extract",
        json={"cik": "320193", "accession": "0000320193-23-000106"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cik"] == "320193"
    assert body["accession"] == "0000320193-23-000106"
    assert body["items"] == sample_records
    assert body["stats"]["status"] == "done"
    assert body["stats"]["steps"] == 7


def test_extract_by_url(stub_app: TestClient, sample_records) -> None:
    r = stub_app.post(
        "/extract",
        json={
            "url": (
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019323000106/aapl-20230930.htm"
            )
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cik"] == "320193"
    assert body["filename"] == "aapl-20230930.htm"
    assert body["items"] == sample_records


def test_extract_rejects_neither(stub_app: TestClient) -> None:
    r = stub_app.post("/extract", json={})
    assert r.status_code == 422


def test_extract_rejects_both(stub_app: TestClient) -> None:
    r = stub_app.post(
        "/extract",
        json={
            "cik": "320193",
            "accession": "0000320193-23-000106",
            "url": (
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019323000106/aapl-20230930.htm"
            ),
        },
    )
    assert r.status_code == 422


def test_extract_rejects_lone_cik(stub_app: TestClient) -> None:
    r = stub_app.post("/extract", json={"cik": "320193"})
    assert r.status_code == 422


def test_extract_rejects_unknown_url(stub_app: TestClient) -> None:
    r = stub_app.post("/extract", json={"url": "https://example.com/foo.htm"})
    assert r.status_code == 400


def test_extract_returns_500_on_agent_failure(monkeypatch, stub_app: TestClient) -> None:
    async def _fail_agent(html_path: Path, out_path: Path):
        class _State:
            cost_usd = 0.5
            steps = 30

        return {"status": "max_steps_exceeded", "state": _State()}

    monkeypatch.setattr(api_mod, "_run_agent", _fail_agent)
    r = stub_app.post(
        "/extract",
        json={"cik": "320193", "accession": "0000320193-23-000106"},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["status"] == "max_steps_exceeded"
