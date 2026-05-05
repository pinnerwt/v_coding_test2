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
