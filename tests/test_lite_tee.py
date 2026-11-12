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


def test_tee_keeps_screen_and_collects_python_output(tmp_path: Path) -> None:
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
    assert {"py out", "py err"} <= set(tail)
    # дочерние процессы пишут в настоящие fd напрямую — вне хвоста (ревью I1–I3)
    assert "child out" not in tail


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


def test_tee_does_not_hang_on_broken_screen(tmp_path: Path) -> None:
    # stdout закрыт читателем (`dfl … | head`): вывод не виснет (ревью I1)
    out = tmp_path / "tail.json"
    script = (
        "import json, sys\n"
        "from lite.tee import tee_output\n"
        "with tee_output() as tail:\n"
        "    try:\n"
        "        for i in range(200000):\n"
        "            print('x' * 50)\n"
        "    except BrokenPipeError:\n"
        "        pass\n"
        f"open({str(out)!r}, 'w').write(json.dumps(len(tail.lines())))\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    proc.stdout.read(10)
    proc.stdout.close()
    assert proc.wait(timeout=20) is not None


def test_tee_wrapper_delegates_stream_attributes(tmp_path: Path) -> None:
    done, _tail = _scenario(
        tmp_path,
        """
        assert sys.stdout.fileno() == 1 and sys.stderr.fileno() == 2
        assert isinstance(sys.stdout.isatty(), bool)
        sys.stdout.buffer.write(b"raw bytes\\n")
        print(sys.stdout.encoding)
        """,
    )
    assert done.returncode == 0, done.stderr
    assert "raw bytes" in done.stdout


def test_dfl_cli_record_has_full_tail(tmp_path: Path) -> None:
    # настоящая точка входа: отказ (конфига нет) — хвост записи не пуст
    state, repo = tmp_path / "state", tmp_path / "repo"
    repo.mkdir()
    env = {
        **__import__("os").environ,
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
    }
    done = subprocess.run(
        [sys.executable, "-m", "lite.dfl", "--cwd", str(repo), "status"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2, done.stderr
    (record,) = state.glob("darkfactory/failures/*/last.json")
    tail = json.loads(record.read_text())["output_tail"]
    assert "dfl status: отказ (код 2)" in tail
    assert tail[-1] == "отчёт разработчикам: dfl report"
