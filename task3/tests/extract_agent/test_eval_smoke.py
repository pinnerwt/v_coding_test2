import subprocess


def test_eval_help():
    r = subprocess.run(
        ["uv", "run", "python", "eval/regression.py", "--help"],
        cwd="/home/pgi/v_coding_test2/task3",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
