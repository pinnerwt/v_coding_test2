# Task 3 SEC Toolbox Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `task3/` — a `uv` project with a `SECClient`, disk cache, four-endpoint toolbox CLI, and a 15-filing survey script that produces a committed reconnaissance report.

**Architecture:** A single `httpx.Client` wrapped by a token-bucket throttle (10 rps) and a SHA1-keyed disk cache, exposed through endpoint helper functions and an `argparse` subcommand CLI. Tests use `httpx.MockTransport` and a fake clock so nothing in CI talks to the SEC. The survey is a thin script over the same toolbox.

**Tech Stack:** Python 3.11+, `uv`, `httpx`, `pytest`, `ruff`, `argparse` (stdlib).

**Reference:** Approved design at `docs/plans/2026-05-04-task3-toolbox-design.md`.

---

## Conventions for the executor

- **CWD for every command in this plan is `task3/`** unless explicitly stated otherwise. Don't `cd` mid-step; execute from `task3/`.
- **Every Python invocation goes through `uv run`.** No direct `python`, no `pip`.
- **TDD red-first.** For every behavior task: write the failing test → run it → see the expected failure → implement minimally → re-run → green. Do not write implementation before the test fails for the right reason.
- **Commit cadence:** one commit per task at the end. Conventional prefixes: `feat(task3):`, `test(task3):`, `chore(task3):`, `docs(task3):`.
- **No `--no-verify`.** If a hook fails, fix the root cause.
- **Ruff must be green before committing each task.** Run `uv run ruff check . && uv run ruff format --check .` and fix anything it flags.

---

## Task 1: Bootstrap the `task3/` uv project

**Files:**
- Create: `task3/pyproject.toml`
- Create: `task3/.gitignore`
- Create: `task3/README.md` (skeleton; full content later)
- Create: `task3/src/sec_toolbox/__init__.py`
- Create: `task3/tests/__init__.py`
- Create: `task3/data/raw/.gitkeep`
- Create: `task3/data/survey/.gitkeep`

**Step 1: Initialize the uv project**

From repo root:

```bash
cd task3 && uv init --package --name sec_toolbox --no-readme --no-pin-python
```

Expected: creates `pyproject.toml`, `src/sec_toolbox/__init__.py`, `.python-version` may or may not appear. If `uv init` produces a `hello.py` or `main.py`, delete it.

**Step 2: Replace `task3/pyproject.toml` with the canonical version**

```toml
[project]
name = "sec_toolbox"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.28.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sec_toolbox"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[dependency-groups]
dev = [
    "pytest>=9.0.3",
    "ruff>=0.15.12",
]
```

**Step 3: Sync dependencies**

```bash
uv sync
```

Expected: writes `task3/uv.lock`, creates `task3/.venv/`. No errors.

**Step 4: Write `task3/.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
data/raw/
data/index.json
```

Note: `data/survey/` is **not** ignored — survey outputs are committed.

**Step 5: Write a skeleton `task3/README.md`**

```markdown
# Task 3 — SEC 10-K Item-level Structured Extraction

This directory holds Task 3 of the AI Coding Test. Bootstrap stage: a fetch toolbox + survey of how the SEC actually serves 10-Ks. Parser comes in a follow-up.

## Run

```bash
cd task3
uv sync
uv run python -m sec_toolbox --help
```

## Survey

```bash
uv run python -m sec_toolbox survey
```

Outputs: `data/survey/report.md`, `data/survey/report.csv`.
```

**Step 6: Create empty `__init__.py` files and `.gitkeep` placeholders**

```bash
mkdir -p tests src/sec_toolbox data/raw data/survey
: > tests/__init__.py
: > data/raw/.gitkeep
: > data/survey/.gitkeep
```

`src/sec_toolbox/__init__.py` from `uv init` may already exist; ensure it's present (empty is fine).

**Step 7: Smoke-test pytest discovery**

```bash
uv run pytest -q
```

Expected: `no tests ran in 0.0Xs` exit 0 (or exit 5 — "no tests collected"; either is acceptable as long as pytest itself runs).

**Step 8: Run ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: `All checks passed!` and format clean.

**Step 9: Commit**

From **repo root**:

```bash
git add task3/
git commit -m "chore(task3): bootstrap uv project skeleton"
```

---

