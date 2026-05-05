import subprocess


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
