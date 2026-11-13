"""Находки проверок на стопе прогона (lite/stop_findings.py), devtools#757."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import run_state as rs
from lite import failure_log as fl
from lite import stop_findings as sf

MAJOR = (
    "error EDGE-CHECK(requirements/requirements-vs-charter): R1 major: Цель G-03 "
    "не покрыта @ 10-requirements.md:[410, 439]"
)
MINOR = (
    "error EDGE-CHECK(requirements/requirements-vs-charter): R4 minor: критерий "
    "FR-05 субъективен @ 10-requirements.md:[136, 136]"
)
NO_PLACE = (
    "error EDGE-CHECK(charter/charter-vs-brief): FAIL по статусу пунктов без находок"
)


@pytest.fixture
def run(tmp_path: Path, monkeypatch) -> rs.RunState:
    monkeypatch.setattr(rs, "run_dir", lambda rid: tmp_path / "runs" / rid)
    (tmp_path / "runs" / "R-1").mkdir(parents=True)
    state = rs.RunState.__new__(rs.RunState)
    state.run_id, state.status = "R-1", "stopped_review"
    state.bundle_dir, state.target_dir = "workstreams/w/spec", "/t"
    state.branch = "spec/w-behaviour-w2"
    return state


def _write(name: str, *rows: str) -> None:
    (rs.run_dir("R-1") / name).write_text("\n".join(rows) + "\n", "utf-8")


def test_summary_counts_levels(run) -> None:
    _write("edge-findings.txt", MAJOR, MINOR, MINOR)
    found = sf.lines("R-1", "stopped_review")
    assert sf.summary(found, "stopped_review") == (
        "смысловая проверка узлов нашла замечаний: 3 (major 1, minor 2)"
    )


def test_readable_and_files(run) -> None:
    assert sf.readable(MAJOR) == (
        "R1 major (10-requirements.md:[410, 439]): Цель G-03 не покрыта"
    )
    assert sf.readable(NO_PLACE) == "FAIL по статусу пунктов без находок"
    assert sf.files([MAJOR, MINOR, NO_PLACE]) == ["10-requirements.md"]


def test_edit_hint_names_bundle_file_target_and_branch(run) -> None:
    hint = sf.edit_hint(run, [MAJOR])
    assert "workstreams/w/spec/10-requirements.md в /t" in hint
    assert "ветка spec/w-behaviour-w2" in hint and "коммитить не нужно" in hint


def test_file_read_only_for_its_status(run) -> None:
    _write("gate-findings.txt", "warning GC-X пустяк")
    assert sf.lines("R-1", "stopped_review") == []
    assert sf.lines("R-1", "completed") == []
    assert sf.lines("R-1", "stopped_gate") == ["warning GC-X пустяк"]


def test_cause_is_summary_not_exit_code(run) -> None:
    rs.run_dir("R-1").joinpath("run.json").write_text(
        '{"status": "stopped_review"}', "utf-8"
    )
    _write("edge-findings.txt", MAJOR)
    tail = ["шаг завершился с кодом 1", "дальше: разберите причину"]
    assert fl.cause_from(None, ["R-1"], tail).startswith(
        "смысловая проверка узлов нашла замечаний: 1"
    )


def test_cause_ignores_findings_after_stop_lifted(run) -> None:
    rs.run_dir("R-1").joinpath("run.json").write_text('{"status": "running"}', "utf-8")
    _write("edge-findings.txt", MAJOR)
    assert fl.cause_from(None, ["R-1"], ["x"]) == "x"
