from pathlib import Path

from extract_agent.queue import mark_done, next_pending

QUEUE = """\
# Test queue
| CIK | Accession | Path | Done |
|---|---|---|---|
| 320193 | 000032019323000106 | data/raw/archive/320193/.../aapl.htm | [x] |
| 1067983 | 000119312526083899 | data/raw/archive/1067983/.../brk.htm | [ ] |
| 789019 | 000156459020034944 | data/raw/archive/789019/.../msft.htm | [ ] |
"""


def test_next_pending_returns_first_unchecked(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE)
    row = next_pending(p)
    assert row["cik"] == "1067983"
    assert row["accession"] == "000119312526083899"
    assert row["path"].endswith("brk.htm")


def test_mark_done_flips_box(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE)
    mark_done(p, cik="1067983", accession="000119312526083899")
    text = p.read_text()
    assert text.count("[x]") == 2
    assert text.count("[ ]") == 1


def test_next_pending_returns_none_when_all_done(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE.replace("[ ]", "[x]"))
    assert next_pending(p) is None


QUEUE_7COL = "\n".join(
    [
        "# 10-K extraction source checklist",
        "",
        "| # | Done | CIK | Accession (no dashes) | Period | Filing | Path |",
        "|---|------|-----|-----------------------|--------|--------|------|",
        "| 1 | [x] | 40545 | 000004054519000014 | 2018-12 | GE 2018 | `a/ge.htm` |",
        "| 2 | [ ] | 1834584 | 000183458424000023 | 2023-12 | Coupang | `a/cpng.htm` |",
        "| 3 | [ ] | 1874178 | 000187417826000008 | 2025-12 | Rivian | `a/rivn.htm` |",
        "",
    ]
)


def test_next_pending_seven_column_format(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE_7COL)
    row = next_pending(p)
    assert row["cik"] == "1834584"
    assert row["accession"] == "000183458424000023"
    # path is wrapped in backticks in source — parser strips them
    assert not row["path"].startswith("`")
    assert not row["path"].endswith("`")
    assert row["path"].endswith("cpng.htm")


def test_mark_done_seven_column_preserves_other_cells(tmp_path: Path):
    p = tmp_path / "queue.md"
    p.write_text(QUEUE_7COL)
    mark_done(p, cik="1834584", accession="000183458424000023")
    text = p.read_text()
    assert text.count("[x]") == 2
    assert text.count("[ ]") == 1
    # Period and Filing cells must still be intact for the flipped row.
    assert "2023-12" in text
    assert "Coupang" in text
    # And next_pending now skips that row.
    nxt = next_pending(p)
    assert nxt["cik"] == "1874178"
