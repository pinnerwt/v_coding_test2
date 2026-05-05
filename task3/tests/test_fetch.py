from pathlib import Path

import httpx

from sec_toolbox.cache import DiskCache
from sec_toolbox.client import SECClient
from sec_toolbox.fetch import Fetcher


class _NoopThrottle:
    def acquire(self, n: float = 1.0) -> None:
        pass


def make_fetcher(tmp_path: Path, handler) -> tuple[Fetcher, dict[str, int]]:
    counter = {"calls": 0}

    def counted(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return handler(request)

    client = SECClient(transport=httpx.MockTransport(counted), throttle=_NoopThrottle())
    cache = DiskCache(root=tmp_path)
    return Fetcher(client=client, cache=cache), counter


def test_second_call_reads_from_cache(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    fetcher, counter = make_fetcher(tmp_path, handler)
    fetcher.submissions(cik="320193")
    fetcher.submissions(cik="320193")
    assert counter["calls"] == 1


def test_refresh_forces_refetch(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    fetcher, counter = make_fetcher(tmp_path, handler)
    fetcher.submissions(cik="320193")
    fetcher.submissions(cik="320193", refresh=True)
    assert counter["calls"] == 2


def test_no_cache_skips_read_but_still_writes(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    fetcher, counter = make_fetcher(tmp_path, handler)
    fetcher.submissions(cik="320193", no_cache=True)
    fetcher.submissions(cik="320193", no_cache=True)
    assert counter["calls"] == 2
    # but the cache file is still there
    assert any(p.suffix == ".json" for p in (tmp_path / "raw" / "submissions").iterdir())


def test_archive_uses_derived_relative_path(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})

    fetcher, _ = make_fetcher(tmp_path, handler)
    entry = fetcher.archive(cik="320193", accession="0000320193-23-000106", filename="a.htm")
    assert entry.path == tmp_path / "raw" / "archive" / "320193" / "000032019323000106" / "a.htm"
    assert entry.path.read_bytes() == b"<html/>"