## Task 2: Token-bucket rate limiter

**Files:**
- Create: `task3/src/sec_toolbox/throttle.py`
- Create: `task3/tests/test_throttle.py`

**Step 1: Write the failing tests**

```python
# task3/tests/test_throttle.py
from sec_toolbox.throttle import TokenBucket


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_bucket_allows_capacity_immediately():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    assert clock.sleeps == []


def test_bucket_blocks_when_empty_then_refills():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    bucket.acquire()
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= 0.099  # ~1/rate seconds


def test_bucket_refills_continuously_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    clock.t += 0.5  # half a second of real time → 5 tokens back
    for _ in range(5):
        bucket.acquire()
    assert clock.sleeps == []  # didn't have to sleep
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_throttle.py -v
```

Expected: `ModuleNotFoundError: sec_toolbox.throttle` or `ImportError`.

**Step 3: Implement `TokenBucket`**

```python
# task3/src/sec_toolbox/throttle.py
from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate: float,
        capacity: float,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time_fn()
        self._time = time_fn
        self._sleep = sleep_fn

    def _refill(self) -> None:
        now = self._time()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def acquire(self, n: float = 1.0) -> None:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return
        deficit = n - self._tokens
        wait = deficit / self.rate
        self._sleep(wait)
        self._refill()
        self._tokens -= n
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_throttle.py -v
```

Expected: 3 passed.

**Step 5: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 6: Commit**

From repo root:

```bash
git add task3/src/sec_toolbox/throttle.py task3/tests/test_throttle.py
git commit -m "feat(task3): token-bucket rate limiter for SEC client"
```

---

## Task 3: Disk cache with atomic index

**Files:**
- Create: `task3/src/sec_toolbox/cache.py`
- Create: `task3/tests/test_cache.py`

**Step 1: Write the failing tests**

```python
# task3/tests/test_cache.py
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
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cache.py -v
```

Expected: import error.

**Step 3: Implement `DiskCache`**

```python
# task3/src/sec_toolbox/cache.py
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    key: str
    endpoint: str
    args: dict[str, Any]
    path: Path
    ext: str
    status: int
    content_type: str
    bytes: int
    fetched_at: str
    sha256: str


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._index: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text())

    def _save(self) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, indent=2, sort_keys=True))
        os.replace(tmp, self.index_path)

    def key(self, endpoint: str, args: dict[str, Any]) -> str:
        normalized = json.dumps(args, sort_keys=True, separators=(",", ":"))
        raw = f"{endpoint}|{normalized}".encode()
        return hashlib.sha1(raw).hexdigest()

    def read(self, endpoint: str, args: dict[str, Any]) -> CacheEntry | None:
        k = self.key(endpoint, args)
        meta = self._index.get(k)
        if not meta:
            return None
        path = self.root / meta["path_relative"]
        if not path.exists():
            return None
        return CacheEntry(
            key=k,
            endpoint=meta["endpoint"],
            args=meta["args"],
            path=path,
            ext=meta["ext"],
            status=meta["status"],
            content_type=meta["content_type"],
            bytes=meta["bytes"],
            fetched_at=meta["fetched_at"],
            sha256=meta["sha256"],
        )

    def write(
        self,
        *,
        endpoint: str,
        args: dict[str, Any],
        ext: str,
        body: bytes,
        status: int,
        content_type: str,
        relative_path: str | None = None,
    ) -> CacheEntry:
        k = self.key(endpoint, args)
        if relative_path is None:
            rel = Path("raw") / endpoint / f"{k}.{ext}"
        else:
            rel = Path(relative_path)
        abs_path = self.root / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(body)

        sha = hashlib.sha256(body).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        meta = {
            "endpoint": endpoint,
            "args": args,
            "path_relative": str(rel),
            "ext": ext,
            "status": status,
            "content_type": content_type,
            "bytes": len(body),
            "fetched_at": fetched_at,
            "sha256": sha,
        }
        self._index[k] = meta
        self._save()
        return CacheEntry(
            key=k,
            endpoint=endpoint,
            args=args,
            path=abs_path,
            ext=ext,
            status=status,
            content_type=content_type,
            bytes=len(body),
            fetched_at=fetched_at,
            sha256=sha,
        )
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cache.py -v
```

Expected: 5 passed.

