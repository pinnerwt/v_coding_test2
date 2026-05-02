from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_MAX_BYTES = 2048


def _normalise(url: str, query_strip: bool) -> str:
    if not query_strip:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class NotesStore:
    def __init__(self, path: Path | str, *, query_strip: bool = True):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._query_strip = query_strip
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS url_notes ("
            "  url TEXT PRIMARY KEY,"
            "  notes TEXT NOT NULL,"
            "  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._conn.commit()

    def get(self, url: str) -> str:
        key = _normalise(url, self._query_strip)
        row = self._conn.execute("SELECT notes FROM url_notes WHERE url = ?", (key,)).fetchone()
        return row[0] if row else ""

    def append(self, url: str, line: str) -> None:
        key = _normalise(url, self._query_strip)
        existing = self.get(url)
        merged = (existing + "\n" + line).strip("\n") if existing else line
        while len(merged.encode()) > _MAX_BYTES and "\n" in merged:
            merged = merged.split("\n", 1)[1]
        self._conn.execute(
            "INSERT INTO url_notes(url, notes, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(url) DO UPDATE SET notes=excluded.notes, updated_at=CURRENT_TIMESTAMP",
            (key, merged),
        )
        self._conn.commit()
