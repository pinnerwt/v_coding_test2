from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        out = dict(event)
        out.setdefault("ts", datetime.now(UTC).isoformat())
        with self._path.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


def read_trace(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
