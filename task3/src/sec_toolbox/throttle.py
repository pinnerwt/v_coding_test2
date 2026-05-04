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