**Step 5: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 6: Commit**

```bash
git add task3/src/sec_toolbox/cache.py task3/tests/test_cache.py
git commit -m "feat(task3): disk cache with atomic JSON index"
```

---

## Task 4: `SECClient` with httpx + UA + throttle

**Files:**
- Create: `task3/src/sec_toolbox/client.py`
- Create: `task3/tests/test_client.py`

**Step 1: Write the failing tests**

```python
# task3/tests/test_client.py
import httpx
import pytest

from sec_toolbox.client import DEFAULT_USER_AGENT, SECClient
from sec_toolbox.throttle import TokenBucket


def make_client(handler, throttle: TokenBucket | None = None, user_agent: str | None = None):
    transport = httpx.MockTransport(handler)
    return SECClient(
        transport=transport,
        throttle=throttle,
        user_agent=user_agent,
    )


def test_default_user_agent_used_when_unset(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("https://data.sec.gov/foo")
    assert seen == [DEFAULT_USER_AGENT]


def test_env_var_user_agent_overrides_default(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "custom-ua test@example.com")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("https://data.sec.gov/foo")
    assert seen == ["custom-ua test@example.com"]


def test_explicit_user_agent_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "from-env")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, user_agent="explicit-ua")
    client.get("https://data.sec.gov/foo")
    assert seen == ["explicit-ua"]


def test_throttle_acquire_called_per_request():
    calls: list[float] = []

    class Counting:
        def acquire(self, n: float = 1.0) -> None:
            calls.append(n)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, throttle=Counting())
    client.get("https://data.sec.gov/a")
    client.get("https://data.sec.gov/b")
    assert calls == [1.0, 1.0]


def test_retries_once_on_429():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"err": "rate"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    class FakeThrottle:
        def acquire(self, n: float = 1.0) -> None:
            pass

    client = SECClient(
        transport=httpx.MockTransport(handler),
        throttle=FakeThrottle(),
        sleep_fn=sleeps.append,
    )
    resp = client.get("https://data.sec.gov/foo")
    assert resp.status_code == 200
    assert attempts["n"] == 2
    assert sleeps == [1.0]


def test_raises_on_persistent_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    class FakeThrottle:
        def acquire(self, n: float = 1.0) -> None:
            pass

    client = SECClient(
        transport=httpx.MockTransport(handler),
        throttle=FakeThrottle(),
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://data.sec.gov/foo")
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_client.py -v
```

Expected: import error.

**Step 3: Implement `SECClient`**

```python
# task3/src/sec_toolbox/client.py
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from sec_toolbox.throttle import TokenBucket

DEFAULT_USER_AGENT = "v_coding_test2 task3 squareznft@gmail.com"


class _Throttle(Protocol):
    def acquire(self, n: float = 1.0) -> None: ...


class SECClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        throttle: _Throttle | None = None,
        user_agent: str | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self._user_agent = user_agent or os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT
        self._throttle = throttle if throttle is not None else TokenBucket(rate=10, capacity=10)
        self._sleep = sleep_fn
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._request("GET", url, params=params)

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None) -> httpx.Response:
        for attempt in (1, 2):
            self._throttle.acquire()
            resp = self._client.request(method, url, params=params)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt == 1:
                    self._sleep(1.0)
                    continue
                resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SECClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_client.py -v
```

Expected: 6 passed.

**Step 5: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 6: Commit**

```bash
git add task3/src/sec_toolbox/client.py task3/tests/test_client.py
git commit -m "feat(task3): SECClient with UA, throttle, single 429/5xx retry"
```

---

## Task 5: Endpoint helpers

**Files:**
- Create: `task3/src/sec_toolbox/endpoints.py`
- Create: `task3/tests/test_endpoints.py`

**Scope:** Four functions, all taking `client: SECClient`, all returning `(bytes, content_type, ext, derived_relative_path | None)`. Cache integration is in the next task — endpoints just talk to the network.

**Step 1: Write the failing tests**

