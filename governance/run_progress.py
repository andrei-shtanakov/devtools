"""Видимый прогресс фазы реализации dfl (devtools#765).

Пока `spec-runner run` идёт (блокирующий `Ops.spec_runner`), окно `dfl`
показывает строки событий задач и живой отсчёт текущей. Источник — файл
состояния spec-runner (`.executor-state.db`, стабильный контракт
`spec-runner/docs/state-schema.md`: `tasks`, `attempts`) только на чтение и
`tasks.md` прогона (названия, число задач). Наблюдатель никогда не валит
прогон: любая ошибка чтения — пропуск такта.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from governance.task_bridge import _TASK_HEAD_RE

UNAVAILABLE = "прогресс недоступен: нет {path}"

_CONFIG = "spec-runner.config.yaml"
_LEGACY_CONFIG = "spec/executor.config.yaml"


def _paths_section(target: Path) -> dict:
    """`paths` из конфига spec-runner (как его `_resolve_config_path`)."""
    for rel, wrapped in ((_CONFIG, False), (_LEGACY_CONFIG, True)):
        path = target / rel
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if wrapped and isinstance(data, dict):
            data = data.get("executor") or {}
        paths = data.get("paths") if isinstance(data, dict) else None
        return paths if isinstance(paths, dict) else {}
    return {}


def state_db_path(target_dir: str, prefix: str) -> Path:
    """Путь БД состояния spec-runner для `--spec-prefix=<prefix>`.

    Как `spec_runner.config`: `paths.state` из конфига (тогда префикс не
    применяется) относительно `paths.root`; иначе
    `spec/.executor-{prefix}state.db`. Нечитаемый конфиг — умолчание.
    """
    target = Path(target_dir)
    paths = _paths_section(target)
    root = target / str(paths["root"]) if paths.get("root") else target
    state = paths.get("state")
    if state:
        state_path = Path(str(state))
        return state_path if state_path.is_absolute() else root / state_path
    return root / "spec" / f".executor-{prefix}state.db"


def read_tasks(text: str) -> list[tuple[str, str]]:
    """`(id, название)` задач `tasks.md` в порядке файла."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if _TASK_HEAD_RE.match(line):
            head, title = line[4:].split(": ", 1)
            out.append((head.strip(), title.strip()))
    return out


@dataclass(frozen=True)
class TaskRow:
    """Строка задачи в снимке БД: статус, времена, число попыток, ошибка."""

    status: str
    started_at: str | None
    completed_at: str | None
    attempts: int
    last_error: str | None


_SNAPSHOT_SQL = """
SELECT t.task_id, t.status, t.started_at, t.completed_at,
       (SELECT COUNT(*) FROM attempts a WHERE a.task_id = t.task_id),
       (SELECT a.error FROM attempts a WHERE a.task_id = t.task_id
        ORDER BY a.timestamp DESC, a.id DESC LIMIT 1)
FROM tasks t
"""


def read_snapshot(db: Path) -> dict[str, TaskRow] | None:
    """Снимок задач БД только на чтение; нет файла или ошибка — `None`."""
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            rows = con.execute(_SNAPSHOT_SQL).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return {
        str(r[0]): TaskRow(str(r[1]), r[2], r[3], int(r[4] or 0), r[5]) for r in rows
    }


def fmt_duration(seconds: float) -> str:
    """`ММ:СС`, от часа — `Ч:ММ:СС`."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def event_line(
    kind: str,
    task_id: str,
    title: str,
    *,
    seconds: float | None = None,
    attempt: int | None = None,
    error: str | None = None,
) -> str:
    """Строка события задачи (форма дизайна §3.1)."""
    if kind == "started":
        return f"▶ {task_id} «{title}» — начата"
    if kind == "done":
        return f"✓ {task_id} готова · {fmt_duration(seconds or 0)}"
    if kind == "retry":
        return f"↻ {task_id} «{title}» — попытка {attempt}"
    first = (error or "").strip().splitlines()
    return f"✗ {task_id} не прошла: {first[0] if first else 'причина не записана'}"


def live_line(task_id: str, ordinal: int, total: int, seconds: float) -> str:
    """Живая строка: задача, «N из M» (если M известно), отсчёт."""
    count = f" · {ordinal} из {total}" if total else ""
    return f"  {task_id}{count} · {fmt_duration(seconds)}"


def summary_line(done: int, total: int, seconds: float) -> str:
    """Итог фазы реализации (без стоимости — решение владельца)."""
    return f"реализация: сделано {done} из {total} · {fmt_duration(seconds)}"
