from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01\n", encoding="utf-8")
    return b


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "edge_check.py"), *args],
        capture_output=True, text=True, cwd=REPO,
    )


def test_bad_basis_syntax_is_exit_2(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "behaviour-vs-requirements", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", "requirements-without-equals"])
    assert got.returncode == 2
    assert "role=path" in got.stderr


def test_missing_mandatory_input_is_exit_3_and_prints_record(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "behaviour-vs-requirements", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", f"requirements={b / 'nope.md'}"])
    assert got.returncode == 3
    record = json.loads(got.stdout)
    assert record["verdict"] == "ERROR"
    assert record["error_code"] == "missing_mandatory_input"


def test_unknown_edge_is_exit_2(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "no-such-edge", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", f"requirements={b / '10-requirements.md'}"])
    assert got.returncode == 2
    assert "no-such-edge" in got.stderr


def test_make_edge_check_target_runs_under_uv(tmp_path: Path) -> None:
    """C1: операторская цель обязана работать без глобально поставленного
    pyyaml — как соседние цели, через `uv run --frozen`, а не голый python3.

    `uv run pytest` кладёт `.venv/bin` в начало PATH — там `python3` уже
    знает про pyyaml, и баг под ним не воспроизводится. Убираем `.venv/bin`
    из PATH подпроцесса, оставляя `uv` доступным, чтобы гонять именно ту
    команду, которую наберёт оператор без активированного venv."""
    b = _bundle(tmp_path)
    env = dict(os.environ)
    venv_bin = str(REPO / ".venv" / "bin")
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if p != venv_bin
    )
    env.pop("VIRTUAL_ENV", None)
    got = subprocess.run(
        ["make", "edge-check",
         "ARGS=--edge no-such-edge --bundle " + str(b)
         + " --subject " + str(b / "15-behaviour-spec.md")
         + " --basis requirements=" + str(b / "10-requirements.md")],
        capture_output=True, text=True, cwd=REPO, env=env,
    )
    assert got.returncode == 2, got.stderr
    assert "ModuleNotFoundError" not in got.stderr


def test_out_write_failure_does_not_lose_the_finished_record(tmp_path: Path) -> None:
    """I6: сбой записи `--out` не должен прятать уже посчитанный результат."""
    b = _bundle(tmp_path)
    bad_out = tmp_path / "no-such-dir" / "record.json"
    got = _run(["--edge", "behaviour-vs-requirements", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", f"requirements={b / 'nope.md'}",
                "--out", str(bad_out)])
    assert got.returncode == 2
    record = json.loads(got.stdout)
    assert record["verdict"] == "ERROR"
    assert not bad_out.exists()


def test_unexpected_exception_is_exit_3_not_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2: последний рубеж main() — сломанный прибор не должен выдавать себя
    за код 1 (FAIL по словарю `_EXIT`)."""
    import edge_check

    def boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("сюрприз")

    monkeypatch.setattr(edge_check, "run_check", boom)
    b = _bundle(tmp_path)
    got = edge_check.main(
        ["--edge", "behaviour-vs-requirements", "--bundle", str(b),
         "--subject", str(b / "15-behaviour-spec.md"),
         "--basis", f"requirements={b / '10-requirements.md'}"]
    )
    assert got == 3
