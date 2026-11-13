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
NO_FINDINGS = (
    "error EDGE-CHECK(charter/charter-vs-brief): FAIL по статусу пунктов без находок"
)
ERROR_EDGE = (
    "error EDGE-CHECK(requirements/requirements-vs-charter): reviewer_failed: "
    "ревьюер упал"
)
ERROR_LEVEL = "error EDGE-CHECK(no_edges): профиль не объявляет узлов уровня 3"


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


def _edge() -> sf.Stop:
    stop = sf.load("R-1", sf.EDGE)
    assert stop is not None
    return stop


def test_summary_counts_findings_by_level(run) -> None:
    _write("edge-findings.txt", MAJOR, MINOR, MINOR)
    assert sf.summary(_edge()) == (
        "смысловая проверка узлов нашла замечаний: 3 (major 1, minor 2)"
    )


def test_multiline_statement_is_one_finding(run) -> None:
    _write("edge-findings.txt", MAJOR, "вторая строка текста находки", MINOR)
    stop = _edge()
    assert len(stop.findings) == 2
    assert stop.findings[0].endswith("[410, 439] вторая строка текста находки")


def test_failures_not_counted_and_no_edit_hint(run) -> None:
    """Отказ edge-check — не замечание к узлу: подсказки «поправьте» нет."""
    _write("edge-findings.txt", ERROR_LEVEL)
    stop = _edge()
    assert stop.findings == [] and stop.failures == [ERROR_LEVEL]
    assert sf.summary(stop) == (
        "смысловая проверка узлов не состоялась: профиль не объявляет узлов уровня 3"
    )
    assert sf.edit_hint(run, stop) is None


def test_mixed_failures_shown_but_not_counted(run) -> None:
    _write("edge-findings.txt", MAJOR, ERROR_EDGE, NO_FINDINGS)
    stop = _edge()
    assert sf.summary(stop).endswith("замечаний: 1 (major 1)")
    texts = [t for t, _ in sf.shown(stop)]
    assert "  - отказ проверки: reviewer_failed: ревьюер упал" in texts
    assert sf.edit_hint(run, stop) is not None


def test_readable_and_files() -> None:
    assert sf.readable(MAJOR) == (
        "R1 major (10-requirements.md:[410, 439]): Цель G-03 не покрыта"
    )
    assert sf.files([MAJOR, MINOR]) == ["10-requirements.md"]


def test_edit_hint_names_bundle_file_target_and_branch(run) -> None:
    _write("edge-findings.txt", MAJOR)
    hint = sf.edit_hint(run, _edge())
    assert hint is not None
    assert "workstreams/w/spec/10-requirements.md в /t" in hint
    assert "ветка spec/w-behaviour-w2" in hint and "коммитить не нужно" in hint


def test_gate_output_raw_not_counted_level_one(run) -> None:
    _write("gate-findings.txt", "GC-01 error [FR-01] пусто", "not_evaluated: GC-X")
    stop = sf.load("R-1", sf.GATE)
    assert stop is not None
    assert sf.summary(stop).startswith("проверка бандла не прошла (вывод: ")
    assert {level for _, level in sf.shown(stop)} == {1}
    assert sf.edit_hint(run, stop) is not None


def test_file_read_only_for_its_status(run) -> None:
    _write("gate-findings.txt", "warning GC-X пустяк")
    assert sf.load("R-1", sf.EDGE) is None
    assert sf.load("R-1", "completed") is None


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
