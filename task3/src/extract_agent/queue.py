"""Markdown-table work queue: read next pending row, tick rows done."""

from __future__ import annotations

import re
from pathlib import Path

_ROW_RE = re.compile(
    r"^\|\s*(?P<cik>\S+)\s*\|\s*(?P<accession>\S+)\s*\|\s*"
    r"(?P<path>.+?)\s*\|\s*\[(?P<box>[ x])\]\s*\|\s*$"
)


def _iter_rows(text: str):
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        if m.group("cik").lower() == "cik":
            continue
        yield m


def next_pending(path: Path) -> dict | None:
    text = Path(path).read_text()
    for m in _iter_rows(text):
        if m.group("box") == " ":
            return {
                "cik": m.group("cik"),
                "accession": m.group("accession"),
                "path": m.group("path"),
            }
    return None


def mark_done(path: Path, *, cik: str, accession: str) -> None:
    p = Path(path)
    text = p.read_text()
    out_lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = _ROW_RE.match(stripped)
        if (
            m
            and m.group("cik") == cik
            and m.group("accession") == accession
            and m.group("box") == " "
        ):
            new = stripped[: m.start("box") - 1] + "[x]" + stripped[m.end("box") + 1 :]
            line = new + ("\n" if line.endswith("\n") else "")
        out_lines.append(line)
    p.write_text("".join(out_lines))
