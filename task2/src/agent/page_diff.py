from __future__ import annotations


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
