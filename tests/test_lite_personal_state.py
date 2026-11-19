"""Состояние личного прогона dfl."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from governance import run_state as rs
from lite.personal import state


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    return root


def _new(slug: str = "o/r", subject: str = "My task") -> state.PersonalRun:
    return state.new_personal_run(
        subject, "git@github.com:o/r.git", slug, "/tmp/t", date(2026, 11, 15)
    )


def test_roundtrip() -> None:
    run = _new()
    assert run.run_id.startswith(f"{run.ws_id}-")
    assert run.bundle_dir == f"workstreams/{run.ws_id}/spec"
    assert run.step == "interview"
    run.step = "design"
    run.spec_pr = 7
    state.save(run)
    assert state.load(run.run_id) == run


def test_load_ignores_unknown_keys() -> None:
    run = _new()
    state.save(run)
    path = state.run_dir(run.run_id) / "run.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_field"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert state.load(run.run_id) == run


def test_active_for_skips_done() -> None:
    old = _new()
    old.step = "done"
    state.save(old)
    assert state.active_for("o/r") is None
    live = _new()
    state.save(live)
    other = _new(slug="o/x")
    state.save(other)
    assert state.active_for("o/r") == live


def test_active_for_latest_mtime() -> None:
    first, second = _new(), _new()
    state.save(first)
    state.save(second)
    os.utime(state.run_dir(first.run_id) / "run.json", (1, 1))
    assert state.active_for("o/r") == second


def test_active_for_no_root() -> None:
    assert state.active_for("o/r") is None


def test_run_dir_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        state.run_dir("../x")


def test_personal_root_outside_runs_root() -> None:
    import importlib

    fresh = importlib.reload(state)
    assert fresh.personal_root() != rs.RUNS_ROOT
    assert rs.RUNS_ROOT not in fresh.personal_root().parents


def _cyr() -> state.PersonalRun:
    return state.new_personal_run(
        "Мой проект", "git@github.com:o/r.git", "o/r", "/tmp/t", date(2026, 11, 15)
    )


def test_cyrillic_subject_transliterated() -> None:
    run = _cyr()
    assert re.fullmatch(r"moj-proekt-20261115-[0-9a-f]{4}", run.ws_id)
    assert run.run_id.startswith(f"{run.ws_id}-")
    assert run.bundle_dir == f"workstreams/{run.ws_id}/spec"


def test_two_cyrillic_runs_distinct_ws_ids() -> None:
    assert len({_cyr().ws_id for _ in range(20)}) > 1


def test_active_for_skips_unreadable(
    personal_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Битый `run.json` и каталог с негодным именем — пропуск с одной
    строкой предупреждения, а не падение `dfl run` (Task 9)."""
    live = _new()
    state.save(live)
    for name, text in (
        ("bad id", "{}"),
        ("broken-1", "{not json"),
        ("partial", "{}"),
        ("listed", "[]"),
    ):
        (personal_root / name).mkdir()
        (personal_root / name / "run.json").write_text(text, encoding="utf-8")
    assert state.active_for("o/r") == live
    out = capsys.readouterr().out
    assert all(n in out for n in ("bad id", "broken-1", "partial", "listed"))


def test_ascii_ws_id_has_random_suffix() -> None:
    """Перезапуск той же ASCII-темы в тот же день не берёт старый бандл."""
    run = _new()
    assert re.fullmatch(r"my-task-20261115-[0-9a-f]{4}", run.ws_id)
    assert len({_new().ws_id for _ in range(20)}) > 1
