"""Журнал одного прогона runner'а: write-ahead, атомарный run.json (спека §4).

Состояние прогона живёт вне worktree целевого репо — под
`devtools/out/governance-runs/<run-id>/run.json` (тот же принцип, что
`--output-root` у issue_worker). Каждый шаг с внешним эффектом (ветка, PR,
ревью, мерж) ведётся как `pending → started → completed` со стабильным
operation key: `op_start` пишет `started` на диск ДО эффекта, чтобы resume мог
опереться на факт «эффект мог начаться» даже при падении между записью и
самим эффектом (write-ahead).

`merge_authority` на уровне прогона — только ужесточение до `"human"` (ось 3,
спека §6): эко-дефолт `agent` ослабить прогоном нельзя, поэтому `new_run`
принимает лишь `None` (не объявлено — дефолт вышестоящего уровня) или
`"human"`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

# Якорь через __file__ (как DEVTOOLS_ROOT в ops.py), не CWD-относительный
# путь: CWD-относительная версия резолвилась в `devtools/devtools/out/…`,
# когда процесс запускался из корня devtools (финальное ревью F-3), и делала
# `start`/`resume` из разных каталогов несовместимыми леджерами.
RUNS_ROOT = Path(__file__).resolve().parent.parent / "out" / "governance-runs"

_ALLOWED_MERGE_AUTHORITY = (None, "human")


@dataclass
class RunState:
    run_id: str
    subject: str
    repo: str
    repo_slug: str
    ws_id: str
    target_dir: str
    bundle_dir: str
    profile: str
    merge_authority: str | None
    status: str
    branch: str
    pr: int | None
    head: str | None
    ops: dict[str, dict]
    remediated_by: str | None
    # Default-ветка целевого репо на момент S7 (`pr_facts["baseRefName"]`,
    # фолбэк "master"), нужна S8 для чекаута перед authoritative-гейтом
    # (финальное ревью, круг 5). Дефолт `None` — старые поля перед ним без
    # дефолтов, дальше в списке этот новее их всех; runner проставляет его
    # явно в `_step_verdict`, не здесь.
    base_ref: str | None = None


def new_run(
    subject: str,
    repo: str,
    repo_slug: str,
    ws_id: str,
    target_dir: str,
    bundle_dir: str,
    profile: str,
    run_id: str,
    merge_authority: str | None = None,
) -> RunState:
    """Новый прогон (S0). `run_id` подаётся снаружи (вызывающая сторона)."""
    if merge_authority not in _ALLOWED_MERGE_AUTHORITY:
        raise ValueError(
            "merge_authority прогона может только ужесточать до 'human' "
            f"(допустимо None или 'human'), получено {merge_authority!r}"
        )
    return RunState(
        run_id=run_id,
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        ws_id=ws_id,
        target_dir=target_dir,
        bundle_dir=bundle_dir,
        profile=profile,
        merge_authority=merge_authority,
        status="running",
        branch="",
        pr=None,
        head=None,
        ops={},
        remediated_by=None,
    )


def run_dir(run_id: str) -> Path:
    """Каталог прогона под `RUNS_ROOT`."""
    return RUNS_ROOT / run_id


def save(state: RunState) -> None:
    """Атомарная запись `run.json`: temp-файл в том же каталоге + `os.replace`."""
    target_dir = run_dir(state.run_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(state), ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target_dir, prefix=".run.json.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, target_dir / "run.json")
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load(run_id: str) -> RunState:
    """Читает `run.json` и восстанавливает `RunState`."""
    raw = (run_dir(run_id) / "run.json").read_text(encoding="utf-8")
    return RunState(**json.loads(raw))


def all_run_ids() -> list[str]:
    """Все `run_id` под `RUNS_ROOT` (по имени каталога, JSON не проверяется).

    Используется WS-lock'ом (`runner._blocking_merged_unverified`, финальное
    ревью круг 5) для обхода соседних прогонов — сам список не решает,
    читается ли каждый `run.json`; битые леджеры отсеивает вызывающая
    сторона.
    """
    if not RUNS_ROOT.exists():
        return []
    return sorted(p.name for p in RUNS_ROOT.iterdir() if p.is_dir())


def op_status(state: RunState, key: str) -> str:
    """Статус операции по ключу; `"new"`, если ключа ещё нет."""
    op = state.ops.get(key)
    return "new" if op is None else op["status"]


def op_start(state: RunState, key: str) -> None:
    """Помечает операцию `started` и сохраняет ДО эффекта (write-ahead, §4)."""
    state.ops[key] = {"status": "started"}
    save(state)


def op_complete(state: RunState, key: str, **result: object) -> None:
    """Помечает операцию `completed`, сохраняет результат и записывает на диск."""
    state.ops[key] = {"status": "completed", **result}
    save(state)
