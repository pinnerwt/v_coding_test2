from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRUNCATE_CHARS_DEFAULT = 12000


def truncate_for_log(value: Any, *, max_chars: int = TRUNCATE_CHARS_DEFAULT) -> Any:
    """Truncate a string when it exceeds `max_chars`. Returns the original
    value unchanged if it is not a string or fits under the cap. For
    over-cap strings, returns `{"truncated": <prefix>, "original_chars": N}`
    so an analyzer can still report the true payload size."""
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return {"truncated": value[:max_chars], "original_chars": len(value)}


class LLMTraceWriter:
    """Append-only JSONL writer for per-session LLM call records.
    Mirrors `agent.trace.TraceWriter` deliberately."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        out = dict(event)
        out.setdefault("ts", datetime.now(UTC).isoformat())
        with self._path.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
