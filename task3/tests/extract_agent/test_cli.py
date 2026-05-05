import subprocess

import pytest

from extract_agent import __main__ as cli_main
from extract_agent.state import SessionState


def test_cli_help():
    r = subprocess.run(
        ["uv", "run", "python", "-m", "extract_agent", "--help"],
        cwd="/home/pgi/v_coding_test2/task3",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "--cik" in r.stdout
    assert "--queue" in r.stdout
    assert "--out" in r.stdout


def _write_queue(path, rows):
    lines = [
        "| cik | accession | path | done |",
        "| --- | --- | --- | --- |",
    ]
    for cik, acc, p in rows:
        lines.append(f"| {cik} | {acc} | {p} | [ ] |")
    path.write_text("\n".join(lines) + "\n")


def test_queue_halts_and_exits_nonzero_on_cost_exceeded(tmp_path, monkeypatch):
    qpath = tmp_path / "queue.md"
    _write_queue(qpath, [("320193", "0000320193-23-000106", "some/path.html")])
    out_dir = tmp_path / "out"

    call_count = {"n": 0}

    async def fake_run_loop(**kwargs):
        call_count["n"] += 1
        return {"status": "cost_exceeded", "state": SessionState(), "messages": []}

    monkeypatch.setattr("extract_agent.loop.run_loop", fake_run_loop)
    monkeypatch.setattr(
        "extract_agent.__main__._resolve_html_path",
        lambda cik, acc: tmp_path / "fake.html",
    )

    rc = cli_main.main(
        [
            "--queue",
            str(qpath),
            "--out-dir",
            str(out_dir),
            "--all",
        ]
    )
    assert rc == 1
    assert call_count["n"] == 1, "loop should not be re-invoked after non-done status"
    # Row remains pending (not marked done).
    assert "[ ]" in qpath.read_text()
    assert "[x]" not in qpath.read_text()


def test_queue_halts_nonzero_on_unknown_non_done_status(tmp_path, monkeypatch):
    """Non-done, non-cost/step statuses must also yield exit 1, not 0."""
    qpath = tmp_path / "queue.md"
    _write_queue(qpath, [("320193", "0000320193-23-000106", "some/path.html")])
    out_dir = tmp_path / "out"

    async def fake_run_loop(**kwargs):
        return {"status": "stalled", "state": SessionState(), "messages": []}

    monkeypatch.setattr("extract_agent.loop.run_loop", fake_run_loop)
    monkeypatch.setattr(
        "extract_agent.__main__._resolve_html_path",
        lambda cik, acc: tmp_path / "fake.html",
    )

    rc = cli_main.main(
        ["--queue", str(qpath), "--out-dir", str(out_dir), "--all"]
    )
    assert rc == 1
    assert "[x]" not in qpath.read_text()


def test_cik_without_accession_errors_pair_check(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main.main(["--cik", "320193", "--out", str(tmp_path / "x.json")])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "must be given together" in err