```python
# task3/tests/test_endpoints.py
import httpx

from sec_toolbox.client import SECClient
from sec_toolbox.endpoints import (
    archive,
    full_text_search,
    submissions,
    xbrl_company_facts,
)
from sec_toolbox.throttle import TokenBucket


class _NoopThrottle:
    def acquire(self, n: float = 1.0) -> None:
        pass


def make_client(handler) -> SECClient:
    return SECClient(transport=httpx.MockTransport(handler), throttle=_NoopThrottle())


def test_submissions_pads_cik_and_hits_correct_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"name": "Apple"}, headers={"content-type": "application/json"})

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
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_endpoints.py -v
```

**Step 3: Implement `endpoints.py`**

```python
# task3/src/sec_toolbox/endpoints.py
from __future__ import annotations

import os
import re
from typing import Any

from sec_toolbox.client import SECClient


def _pad_cik(cik: str | int) -> str:
    s = str(cik).strip().lstrip("0") or "0"
    return s.zfill(10)


def _strip_dashes(accession: str) -> str:
    return accession.replace("-", "")


def _ext_from_filename(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return ext.lower() if dot else ""


def submissions(client: SECClient, *, cik: str | int) -> tuple[bytes, str, str, str | None]:
    url = f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json"
    resp = client.get(url)
    return resp.content, resp.headers.get("content-type", ""), "json", None


def full_text_search(
    client: SECClient,
    *,
    q: str,
    forms: str | None = None,
    ciks: str | None = None,
    date_range: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[bytes, str, str, str | None]:
    params: dict[str, Any] = {"q": q}
    if forms is not None:
        params["forms"] = forms
    if ciks is not None:
        params["ciks"] = ciks
    if date_range is not None:
        params["dateRange"] = date_range
    if extra:
        params.update(extra)
    resp = client.get("https://efts.sec.gov/LATEST/search-index", params=params)
    return resp.content, resp.headers.get("content-type", ""), "json", None


def xbrl_company_facts(client: SECClient, *, cik: str | int) -> tuple[bytes, str, str, str | None]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_pad_cik(cik)}.json"
    resp = client.get(url)
    return resp.content, resp.headers.get("content-type", ""), "json", None


_CIK_NUMERIC = re.compile(r"^\d+$")


def archive(
    client: SECClient,
    *,
    cik: str | int,
    accession: str,
    filename: str,
) -> tuple[bytes, str, str, str | None]:
    cik_str = str(cik).lstrip("0") or "0"
    if not _CIK_NUMERIC.match(cik_str):
        raise ValueError(f"non-numeric CIK: {cik!r}")
    acc = _strip_dashes(accession)
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc}/{filename}"
    )
    resp = client.get(url)
    ext = _ext_from_filename(filename)
    relative = os.path.join("raw", "archive", cik_str, acc, filename)
    return resp.content, resp.headers.get("content-type", ""), ext, relative
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_endpoints.py -v
```

**Step 5: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 6: Commit**

```bash
git add task3/src/sec_toolbox/endpoints.py task3/tests/test_endpoints.py
git commit -m "feat(task3): endpoint helpers for submissions/search/xbrl/archive"
```

---

## Task 6: Cache-aware fetcher

**Files:**
- Create: `task3/src/sec_toolbox/fetch.py`
- Create: `task3/tests/test_fetch.py`

**Scope:** A `Fetcher` that wires `SECClient` + `DiskCache` + endpoint functions, honoring `--no-cache` (skip read) and `--refresh` (force re-fetch).

**Step 1: Write the failing tests**

```python
# task3/tests/test_fetch.py
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
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_fetch.py -v
```

**Step 3: Implement `Fetcher`**

