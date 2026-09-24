"""Чтение исторических леджеров переживает удаление прежнего пути (S13, §7.4).

Фикстуры сняты с РЕАЛЬНЫХ записей `out/governance-runs`, а не сочинены:
живая проверка на машине умирает вместе с сессией, фикстура переживает и
едет в CI. Источник каждой — в `governance_fixtures/historical_ledgers/README.md`.

Предмет: удаление ИСПОЛНЕНИЯ не трогает ЧТЕНИЕ. Леджер прежнего пути
по-прежнему загружается, показывается консолью и участвует в WS-lock —
и ровно так же, как до удаления.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import console_model as cm
from governance import run_state as rs
from governance import runner

_FIXTURES = Path(__file__).parent / "governance_fixtures" / "historical_ledgers"


@pytest.fixture()
def historical_runs(tmp_path: Path, monkeypatch) -> Path:
    """Копия фикстур под собственным RUNS_ROOT — оригиналы не трогаются."""
    root = tmp_path / "governance-runs"
    root.mkdir()
    for src in sorted(_FIXTURES.iterdir()):
        if src.is_dir():
            raw = (src / "run.json").read_text(encoding="utf-8")
            run_id = _run_id_of(raw)
            shutil.copytree(src, root / run_id)
    for mod in (rs, runner, cm):
        monkeypatch.setattr(mod, "RUNS_ROOT", root, raising=False)
    return root


def _run_id_of(raw: str) -> str:
    import json

    return json.loads(raw)["run_id"]


def test_every_historical_ledger_still_loads(historical_runs) -> None:
    """`rs.load` читает все классы: прежний путь, его терминальный статус,
    verification-потомок и волновой прогон."""
    ids = rs.all_run_ids()
    assert len(ids) == 4, ids

    by_mode = {}
    for run_id in ids:
        state = rs.load(run_id)
        by_mode.setdefault(state.authoring, []).append(state.status)

    assert sorted(by_mode["legacy"]) == ["completed", "completed", "merged_unverified"]
    assert by_mode["waves"] == ["completed"]


def test_console_renders_historical_ledgers_without_a_corrupt_row(
    historical_runs,
) -> None:
    """Консоль показывает их как прежде — ни одной строки-заглушки битого
    леджера. Удаление шагов конвейера не должно превращать историю в шум."""
    rows = cm.list_runs()
    assert len(rows) == 4
    assert all(row.status for row in rows)
    assert {row.status for row in rows} == {"completed", "merged_unverified"}

    detail = cm.run_detail("WS-SMOKE-001-a1")
    assert detail.row.status == "merged_unverified"


def test_ws_lock_still_reads_merged_unverified_the_same_way(
    historical_runs,
) -> None:
    """WS-lock продолжает судить по legacy-леджеру (D3).

    У этого родителя ЕСТЬ зелёный verification-потомок, поэтому он НЕ
    блокирует — и это не «лок сломался», а его штатный ответ. Проверяются
    обе стороны: с потомком — не блокирует, без него — блокирует. Одна
    сторона доказывала бы только, что функция не падает.
    """
    parent = rs.load("WS-SMOKE-001-a1")
    assert parent.status == "merged_unverified"
    assert runner._has_green_child("WS-SMOKE-001-a1") is True
    assert runner._blocking_merged_unverified(parent.ws_id) is None

    # Убираем потомка — родитель обязан снова держать ws_id.
    shutil.rmtree(rs.run_dir("WS-SMOKE-001-a1-v1"))
    assert runner._has_green_child("WS-SMOKE-001-a1") is False
    assert runner._blocking_merged_unverified(parent.ws_id) == "WS-SMOKE-001-a1"


def test_resume_of_a_historical_legacy_ledger_refuses_tracelessly(
    historical_runs,
) -> None:
    """Связка чтения и отказа: леджер читается, но исполнение не
    возобновляется — и отказ не меняет ни байта."""
    from tests.test_governance_runner import FakeOps

    ledger = rs.run_dir("WS-dispatcher-229-7ed609") / "run.json"
    before = ledger.read_bytes()

    with pytest.raises(ValueError) as exc:
        runner.resume("WS-dispatcher-229-7ed609", FakeOps())

    assert "S13" in str(exc.value) and "2026-09-23" in str(exc.value)
    assert ledger.read_bytes() == before
