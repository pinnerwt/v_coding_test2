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
