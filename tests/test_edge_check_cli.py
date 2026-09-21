from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
