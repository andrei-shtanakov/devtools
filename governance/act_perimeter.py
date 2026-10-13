"""Периметр источника политики (спека §6.2 б, §6.3).

Потребитель, обнаруживший нарушение, пишет долговечный маркер с номером
текущей эпохи и отказывает во всех решениях источника, пока в журнале не
появится эпоха с бОльшим номером. Канал уведомления человека — не часть
протокола. Битый маркер блокирует (fail-closed); нечитаемый ≠ нарушение:
маркер пишет только установленное нарушение.
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from governance.act_epochs import Epoch

_WRITE = ("push", "maintain", "admin")


class PerimeterError(ValueError):
    """Маркер периметра не записать или аргумент недопустим."""


def marker_path(source_repo: str, state_home: Path) -> Path:
    """`$XDG_STATE_HOME/darkfactory/perimeter/<owner>__<repo>.json`."""
    name = source_repo.replace("/", "__") + ".json"
    return state_home / "darkfactory" / "perimeter" / name


def _marked_epoch(path: Path) -> int | None:
    """Номер эпохи маркера; `None` — маркер битый (блокирует)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    marked = data.get("epoch")
    if type(marked) is not int or marked < 1:
        return None
    return marked


def _write_marker(path: Path, epoch_no: int, detail: str) -> None:
    tmp = path.with_suffix(".tmp")
    payload = json.dumps({"epoch": epoch_no, "detail": detail}, ensure_ascii=False)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def record_violation(
    source_repo: str, epoch_no: int, detail: str, state_home: Path
) -> None:
    """Записать маркер монотонно: под `flock` хранится максимум эпох обнаружения.

    Запоздалый писатель с меньшей эпохой маркер не понижает; битый маркер не
    перезаписывается (он и так блокирует — снимает его человек).
    """
    if type(epoch_no) is not int or epoch_no < 1:
        raise PerimeterError(f"номер эпохи обнаружения недопустим: {epoch_no!r}")
    path = marker_path(source_repo, state_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                current = _marked_epoch(path)
                if current is None or current >= epoch_no:
                    return
            _write_marker(path, epoch_no, detail)
    except OSError as exc:
        raise PerimeterError(f"маркер периметра {path} не записан: {exc}") from exc


def blocked(source_repo: str, epochs: Sequence[Epoch], state_home: Path) -> str | None:
    """Причина блокировки источника либо `None`."""
    path = marker_path(source_repo, state_home)
    try:
        present = path.exists()
    except OSError:
        present = True
    if not present:
        return None
    marked = _marked_epoch(path)
    if marked is None:
        return f"маркер периметра {path} не читается — источник заблокирован"
    if epochs and epochs[-1].epoch > marked:
        return None
    return (
        f"нарушение периметра обнаружено в эпохе {marked}; ждём эпоху с большим "
        "номером в журнале (§6.3)"
    )


def permissions_refusal(permissions: Mapping[str, object] | None) -> str | None:
    """Самоотчёт прав credential на источнике: запись → отказ; нет поля → отказ."""
    if permissions is None or any(k not in permissions for k in _WRITE):
        return (
            "права credential на источнике не установлены "
            "(недоступное ≠ подтверждённое)"
        )
    granted = [k for k in _WRITE if permissions[k] is not False]
    if granted:
        return f"у credential процесса есть {', '.join(granted)} на источнике политики"
    return None