```python
# task3/src/sec_toolbox/fetch.py
from __future__ import annotations

from typing import Any

from sec_toolbox import endpoints
from sec_toolbox.cache import CacheEntry, DiskCache
from sec_toolbox.client import SECClient


class Fetcher:
    def __init__(self, *, client: SECClient, cache: DiskCache) -> None:
        self.client = client
        self.cache = cache

    def _go(
        self,
        endpoint: str,
        args: dict[str, Any],
        fetch_callable,
        *,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        if not refresh and not no_cache:
            hit = self.cache.read(endpoint, args)
            if hit is not None:
                return hit
        body, content_type, ext, relative = fetch_callable()
        return self.cache.write(
            endpoint=endpoint,
            args=args,
            ext=ext,
            body=body,
            status=200,
            content_type=content_type,
            relative_path=relative,
        )

    def submissions(self, *, cik: str | int, no_cache: bool = False, refresh: bool = False) -> CacheEntry:
        args = {"cik": str(cik)}
        return self._go(
            "submissions",
            args,
            lambda: endpoints.submissions(self.client, cik=cik),
            no_cache=no_cache,
            refresh=refresh,
        )

    def search(
        self,
        *,
        q: str,
        forms: str | None = None,
        ciks: str | None = None,
        date_range: str | None = None,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        args = {"q": q, "forms": forms, "ciks": ciks, "dateRange": date_range}
        return self._go(
            "search",
            args,
            lambda: endpoints.full_text_search(
                self.client, q=q, forms=forms, ciks=ciks, date_range=date_range
            ),
            no_cache=no_cache,
            refresh=refresh,
        )

    def xbrl(self, *, cik: str | int, no_cache: bool = False, refresh: bool = False) -> CacheEntry:
        args = {"cik": str(cik)}
        return self._go(
            "xbrl",
            args,
            lambda: endpoints.xbrl_company_facts(self.client, cik=cik),
            no_cache=no_cache,
            refresh=refresh,
        )

    def archive(
        self,
        *,
        cik: str | int,
        accession: str,
        filename: str,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        args = {"cik": str(cik), "accession": accession, "filename": filename}
        return self._go(
            "archive",
            args,
            lambda: endpoints.archive(
                self.client, cik=cik, accession=accession, filename=filename
            ),
            no_cache=no_cache,
            refresh=refresh,
        )
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_fetch.py -v
```

**Step 5: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 6: Commit**

```bash
git add task3/src/sec_toolbox/fetch.py task3/tests/test_fetch.py
git commit -m "feat(task3): cache-aware Fetcher honoring --refresh and --no-cache"
```

---

## Task 7: CLI subcommands

**Files:**
- Create: `task3/src/sec_toolbox/cli.py`
- Create: `task3/src/sec_toolbox/__main__.py`
- Create: `task3/tests/test_cli.py`

**Step 1: Write the failing tests**

```python
# task3/tests/test_cli.py
from pathlib import Path

import httpx

from sec_toolbox.cli import main


class _NoopThrottle:
    def acquire(self, n: float = 1.0) -> None:
        pass


def _patch_client(monkeypatch, handler):
    from sec_toolbox import cli

    def _build(_args):
        from sec_toolbox.client import SECClient

        return SECClient(transport=httpx.MockTransport(handler), throttle=_NoopThrottle())

    monkeypatch.setattr(cli, "_build_client", _build)


def test_submissions_prints_json_to_stdout(tmp_path, capsys, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"name": "Apple"}, headers={"content-type": "application/json"})

    _patch_client(monkeypatch, handler)
    rc = main(["--data-dir", str(tmp_path), "submissions", "--cik", "320193"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Apple" in out


def test_archive_writes_file_and_prints_path(tmp_path, capsys, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})

    _patch_client(monkeypatch, handler)
    rc = main(
        [
            "--data-dir",
            str(tmp_path),
            "archive",
            "--cik",
            "320193",
            "--accession",
            "0000320193-23-000106",
            "--filename",
            "a.htm",
        ]
    )
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).exists()
    assert Path(out).read_bytes() == b"<html/>"


def test_unknown_subcommand_exits_nonzero(monkeypatch, capsys):
    rc = main(["nope"])
    assert rc != 0


def test_missing_required_arg_exits_nonzero(monkeypatch, capsys):
    rc = main(["submissions"])  # missing --cik
    assert rc != 0
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py -v
```

**Step 3: Implement `cli.py` and `__main__.py`**

