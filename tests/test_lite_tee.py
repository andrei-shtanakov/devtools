"""Дублирование вывода `dfl` (lite/tee.py).

Под pytest `sys.stdout` подменён захватом и пишет мимо fd 1, поэтому каждый
сценарий исполняется в отдельном процессе Python с настоящими дескрипторами.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _scenario(tmp_path: Path, body: str) -> tuple[subprocess.CompletedProcess, list]:
    out = tmp_path / "tail.json"
    head = [
        "import json, subprocess, sys",
        "from lite.tee import tee_output",
        "tail = None",
        "try:",
        "    with tee_output(max_lines=200) as tail:",
    ]
    lines = textwrap.dedent(body).strip("\n").splitlines()
    foot = [
        "finally:",
        f"    open({str(out)!r}, 'w').write(json.dumps(tail.lines()))",
    ]
    script = "\n".join([*head, *("        " + ln for ln in lines), *foot]) + "\n"
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.exists(), done.stderr
    return done, json.loads(out.read_text())


def test_tee_keeps_screen_and_collects_python_and_child(tmp_path: Path) -> None:
    done, tail = _scenario(
        tmp_path,
        """
        print("py out")
        print("py err", file=sys.stderr)
        subprocess.run(["sh", "-c", "echo child out; echo child err >&2"])
        """,
    )
    assert done.returncode == 0, done.stderr
    assert "py out" in done.stdout and "child out" in done.stdout
    assert "py err" in done.stderr and "child err" in done.stderr
    assert {"py out", "py err", "child out", "child err"} <= set(tail)


def test_tee_keeps_last_n_lines(tmp_path: Path) -> None:
    done, tail = _scenario(
        tmp_path,
        """
        for i in range(250):
            print(f"line {i}")
        """,
    )
    assert len(tail) == 200
    assert tail[0] == "line 50" and tail[-1] == "line 249"
    assert "line 0\n" in done.stdout


def test_tee_restores_fds_on_exception(tmp_path: Path) -> None:
    done, tail = _scenario(
        tmp_path,
        """
        print("before")
        raise RuntimeError("boom")
        """,
    )
    assert done.returncode == 1
    assert "before" in done.stdout
    assert "RuntimeError: boom" in done.stderr  # traceback уже после восстановления
    assert tail == ["before"]


def test_tee_keeps_partial_last_line(tmp_path: Path) -> None:
    done, tail = _scenario(tmp_path, 'sys.stdout.write("no newline")\n')
    assert done.stdout == "no newline"
    assert tail == ["no newline"]
