"""Находки проверок на стопе прогона (lite/stop_findings.py), devtools#757."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
        "смысловая проверка узлов: блокирующих замечаний 1, рекомендаций 2"
    )
    texts = [t for t, _ in sf.shown(_edge())]
    assert texts.index(sf.ADVISORY_HEAD) == 1  # major — до заголовка рекомендаций


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
    assert sf.summary(stop) == sf.FAILED_CHECK
    assert sf.shown(stop) == [(f"  - {sf.readable(ERROR_LEVEL)}", 1)]
    assert sf.edit_hint(run, stop) is None


def test_failure_with_minor_is_failed_check(run) -> None:
    """Отказ ребра рядом с minor — сбой проверки, не «пункт не оценён»."""
    _write("edge-findings.txt", MINOR, ERROR_EDGE)
    assert sf.summary(_edge()) == sf.FAILED_CHECK


def test_mixed_failures_shown_but_not_counted(run) -> None:
    _write("edge-findings.txt", MAJOR, ERROR_EDGE, NO_FINDINGS)
    stop = _edge()
    assert sf.summary(stop).endswith("блокирующих замечаний 1")
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


def test_edit_hint_edge_keeps_paid_repeat_wording(run) -> None:
    _write("edge-findings.txt", MAJOR)
    hint = sf.edit_hint(run, _edge())
    assert hint is not None
    assert "повтор — новый платный вызов проверки" in hint


def test_edit_hint_gate_does_not_call_repeat_paid(run) -> None:
    _write("gate-findings.txt", "error GC-ORPHAN [BEH-01] сирота")
    stop = sf.load("R-1", sf.GATE)
    assert stop is not None
    hint = sf.edit_hint(run, stop)
    assert hint is not None
    assert "новый платный вызов" not in hint
    assert "после гейта снова пойдёт платная смысловая проверка" in hint


def test_gate_output_without_errors_stays_level_one(run) -> None:
    _write("gate-findings.txt", "GC-01 warning [FR-01] пусто", "not_evaluated: GC-X")
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
        "смысловая проверка узлов: блокирующих замечаний 1"
    )


def test_cause_ignores_findings_after_stop_lifted(run) -> None:
    rs.run_dir("R-1").joinpath("run.json").write_text('{"status": "running"}', "utf-8")
    _write("edge-findings.txt", MAJOR)
    assert fl.cause_from(None, ["R-1"], ["x"]) == "x"


def test_blocking_matches_edge_rules_contract() -> None:
    """`BLOCKING` совпадает с `severity.blocking` каждого ребра контракта."""
    root = Path(__file__).resolve().parents[1] / "contracts/edge-check/v1/rules"
    paths = sorted(root.glob("*.yaml"))
    assert paths, f"правил рёбер нет в {root}"
    for path in paths:
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(rule["severity"]["blocking"]) == sf.BLOCKING, path.name


def test_stop_without_blocking_is_not_advisory(run) -> None:
    """FAIL по статусу пункта при одних minor: не «можно оставить» и не
    «поправьте файл» (ревью #759)."""
    _write("edge-findings.txt", MINOR)
    stop = _edge()
    assert sf.summary(stop) == sf.UNBLOCKED_STOP
    texts = [t for t, _ in sf.shown(stop)]
    assert sf.ADVISORY_HEAD not in texts and len(texts) == 1
    assert sf.edit_hint(run, stop) is None


def test_edit_hint_names_only_blocking_files(run) -> None:
    other = MINOR.replace("10-requirements.md", "15-behaviour-spec.md")
    _write("edge-findings.txt", MAJOR, other)
    hint = sf.edit_hint(run, _edge())
    assert hint is not None and "15-behaviour-spec.md" not in hint


def test_unrated_fail_is_not_instrument_failure(run) -> None:
    """FAIL ребра без находок — штатный исход проверки, не «сбой ревьюера»
    (ревью #760); строка видна на уровне 0."""
    _write("edge-findings.txt", NO_FINDINGS)
    stop = _edge()
    assert stop.unrated == [NO_FINDINGS] and stop.failures == []
    assert sf.summary(stop) == sf.UNBLOCKED_STOP
    assert sf.shown(stop) == [(f"  - {sf.UNRATED_TEXT}", 0)]
    assert sf.edit_hint(run, stop) is None


def test_gate_errors_on_level_zero(run) -> None:
    """Ошибки гейта (`error GC-…`) — на экране без -v; прочее — на -v."""
    _write(
        "gate-findings.txt",
        "error GC-ORPHAN: BEH-40: сирота",
        "not_evaluated: GC-X",
    )
    stop = sf.load("R-1", sf.GATE)
    assert stop is not None
    assert sf.summary(stop).startswith("проверка бандла не прошла: ошибок 1 (вывод: ")
    assert sf.shown(stop) == [
        ("  - GC-ORPHAN: BEH-40: сирота", 0),
        ("  not_evaluated: GC-X", 1),
    ]


def test_gate_check_format_errors_on_level_zero(run) -> None:
    """Формат самого gate-check (`GC-NN error […]`) — тоже ошибка (ревью #761)."""
    _write("gate-findings.txt", "GC-08 error [FR-01] нет критерия", "GC-05 warning x")
    stop = sf.load("R-1", sf.GATE)
    assert stop is not None
    assert "ошибок 1" in sf.summary(stop)
    assert sf.shown(stop)[0] == ("  - GC-08 error [FR-01] нет критерия", 0)
    assert sf.shown(stop)[1][1] == 1
