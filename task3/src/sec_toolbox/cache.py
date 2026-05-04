from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
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
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
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
