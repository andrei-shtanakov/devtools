"""Read-only view-model прогонов и бандла для behaviour console (спека
Task 3): собирает `RunRow`/`RunDetail` поверх `governance.run_state`
(B1) и срез бандла поверх `governance.bundle_state` (Task 2). Никаких
ops/subprocess — модуль только читает то, что уже на диске.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from governance import bundle_dag
from governance import run_state as rs
from governance.bundle_state import candidate_state

# Порядок op-ключей пайплайна (B1 run_state.py / runner.py): используется
# и для вычисления текущего шага прогона, и для стабильного порядка
# `RunDetail.ops`.
PIPELINE_KEYS: tuple[str, ...] = (
    "branch",
    "materialize-brief",
    "author-charter",
    "author-requirements",
    "author-behaviour",
    "author-design",
    "author-acceptance",
    "author-decomposition",
    "commit",
    "gate-candidate",
    "push",
    "pr",
    "ready",
    "review",
    "verdict",
    "merge",
    "sync-default",
    "gate-authoritative",
    "remediation-issue",
)

# Findings-файлы, которые может нести каталог прогона — читаем оба, если
# есть (спека Task 3): gate-candidate пишет `gate-findings.txt`,
# authoritative-гейт S8 — `s8-findings.txt`.
_FINDINGS_FILES: tuple[str, ...] = (
    "brief-findings.txt", "gate-findings.txt", "s8-findings.txt",
)

# Ключи, которые наступают при обычном продвижении прогона до его конца.
# `remediation-issue` из `PIPELINE_KEYS` исключён (финальное ревью I-5):
# это УСЛОВНЫЙ op — `_step_s8` на exit 0 завершает прогон и возвращает
# `True` ДО блока создания issue (`runner.py:833-839`), поэтому у зелёного
# прогона он навсегда `"new"`. Считать его обязательным шагом инвертирует
# картину: `status="completed"` показывался бы «застрявшим» на
# `remediation-issue`, а `merged_unverified` (где issue реально создан) —
# наоборот, финишным `"—"`. `RunDetail.ops` по-прежнему строится по полному
# `PIPELINE_KEYS` — это только про вычисление «текущего шага» таблицы.
_REQUIRED_STEP_KEYS: tuple[str, ...] = tuple(
    key for key in PIPELINE_KEYS if key != "remediation-issue"
)

# Терминальные статусы прогона (спека §4/§5): `completed` — обычный зелёный
# финиш, `merged_unverified` — S8 навсегда остановился (см. verify()).
# `_current_step` возвращает `"—"` на них напрямую, а не только через
# перебор `_REQUIRED_STEP_KEYS` — защита от рассинхрона status/ops, а не
# только от условного `remediation-issue`.
_TERMINAL_STATUSES: tuple[str, ...] = ("completed", "merged_unverified")

# Хвост пайплайна после авторинга/публикации — общий для обоих режимов.
_TAIL_KEYS: tuple[str, ...] = (
    "merge", "sync-default", "gate-authoritative", "remediation-issue",
)
_AUTHOR_KEY_OF = {
    "charter": "author-charter",
    "requirements": "author-requirements",
    "behaviour-spec": "author-behaviour",
    "design": "author-design",
    "acceptance": "author-acceptance",
    "decomposition": "author-decomposition",
}


def pipeline_keys(state: rs.RunState) -> tuple[str, ...]:
    """Порядок op-ключей ЭТОГО прогона: legacy — `PIPELINE_KEYS`; волны —
    per-wave ключи (S10) волн 1..`state.wave` и общий хвост S8.

    Статический кортеж `PIPELINE_KEYS` сохраняется как источник legacy и
    для тестов FR-04; волновой порядок выводится из уровней DAG, а не
    хардкодится второй раз.
    """
    if state.authoring != "waves":
        return PIPELINE_KEYS
    levels = bundle_dag.levels(bundle_dag.BUNDLE_DAG)
    keys: list[str] = []
    for wave in range(1, max(state.wave, 1) + 1):
        keys += [f"branch-{wave}", f"materialize-brief-{wave}"]
        keys += [
            _AUTHOR_KEY_OF[node] for node in levels if levels[node] == wave - 1
        ]
        keys += [
            f"commit-{wave}", f"gate-candidate-{wave}", f"edge-{wave}",
            f"push-{wave}", f"candidate-{wave}", f"finalize-{wave}",
        ]
    return tuple(keys) + _TAIL_KEYS


def required_step_keys(state: rs.RunState) -> tuple[str, ...]:
    """Обязательные шаги прогона для «текущего шага» (без условного
    `remediation-issue`; в волнах — и без `push-<w>`/`finalize-<w>`,
    которые наступают не в каждой волне: push пропускает режим reapprove,
    finalize — заявка, вмерженная агентом с первого захода)."""
    conditional = {"remediation-issue"}
    if state.authoring == "waves":
        conditional |= {
            key for key in pipeline_keys(state)
            if key.startswith(("push-", "finalize-"))
        }
    return tuple(k for k in pipeline_keys(state) if k not in conditional)


@dataclass(frozen=True)
class RunRow:
    run_id: str
    ws_id: str
    repo: str
    status: str
    step: str
    pr: int | None
    remediated_by: str | None
    # Волна прогона (0 — legacy); показ волнового режима (Task 11).
    wave: int = 0


@dataclass(frozen=True)
class RunDetail:
    row: RunRow
    ops: tuple[tuple[str, str], ...]
    findings: str
    verdict_reason: str | None


def _op_status_of(ops: dict[str, dict], key: str) -> str:
    op = ops.get(key)
    return "new" if op is None else op.get("status", "new")


def _current_step(state: rs.RunState) -> str:
    """Первый не-`completed` обязательный op-ключ либо `"—"`.

    `"—"` — либо все `_REQUIRED_STEP_KEYS` завершены, либо `status` уже
    терминален (`_TERMINAL_STATUSES`, I-5): второе проверяется первым, так
    что рассинхрон между `status` и `ops` (не должен случаться штатно, но
    не должен и рисовать несуществующий шаг) не путает таблицу.
    """
    if state.status in _TERMINAL_STATUSES:
        return "—"
    for key in required_step_keys(state):
        if key.startswith("materialize-brief") and state.brief is None:
            continue
        if _op_status_of(state.ops, key) != "completed":
            return key
    return "—"


def _row_from_state(state: rs.RunState) -> RunRow:
    return RunRow(
        run_id=state.run_id,
        ws_id=state.ws_id,
        repo=state.repo,
        status=state.status,
        step=_current_step(state),
        pr=state.pr if state.pr is not None else _wave_pr(state),
        remediated_by=state.remediated_by,
        wave=state.wave,
    )


def _wave_pr(state: rs.RunState) -> int | None:
    """В волнах бандл-PR нет: показывается candidate-PR текущей волны."""
    if state.authoring != "waves":
        return None
    record = state.ops.get(f"candidate-{state.wave}") or {}
    op = state.ops.get(record.get("request") or "") or {}
    pr = op.get("candidate_pr")
    return pr if isinstance(pr, int) else None


def _corrupt_row(run_id: str) -> RunRow:
    return RunRow(
        run_id=run_id, ws_id="", repo="", status="corrupt", step="—",
        pr=None, remediated_by=None,
    )


def list_runs() -> tuple[RunRow, ...]:
    """Все прогоны под `RUNS_ROOT`; битый `run.json` -> строка `status="corrupt"`."""
    rows: list[RunRow] = []
    for run_id in rs.all_run_ids():
        try:
            state = rs.load(run_id)
        except (OSError, ValueError, TypeError, KeyError):
            rows.append(_corrupt_row(run_id))
            continue
        rows.append(_row_from_state(state))
    return tuple(rows)


def _read_findings(run_id: str) -> str:
    """Содержимое `gate-findings.txt`/`s8-findings.txt`, что есть — конкатенация."""
    run_dir = rs.run_dir(run_id)
    parts = [
        (run_dir / name).read_text(encoding="utf-8")
        for name in _FINDINGS_FILES
        if (run_dir / name).exists()
    ]
    return "\n".join(parts)


def run_detail(run_id: str) -> RunDetail:
    """Детальная карточка прогона: ops в порядке пайплайна, findings, verdict."""
    state = rs.load(run_id)
    visible_keys = tuple(
        key for key in pipeline_keys(state)
        if not key.startswith("materialize-brief") or state.brief is not None
    )
    ops = tuple((key, _op_status_of(state.ops, key)) for key in visible_keys)
    verdict_op = state.ops.get("verdict")
    verdict_reason = None if verdict_op is None else verdict_op.get("reason")
    return RunDetail(
        row=_row_from_state(state),
        ops=ops,
        findings=_read_findings(run_id),
        verdict_reason=verdict_reason,
    )


def bundle_summary(
    target_dir: str, profile: str, bundle_dir: str
) -> tuple[tuple[str, str], ...]:
    """`(node_id, status)` из `candidate_state`; ошибки чтения -> `[("error", msg)]`."""
    try:
        state = candidate_state(
            Path(target_dir) / profile, Path(target_dir) / bundle_dir
        )
    except Exception as exc:  # noqa: BLE001 — read-only срез не должен падать
        return (("error", str(exc)),)
    return tuple((node.node_id, node.status) for node in state.nodes)


def rows_to_json(rows: tuple[RunRow, ...]) -> str:
    """Список `RunRow` в JSON."""
    return json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2)


def detail_to_json(detail: RunDetail) -> str:
    """`RunDetail` в JSON (`ops` — список пар `[key, status]`, порядок сохранён)."""
    return json.dumps(asdict(detail), ensure_ascii=False, indent=2)
