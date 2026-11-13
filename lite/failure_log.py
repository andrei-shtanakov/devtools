"""Запись сбоя `dfl`: что упало, где и что видел коллега (для `dfl report`).

Запись — `$XDG_STATE_HOME/darkfactory/failures/<repo-key>/last.json`,
последний сбой репо; успешный вызов её не трогает. Ключ репо — хэш realpath
корня git-репо (`--cwd` не git — самого `--cwd`): он есть и у сбоя до
регистрации цели (спека `2026-11-12-dfl-report-design.md` §5).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from governance import run_state as rs
from lite import stop_findings as sf
from lite.output import split_refusal

RunsSnapshot = dict[str, float]
UNCHANGED_NOTE = "вызов его не менял"
NO_CAUSE = "причина не записана"
CAUSE_PREFIX = "  причина:"  # строка блока `lite.output.refusal`
# хвостовые строки-подсказки dfl: причиной не бывают (devtools#755)
HINT_PREFIXES = ("подробно:", "отчёт разработчикам:", "дальше:")


@dataclass(frozen=True)
class FailureRecord:
    """Сбой одного вызова `dfl`."""

    command: str
    argv: list[str]
    exit_code: int
    time: str
    output_tail: list[str]
    run_ids: list[str]
    runs_note: str
    cwd_repo: str
    cause: str
    log_path: str = ""  # лог вызова (`lite.runlog`); старые записи — пусто
    verbosity: int = 0


def repo_key(cwd: Path) -> str:
    """16 hex sha256 от realpath корня git-репо `cwd` (не git — от `cwd`)."""
    return hashlib.sha256(_root(cwd).encode()).hexdigest()[:16]


def record_path(cwd: Path, env: Mapping[str, str] = os.environ) -> Path:
    """Путь записи сбоя репо `cwd`."""
    raw = env.get("XDG_STATE_HOME", "")
    state = Path(raw) if raw and Path(raw).is_absolute() else _home_state()
    return state / "darkfactory" / "failures" / repo_key(cwd) / "last.json"


def _home_state() -> Path:
    return Path.home() / ".local" / "state"


def write(cwd: Path, rec: FailureRecord) -> None:
    """Атомарно записать сбой (tmp + `os.replace`), каталоги — `0o700`."""
    path = record_path(cwd)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = json.dumps(dataclasses.asdict(rec), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".last.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(cwd: Path) -> FailureRecord | None:
    """Последний сбой репо; нет файла или он не читается — `None`."""
    try:
        raw = json.loads(record_path(cwd).read_text(encoding="utf-8"))
        return FailureRecord(**raw)
    except (OSError, ValueError, TypeError):
        return None


def snapshot_runs() -> RunsSnapshot:
    """`run_id` → mtime `run.json` по всем прогонам."""
    out: RunsSnapshot = {}
    try:
        run_ids = rs.all_run_ids()
    except (OSError, ValueError):
        return out  # снимок — подсказка для отчёта, не повод менять код dfl
    for run_id in run_ids:
        try:
            out[run_id] = (rs.run_dir(run_id) / "run.json").stat().st_mtime
        except OSError:
            continue
    return out


def changed_runs(
    before: RunsSnapshot, after: RunsSnapshot, cwd: Path
) -> tuple[list[str], str]:
    """Прогоны, созданные или изменённые вызовом; нет — свежий прогон репо."""
    changed = sorted(r for r, m in after.items() if before.get(r) != m)
    if changed:
        return changed, ""
    mine = [r for r in after if _target_root(r) == _root(cwd)]
    if not mine:
        return [], ""
    return [max(mine, key=lambda r: after[r])], UNCHANGED_NOTE


def _root(cwd: Path) -> str:
    """realpath корня git-репо `cwd`; не git или каталога нет — realpath `cwd`."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return os.path.realpath(cwd)
    top = done.stdout.strip() if done.returncode == 0 else ""
    return os.path.realpath(top or cwd)


def _target_root(run_id: str) -> str | None:
    try:
        raw = json.loads((rs.run_dir(run_id) / "run.json").read_text("utf-8"))
        return os.path.realpath(raw["target_dir"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cause_from(
    exc: BaseException | None,
    run_ids: list[str],
    tail: list[str],
    *,
    refusals: tuple[type[BaseException], ...] = (),
) -> str:
    """Причина: отказ → блок отказа в выводе → `stop-reason.txt` (или
    `interview.stop_reason` в run.json, или сводка находок проверки) →
    исключение → последняя строка
    вывода, кроме подсказок dfl.

    Блок отказа в выводе — для отказов, которые печатает сама подкоманда
    (`init_repo`), не поднимая исключения до `main`.
    """
    if exc is not None and isinstance(exc, refusals):
        return split_refusal(str(exc))[0].strip() or NO_CAUSE
    printed = _printed_cause(tail)
    if printed:
        return printed
    for run_id in run_ids:
        reason = _first_line(rs.run_dir(run_id) / "stop-reason.txt")
        reason = reason or _interview_stop(run_id) or _findings_stop(run_id)
        if reason:
            return reason
    if exc is not None:
        first = str(exc).strip().splitlines()
        return f"{type(exc).__name__}: {first[0]}" if first else type(exc).__name__
    for line in reversed(tail):
        if line.strip() and not line.strip().startswith(HINT_PREFIXES):
            return line.strip()
    return NO_CAUSE


def _interview_stop(run_id: str) -> str:
    """Первая строка `interview.stop_reason` прогона, который СЕЙЧАС стоит в
    стопе интервью (стоп пишет причину в run.json, не в stop-reason.txt, и
    поле после снятия стопа остаётся); иначе — пусто."""
    try:
        raw = json.loads((rs.run_dir(run_id) / "run.json").read_text("utf-8"))
        if raw.get("status") != "stopped_interview":
            return ""
        reason = str((raw.get("interview") or {}).get("stop_reason") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    lines = reason.strip().splitlines()
    return lines[0].strip() if lines else ""


def _findings_stop(run_id: str) -> str:
    """Сводка находок проверки, на которой прогон СЕЙЧАС стоит (стоп
    edge-check и гейта `stop-reason.txt` не пишет, devtools#757)."""
    try:
        raw = json.loads((rs.run_dir(run_id) / "run.json").read_text("utf-8"))
        status = str(raw.get("status") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    found = sf.lines(run_id, status)
    return sf.summary(found, status) if found else ""


def _printed_cause(tail: list[str]) -> str:
    for line in reversed(tail):
        if line.startswith(CAUSE_PREFIX):
            return line.removeprefix(CAUSE_PREFIX).strip()
    return ""


def _first_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return ""
    return lines[0].strip() if lines else ""
