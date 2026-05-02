from __future__ import annotations

import difflib
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


class GlobalTextCache:
    """Stores the most recent `document.body.innerText` for global-diff
    comparisons across turns."""

    def __init__(self) -> None:
        self._prev: str | None = None

    def previous(self) -> str | None:
        return self._prev

    def update(self, current: str) -> None:
        self._prev = current


def format_small_diff(*, previous: str, current: str, max_lines: int) -> str:
    if previous == current:
        return ""
    prev_lines = previous.splitlines(keepends=False)
    curr_lines = current.splitlines(keepends=False)
    body: list[str] = []
    for line in difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            body.append("  + " + line[1:])
        elif line.startswith("-"):
            body.append("  - " + line[1:])
        if len(body) >= max_lines:
            break
    added = sum(1 for ln in body if ln.startswith("  + "))
    removed = sum(1 for ln in body if ln.startswith("  - "))
    header = f"Page changes since last turn (+{added} / -{removed} lines):"
    return "\n".join([header, *body])


def diff_char_size(*, previous: str, current: str) -> int:
    """Sum of characters added and removed (line-level) between the two
    strings. Used as the threshold metric for context injection."""
    if previous == current:
        return 0
    prev_lines = previous.splitlines(keepends=False)
    curr_lines = current.splitlines(keepends=False)
    total = 0
    for line in difflib.unified_diff(prev_lines, curr_lines, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            total += len(line) - 1  # strip the leading +/-
    return total


def should_inject_diff(*, previous: str, current: str, threshold: int) -> bool:
    if previous == current:
        return False
    return diff_char_size(previous=previous, current=current) <= threshold
