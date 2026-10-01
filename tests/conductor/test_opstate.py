"""Операционное состояние (спека среза 1, §4.6, §5.3–5.4)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conductor.opstate import (
    StateError,
    init_state,
    open_state,
    recover_state,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=2)


def _state(tmp_path: Path):
    init_state(tmp_path, T0)
    opened = open_state(tmp_path, LATER)
    assert opened.finding is None and opened.state is not None
    return opened.state


def _begin(state, aid: str, eff: str, now: datetime) -> None:
    state.begin_attempt(
        attempt_id=aid,
        mutation_id="m-" + aid,
        effect_key=eff,
        target="own/r#1",
        marker_key="k",
        action="nudge",
        subject="todo://r/x",
        now=now,
    )


def test_init_writes_init_last_and_quarantines(tmp_path: Path) -> None:
    init_state(tmp_path, T0)
    assert {p.name for p in tmp_path.iterdir()} == {
        "INIT",
        "attempts.jsonl",
        "failures.json",
        "quarantine.json",
    }
    opened = open_state(tmp_path, T0 + timedelta(minutes=10))
    assert opened.state is not None and opened.state.quarantined(
        T0 + timedelta(minutes=64)
    )
    assert not opened.state.quarantined(T0 + timedelta(minutes=66))


def test_init_refuses_non_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("1")
    with pytest.raises(StateError):
        init_state(tmp_path, T0)


def test_missing_init_is_uninitialized(tmp_path: Path) -> None:
    assert open_state(tmp_path, T0).finding == "OPSTATE-UNINITIALIZED"
    init_state(tmp_path, T0)
    (tmp_path / "INIT").unlink()
    opened = open_state(tmp_path, LATER)
    assert opened.state is None and opened.finding == "OPSTATE-UNINITIALIZED"


def test_recover_moves_files_and_quarantines(tmp_path: Path) -> None:
    init_state(tmp_path, T0)
    (tmp_path / "INIT").unlink()
    recover_state(tmp_path, LATER)
    assert (tmp_path / "corrupt").is_dir()
    opened = open_state(tmp_path, LATER + timedelta(minutes=1))
    assert opened.state is not None and opened.state.quarantined(
        LATER + timedelta(minutes=1)
    )
    assert opened.state.generation == 2


def test_recover_refuses_with_init_or_empty(tmp_path: Path) -> None:
    with pytest.raises(StateError):
        recover_state(tmp_path, T0)
    init_state(tmp_path, T0)
    with pytest.raises(StateError):
        recover_state(tmp_path, T0)


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: (d / "failures.json").unlink(),
        lambda d: (d / "quarantine.json").write_text("{"),
        lambda d: (d / "attempts.jsonl").write_text('x\n{"t": "header"}\n'),
        lambda d: (d / "failures.json").write_text(
            json.dumps({"generation": 9, "series": {}})
        ),
    ],
)
def test_damage_quarantines_with_new_generation(tmp_path: Path, damage) -> None:
    init_state(tmp_path, T0)
    damage(tmp_path)
    opened = open_state(tmp_path, LATER)
    assert opened.finding == "OPSTATE-LOST" and opened.state is not None
    assert opened.state.quarantined(LATER + timedelta(minutes=64))
    assert any((tmp_path / "corrupt").iterdir())
    assert opened.state.generation >= 2


def test_truncated_attempt_tail_is_repaired(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    with open(tmp_path / "attempts.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"t": "be')
    opened = open_state(tmp_path, LATER)
    assert opened.finding is None and opened.state is not None
    assert opened.state.delayed("e1", LATER + timedelta(minutes=1))
    _begin(opened.state, "a2", "e2", LATER)  # файл снова дописывается
    assert open_state(tmp_path, LATER).state is not None


def test_delay_by_effect_key(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    assert state.delayed("e1", LATER + timedelta(minutes=59))
    assert not state.delayed("e2", LATER)
    assert not state.delayed("e1", LATER + timedelta(minutes=61))
    assert state.delayed("e1", LATER - timedelta(minutes=5))  # часы назад
    state.finish_attempt("a1", "uncertain", LATER)
    assert state.delayed("e1", LATER + timedelta(minutes=30))
    state.settle("e1", LATER)
    assert not state.delayed("e1", LATER + timedelta(minutes=30))


def test_in_flight_survives_reload(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None and reloaded.delayed("e1", LATER)


def test_finished_failed_or_ok_does_not_delay(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _begin(state, "a1", "e1", LATER)
    state.finish_attempt("a1", "failed", LATER)
    assert not state.delayed("e1", LATER)


def test_series_table(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert state.series_event("m", "failed", "r1", LATER, error="500") == 1
    assert state.series_event("m", "failed", "r1", LATER) == 1  # +1 за прогон
    state.end_run(LATER)
    assert state.series_event("m", "failed", "r2", LATER) == 2
    state.end_run(LATER)
    [episode] = state.episodes()
    assert episode.first_run == "r1" and episode.runs == ("r1", "r2")
    assert state.series_event("m", "uncertain", "r3", LATER) == 2
    state.end_run(LATER)
    assert state.series_event("m", "delay", "r4", LATER) == 2
    state.end_run(LATER)
    assert state.series_event("m", "rate_limited", "r5", LATER) == 2
    state.end_run(LATER)
    state.end_run(LATER)  # r6: шаг не отправлялся — серия рвётся
    assert state.episodes() == []
    assert state.series_event("m", "failed", "r7", LATER) == 1
    assert state.series_event("m", "ok", "r8", LATER) == 0


def test_series_persist_across_reload(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.series_event("m", "failed", "r1", LATER)
    state.end_run(LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None
    assert reloaded.series_event("m", "failed", "r2", LATER) == 2


def test_open_attempts_one_per_effect_with_identity(tmp_path: Path) -> None:
    """Поиск эффекта (§5.3) получает незавершённые попытки с op/expected."""
    state = _state(tmp_path)
    state.begin_attempt(
        attempt_id="a1",
        mutation_id="m1",
        effect_key="e1",
        target="own/r#1",
        marker_key="k",
        action="nudge",
        subject="s",
        now=LATER,
        op="comment",
        expected="<!-- conductor:v1 q id=h1-0 -->",
    )
    _begin(state, "a2", "e1", LATER)  # тот же эффект
    _begin(state, "a3", "e3", LATER)
    state.finish_attempt("a3", "failed", LATER)
    reloaded = open_state(tmp_path, LATER).state
    assert reloaded is not None
    [found] = reloaded.open_attempts()
    assert (found["op"], found["expected"]) == (
        "comment",
        "<!-- conductor:v1 q id=h1-0 -->",
    )
    reloaded.settle("e1", LATER)
    assert reloaded.open_attempts() == []
