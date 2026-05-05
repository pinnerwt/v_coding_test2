"""Markdown-table work queue: read next pending row, tick rows done.

The parser is header-aware: it locates the table's header row by column
names (`Done`, `CIK`, an `Accession*` cell, `Path`), records each column's
index, and uses those indices to read data rows. This means the same code
handles the original 4-column shape (`CIK | Accession | Path | Done`) and
the real 7-column shape (`# | Done | CIK | Accession (no dashes) | Period
| Filing | Path`) without a separate parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_BOX_RE = re.compile(r"\[[ x]\]")
_DIVIDER_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _split_row(line: str) -> list[str] | None:
    """Split a markdown-table row on `|`. Return stripped cells, or None
    if `line` is not a table row."""
    s = line.strip()
    if not s.startswith("|") or not s.endswith("|"):
        return None
    # Drop leading + trailing pipe before splitting so the empty edge cells
    # don't appear in the output.
    inner = s[1:-1]
    return [c.strip() for c in inner.split("|")]


def _is_divider(cells: list[str]) -> bool:
    return bool(cells) and all(_DIVIDER_CELL_RE.match(c or "") for c in cells)


@dataclass
class _Header:
    cik: int
    accession: int
    path: int
    done: int


def _find_header(lines: list[str]) -> tuple[_Header, int] | None:
    """Locate the header row. Returns (Header, index_of_header_line)."""
    for i, line in enumerate(lines):
        cells = _split_row(line)
        if cells is None:
            continue
        lowered = [c.lower() for c in cells]
        # Need a Done column, a CIK column, an Accession* column, a Path column.
        try:
            done_i = lowered.index("done")
            cik_i = lowered.index("cik")
            path_i = lowered.index("path")
        except ValueError:
            continue
        acc_i = next(
            (j for j, c in enumerate(lowered) if c.startswith("accession")),
            -1,
        )
        if acc_i < 0:
            continue
        return _Header(cik=cik_i, accession=acc_i, path=path_i, done=done_i), i
    return None


def _strip_backticks(s: str) -> str:
    if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
        return s[1:-1]
    return s


def _iter_data_rows(text: str):
    """Yield (cells, header) for every data row beneath the header."""
    lines = text.splitlines()
    found = _find_header(lines)
    if found is None:
        return
    header, hi = found
    for line in lines[hi + 1 :]:
        cells = _split_row(line)
        if cells is None:
            continue
        if _is_divider(cells):
            continue
        # Row must have enough cells to cover all four indices we care about.
        max_idx = max(header.cik, header.accession, header.path, header.done)
        if len(cells) <= max_idx:
            continue
        yield cells, header


def next_pending(path: Path) -> dict | None:
    text = Path(path).read_text()
    for cells, header in _iter_data_rows(text):
        done_cell = cells[header.done]
        if "[ ]" in done_cell:
            return {
                "cik": cells[header.cik],
                "accession": cells[header.accession],
                "path": _strip_backticks(cells[header.path]),
            }
    return None


def mark_done(path: Path, *, cik: str, accession: str) -> None:
    """Flip the matching row's `[ ]` to `[x]`. Preserve every other byte."""
    p = Path(path)
    text = p.read_text()
    lines = text.splitlines(keepends=True)

    found = _find_header([ln.rstrip("\n") for ln in lines])
    if found is None:
        return
    header, hi = found

    for idx in range(hi + 1, len(lines)):
        raw = lines[idx]
        stripped_line = raw.rstrip("\n")
        cells = _split_row(stripped_line)
        if cells is None or _is_divider(cells):
            continue
        max_idx = max(header.cik, header.accession, header.path, header.done)
        if len(cells) <= max_idx:
            continue
        if cells[header.cik] != cik or cells[header.accession] != accession:
            continue
        if "[ ]" not in cells[header.done]:
            continue
        # Locate the literal `[ ]` substring in the raw line and flip it.
        # There may legitimately be `[ ]` text elsewhere in the row, so we
        # restrict the search to the byte span of the Done cell.
        # The Done cell's span is between the `header.done`-th and
        # `(header.done+1)`-th unescaped `|` characters.
        pipe_positions = [j for j, ch in enumerate(stripped_line) if ch == "|"]
        # pipe_positions[0] is the leading `|`; cell k sits between
        # pipe_positions[k] and pipe_positions[k+1].
        if len(pipe_positions) < header.done + 2:
            continue
        cell_start = pipe_positions[header.done] + 1
        cell_end = pipe_positions[header.done + 1]
        cell_text = stripped_line[cell_start:cell_end]
        m = _BOX_RE.search(cell_text)
        if not m:
            continue
        abs_start = cell_start + m.start()
        abs_end = cell_start + m.end()
        new_line = stripped_line[:abs_start] + "[x]" + stripped_line[abs_end:]
        lines[idx] = new_line + ("\n" if raw.endswith("\n") else "")
        break

    p.write_text("".join(lines))