```python
# task3/src/sec_toolbox/cli.py
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sec_toolbox.cache import DiskCache
from sec_toolbox.client import SECClient
from sec_toolbox.fetch import Fetcher


def _build_client(args: argparse.Namespace) -> SECClient:
    return SECClient()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sec_toolbox")
    p.add_argument("--data-dir", default="data", help="cache root (default: data/)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("-o", "--output", default=None, help="override output path (archive only)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submissions")
    s.add_argument("--cik", required=True)

    s = sub.add_parser("search")
    s.add_argument("--q", required=True)
    s.add_argument("--forms", default=None)
    s.add_argument("--ciks", default=None)
    s.add_argument("--dateRange", dest="date_range", default=None)

    s = sub.add_parser("xbrl")
    s.add_argument("--cik", required=True)

    s = sub.add_parser("archive")
    s.add_argument("--cik", required=True)
    s.add_argument("--accession", required=True)
    s.add_argument("--filename", required=True)

    s = sub.add_parser("survey")
    s.add_argument("--out", default="data/survey")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2

    cache = DiskCache(root=Path(args.data_dir))
    client = _build_client(args)
    fetcher = Fetcher(client=client, cache=cache)

    try:
        if args.cmd == "submissions":
            entry = fetcher.submissions(cik=args.cik, no_cache=args.no_cache, refresh=args.refresh)
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "search":
            entry = fetcher.search(
                q=args.q,
                forms=args.forms,
                ciks=args.ciks,
                date_range=args.date_range,
                no_cache=args.no_cache,
                refresh=args.refresh,
            )
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "xbrl":
            entry = fetcher.xbrl(cik=args.cik, no_cache=args.no_cache, refresh=args.refresh)
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "archive":
            entry = fetcher.archive(
                cik=args.cik,
                accession=args.accession,
                filename=args.filename,
                no_cache=args.no_cache,
                refresh=args.refresh,
            )
            if args.output:
                Path(args.output).write_bytes(entry.path.read_bytes())
                print(args.output)
            else:
                print(str(entry.path))
        elif args.cmd == "survey":
            from sec_toolbox.survey import run_survey

            run_survey(fetcher=fetcher, out_dir=Path(args.out))
        else:
            parser.error(f"unknown subcommand {args.cmd!r}")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# task3/src/sec_toolbox/__main__.py
from sec_toolbox.cli import main

raise SystemExit(main())
```

**Step 4: Add a stub for survey to make import not crash**

Create `task3/src/sec_toolbox/survey.py` with a placeholder so the CLI test that doesn't exercise survey still imports cleanly:

```python
# task3/src/sec_toolbox/survey.py
from __future__ import annotations

from pathlib import Path

from sec_toolbox.fetch import Fetcher


def run_survey(*, fetcher: Fetcher, out_dir: Path) -> None:
    raise NotImplementedError("Implemented in task 8")
```

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: 4 passed. (`test_unknown_subcommand_exits_nonzero` and `test_missing_required_arg_exits_nonzero` rely on argparse exiting non-zero on parse error — `parser.parse_args` raises `SystemExit(2)`, our handler catches and returns int.)

**Step 6: Run the full suite**

```bash
uv run pytest -v
```

Expected: all green.

**Step 7: Smoke-test the CLI**

```bash
uv run python -m sec_toolbox --help
```

Expected: argparse help printed, exit 0.

**Step 8: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 9: Commit**

```bash
git add task3/src/sec_toolbox/cli.py task3/src/sec_toolbox/__main__.py task3/src/sec_toolbox/survey.py task3/tests/test_cli.py
git commit -m "feat(task3): argparse CLI wiring submissions/search/xbrl/archive"
```

---

## Task 8: Survey script

**Files:**
- Modify: `task3/src/sec_toolbox/survey.py` (replace stub with real implementation)
- Create: `task3/tests/test_survey.py` (unit-test the slate-resolution + sniffer; live run is separate)
- Create: `task3/scripts/run_survey.sh`

**Step 1: Write failing tests for the pure logic**

```python
# task3/tests/test_survey.py
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
    body = b"<html xmlns:ix=\"http://www.xbrl.org/2013/inlineXBRL\">...</html>"
    assert sniff_kind(body, "text/html") == "inline_xbrl"


def test_sniff_kind_html():
    assert sniff_kind(b"<HTML><body>hi</body></HTML>", "text/html") == "html"


def test_sniff_kind_plain_text():
    assert sniff_kind(b"<SEC-DOCUMENT>...plain text...", "text/plain") == "plain_text"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_survey.py -v
```

**Step 3: Implement `survey.py`**

