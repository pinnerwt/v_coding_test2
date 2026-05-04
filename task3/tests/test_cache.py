import json
from pathlib import Path

from sec_toolbox.cache import DiskCache


def test_key_is_deterministic_and_args_order_insensitive(tmp_path: Path):
    cache = DiskCache(root=tmp_path)
    k1 = cache.key("submissions", {"cik": "0000320193"})
    k2 = cache.key("submissions", {"cik": "0000320193"})
    assert k1 == k2
    k3 = cache.key("submissions", {"cik": "0000789019"})
    assert k1 != k3


def test_write_then_read_hit(tmp_path: Path):
    cache = DiskCache(root=tmp_path)
    payload = b'{"hello": "world"}'
    entry = cache.write(
        endpoint="submissions",
        args={"cik": "0000320193"},
        ext="json",
        body=payload,
        status=200,
        content_type="application/json",
    )
    assert entry.path.exists()
    assert entry.path.read_bytes() == payload

    hit = cache.read("submissions", {"cik": "0000320193"})
    assert hit is not None
    assert hit.path.read_bytes() == payload


def test_read_miss_returns_none(tmp_path: Path):
    cache = DiskCache(root=tmp_path)
    assert cache.read("submissions", {"cik": "9999999999"}) is None


def test_index_is_persisted_and_reloadable(tmp_path: Path):
    cache = DiskCache(root=tmp_path)
    cache.write(
        endpoint="submissions",
        args={"cik": "0000320193"},
        ext="json",
        body=b"{}",
        status=200,
        content_type="application/json",
    )
    cache2 = DiskCache(root=tmp_path)
    assert cache2.read("submissions", {"cik": "0000320193"}) is not None


def test_index_write_is_atomic(tmp_path: Path):
    cache = DiskCache(root=tmp_path)
    cache.write(
        endpoint="submissions",
        args={"cik": "0000320193"},
        ext="json",
        body=b"{}",
        status=200,
        content_type="application/json",
    )
    index_path = tmp_path / "index.json"
    tmp_index = tmp_path / "index.json.tmp"
    assert index_path.exists()
    assert not tmp_index.exists()
    data = json.loads(index_path.read_text())
    assert len(data) == 1
