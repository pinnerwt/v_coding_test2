from pathlib import Path

import httpx

from sec_toolbox.cli import main


class _NoopThrottle:
    def acquire(self, n: float = 1.0) -> None:
        pass


def _patch_client(monkeypatch, handler):
    from sec_toolbox import cli

    def _build(_args):
        from sec_toolbox.client import SECClient

        return SECClient(transport=httpx.MockTransport(handler), throttle=_NoopThrottle())

    monkeypatch.setattr(cli, "_build_client", _build)


def test_submissions_prints_json_to_stdout(tmp_path, capsys, monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json={"name": "Apple"}, headers={"content-type": "application/json"}
        )

    _patch_client(monkeypatch, handler)
    rc = main(["--data-dir", str(tmp_path), "submissions", "--cik", "320193"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Apple" in out


def test_archive_writes_file_and_prints_path(tmp_path, capsys, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})

    _patch_client(monkeypatch, handler)
    rc = main(
        [
            "--data-dir",
            str(tmp_path),
            "archive",
            "--cik",
            "320193",
            "--accession",
            "0000320193-23-000106",
            "--filename",
            "a.htm",
        ]
    )
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).exists()
    assert Path(out).read_bytes() == b"<html/>"


def test_unknown_subcommand_exits_nonzero(monkeypatch, capsys):
    rc = main(["nope"])
    assert rc != 0


def test_missing_required_arg_exits_nonzero(monkeypatch, capsys):
    rc = main(["submissions"])  # missing --cik
    assert rc != 0