```python
# task3/src/sec_toolbox/survey.py
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sec_toolbox.fetch import Fetcher


@dataclass(frozen=True)
class SlateEntry:
    category: str
    name: str
    cik: str
    target: int | str  # year or "recent"


SLATE: list[SlateEntry] = [
    # A — modern inline-XBRL
    SlateEntry("A", "Apple", "320193", 2023),
    SlateEntry("A", "Microsoft", "789019", 2023),
    SlateEntry("A", "NVIDIA", "1045810", 2024),
    # B — heavy IBR
    SlateEntry("B", "Berkshire Hathaway", "1067983", "recent"),
    SlateEntry("B", "JPMorgan Chase", "19617", "recent"),
    SlateEntry("B", "ExxonMobil", "34088", "recent"),
    # C — older HTML, pre-XBRL
    SlateEntry("C", "IBM", "51143", 2004),
    SlateEntry("C", "General Electric", "40545", 2004),
    SlateEntry("C", "Coca-Cola", "21344", 2004),
    # D — small-cap / recent IPO
    SlateEntry("D", "Palantir", "1321655", "recent"),
    SlateEntry("D", "Reddit", "1834584", 2024),
    SlateEntry("D", "Rivian", "1874178", "recent"),
    # E — older plain-text
    SlateEntry("E", "IBM", "51143", 1995),
    SlateEntry("E", "General Electric", "40545", 1995),
    SlateEntry("E", "Microsoft", "789019", 1995),
]


def pick_filing(submissions_json: dict[str, Any], target: int | str) -> dict[str, str] | None:
    recent = submissions_json.get("filings", {}).get("recent", {})
    accs = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    forms = recent.get("form") or []
    docs = recent.get("primaryDocument") or []
    rows = [
        {"accession": a, "date": d, "form": f, "primary_doc": p}
        for a, d, f, p in zip(accs, dates, forms, docs, strict=False)
        if f == "10-K"
    ]
    if not rows:
        return None
    if target == "recent":
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows[0]
    target_year = int(target)
    rows.sort(key=lambda r: abs(int(r["date"][:4]) - target_year))
    return rows[0]


def sniff_kind(body: bytes, content_type: str) -> str:
    head = body[:4096].lower()
    if b"inlinexbrl" in head or b"xmlns:ix" in head:
        return "inline_xbrl"
    if content_type.startswith("text/plain") or head.startswith(b"<sec-document>"):
        return "plain_text"
    if b"<html" in head or content_type.startswith("text/html"):
        return "html"
    return "unknown"


def run_survey(*, fetcher: Fetcher, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "report.csv"
    md_path = out_dir / "report.md"

    rows: list[dict[str, Any]] = []
    for s in SLATE:
        try:
            sub_entry = fetcher.submissions(cik=s.cik)
            sub_json = json.loads(sub_entry.path.read_text())
            pick = pick_filing(sub_json, s.target)
            if pick is None:
                rows.append(
                    {
                        "category": s.category,
                        "name": s.name,
                        "cik": s.cik,
                        "accession": "",
                        "filing_date": "",
                        "primary_doc": "",
                        "content_type": "",
                        "ext": "",
                        "bytes": 0,
                        "sniffed_kind": "no-10k-found",
                    }
                )
                continue
            arc = fetcher.archive(
                cik=s.cik,
                accession=pick["accession"],
                filename=pick["primary_doc"],
            )
            body = arc.path.read_bytes()
            rows.append(
                {
                    "category": s.category,
                    "name": s.name,
                    "cik": s.cik,
                    "accession": pick["accession"],
                    "filing_date": pick["date"],
                    "primary_doc": pick["primary_doc"],
                    "content_type": arc.content_type,
                    "ext": arc.ext,
                    "bytes": arc.bytes,
                    "sniffed_kind": sniff_kind(body, arc.content_type),
                }
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                {
                    "category": s.category,
                    "name": s.name,
                    "cik": s.cik,
                    "accession": "",
                    "filing_date": "",
                    "primary_doc": "",
                    "content_type": "",
                    "ext": "",
                    "bytes": 0,
                    "sniffed_kind": f"error: {type(e).__name__}: {e}",
                }
            )

    fieldnames = [
        "category",
        "name",
        "cik",
        "accession",
        "filing_date",
        "primary_doc",
        "content_type",
        "ext",
        "bytes",
        "sniffed_kind",
    ]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    md_lines = ["# Task 3 — SEC 10-K survey", ""]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    cat_titles = {
        "A": "A. Modern inline-XBRL",
        "B": "B. Heavy 'incorporated by reference'",
        "C": "C. Older HTML, pre-XBRL",
        "D": "D. Small-cap / recent IPO",
        "E": "E. Older plain-text",
    }
    for cat in sorted(by_cat):
        md_lines.append(f"## {cat_titles.get(cat, cat)}")
        md_lines.append("")
        md_lines.append("| Filer | CIK | Accession | Filing date | Primary doc | Content-Type | Ext | Bytes | Sniffed |")
        md_lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in by_cat[cat]:
            md_lines.append(
                f"| {r['name']} | {r['cik']} | {r['accession']} | {r['filing_date']} | "
                f"{r['primary_doc']} | {r['content_type']} | {r['ext']} | {r['bytes']} | {r['sniffed_kind']} |"
            )
        md_lines.append("")
    md_path.write_text("\n".join(md_lines))
```

