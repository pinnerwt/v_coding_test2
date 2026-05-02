from __future__ import annotations

from dataclasses import dataclass


class OffsetCache:
    """Per-offset memo of the last string served to the LLM at each offset.

    Used to detect when a `read(offset=N)` call would produce content the
    agent has already seen at that offset, so the loop can auto-advance.
    """

    def __init__(self) -> None:
        self._served: dict[int, str] = {}

    def record(self, *, offset: int, served: str) -> None:
        self._served[offset] = served

    def was_served(self, *, offset: int, candidate: str) -> bool:
        return self._served.get(offset) == candidate

    def clear(self) -> None:
        self._served.clear()


@dataclass(frozen=True)
class ReadPlan:
    served_offset: int
    served_text: str
    advanced_from: int | None  # original offset if advanced, else None
    exhausted: bool  # True iff we walked past the page or hit max hops


def plan_read(
    *,
    text: str,
    requested_offset: int,
    cache: OffsetCache,
    read_limit: int,
    max_hops: int,
) -> ReadPlan:
    """Decide what to serve for a read(offset) call, honoring the cache.

    Walks forward from `requested_offset` in `read_limit` strides while the
    candidate window has already been served at that offset. Stops when
    either (a) the window differs from what was served at that offset, or
    (b) we have walked past `len(text)`, or (c) we hit `max_hops`.
    """
    n = len(text)
    offset = requested_offset
    hops = 0
    while True:
        if offset >= n:
            return ReadPlan(
                served_offset=offset,
                served_text="",
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=True,
            )
        candidate = text[offset : offset + read_limit]
        if not cache.was_served(offset=offset, candidate=candidate):
            return ReadPlan(
                served_offset=offset,
                served_text=candidate,
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=False,
            )
        # cache hit: advance
        hops += 1
        if hops >= max_hops:
            return ReadPlan(
                served_offset=offset,
                served_text="",
                advanced_from=requested_offset if offset != requested_offset else None,
                exhausted=True,
            )
        offset += read_limit