**Step 4: Write `scripts/run_survey.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m sec_toolbox survey "$@"
```

```bash
chmod +x scripts/run_survey.sh
```

**Step 5: Run unit tests**

```bash
uv run pytest tests/test_survey.py -v
```

Expected: 5 passed.

**Step 6: Run full suite**

```bash
uv run pytest -v
```

Expected: all green.

**Step 7: Ruff**

```bash
uv run ruff check . && uv run ruff format .
```

**Step 8: Commit (without running survey yet)**

```bash
git add task3/src/sec_toolbox/survey.py task3/tests/test_survey.py task3/scripts/run_survey.sh
git commit -m "feat(task3): survey script over 15-filing slate"
```

---

## Task 9: Run the live survey and commit the report

**Step 1: Set the User-Agent**

```bash
export SEC_USER_AGENT="v_coding_test2 task3 squareznft@gmail.com"
```

(Already the default, but exporting makes it explicit and lets us change it without code edits.)

**Step 2: Run the survey**

From `task3/`:

```bash
uv run python -m sec_toolbox survey
```

Expected: prints nothing of note; writes `data/survey/report.md` and `data/survey/report.csv`. Network calls will be visible in `data/index.json` and `data/raw/`.

If filings 404 (older filers' "primary document" is sometimes a `.txt` index that no longer matches): the survey records the error in `sniffed_kind` and continues. Don't fix individual cases here; that's a finding to surface in the README.

**Step 3: Inspect the outputs**

```bash
cat data/survey/report.md
head data/survey/report.csv
```

Eyeball: every row has `category`, every category has 3 rows, `sniffed_kind` populated for at least the modern-era filings. Older filings may show `error:` rows — that is acceptable evidence and gets called out in the README in the next task.

**Step 4: Stage and commit only the survey output**

```bash
git status
```

`data/survey/report.md` and `data/survey/report.csv` should be the only new tracked files (raw/index are gitignored).

```bash
git add task3/data/survey/report.md task3/data/survey/report.csv
git commit -m "chore(task3): commit survey report from live run"
```

---

## Task 10: Flesh out README and final ruff/pytest sweep

**Files:**
- Modify: `task3/README.md`

**Step 1: Replace the README skeleton with the real version**

Cover:
- Purpose (link to design doc).
- How to run: `uv sync`, `uv run python -m sec_toolbox --help`, each subcommand with one example.
- Survey: how to re-run, where outputs land, what's committed and what's gitignored.
- Findings (3-6 bullets) summarizing what the survey actually shows: format eras, common failure modes (404 on old filings, primary doc that's actually a `.txt` index, etc.). Cite specific rows from the CSV — no hand-waving.
- Where AI helped: brainstorming → design doc → plan → implementation, all under git history.

**Step 2: Final sweep**

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -v
```

Both green.

**Step 3: Commit**

```bash
git add task3/README.md
git commit -m "docs(task3): README with run instructions and survey findings"
```

---

## Done criteria

- `task3/` is a working `uv` project; `uv run pytest` is green, `uv run ruff check .` is clean.
- `uv run python -m sec_toolbox --help` and each subcommand work from a fresh `uv sync`.
- `task3/data/survey/report.md` and `report.csv` exist in git.
- README explains run + findings; design doc is referenced.
- Cache (`task3/data/raw/`, `task3/data/index.json`) is gitignored; survey output is not.

## Out of scope (next plan)

- 10-K parsing into Items.
- Cross-validation against XBRL Company Facts.
- Zeabur deploy.
- Public API surface.
