"""Блокировка прогона (спека need-stage §11.4.5).

Одна на вход процесса: точки входа (spec-loop, CLI раннера, brief-tools)
берут её сразу после выбора прогона и перечитывают `run.json` уже под ней;
функции раннера её не берут, а требуют токен `RunLock`. Файл блокировки
лежит рядом с `RUNS_ROOT`, не внутри каталога прогона: пустой каталог без
`run.json` `find_runs` счёл бы битым леджером.

Дескриптор не наследуется (`O_CLOEXEC` по умолчанию); соседу его передают
явно — `subprocess(..., pass_fds=(lock.fd,))`: пока жив процесс, держащий
описание файла, прогон занят, даже если родитель уже умер.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from governance import run_state as rs


@dataclass(frozen=True)
class RunLock:
    """Токен: процесс держит блокировку прогона `run_id` дескриптором `fd`."""

    run_id: str
    fd: int


class LockBusy(RuntimeError):
    """Прогон исполняется другим процессом."""


def lock_path(run_id: str) -> Path:
    """Файл блокировки прогона: `<RUNS_ROOT>-locks/<run_id>.lock`."""
    rs.validate_id_component(run_id, label="run_id")
    root = rs.RUNS_ROOT.with_name(rs.RUNS_ROOT.name + "-locks")
    return root / f"{run_id}.lock"


@contextmanager
def run_lock(run_id: str) -> Iterator[RunLock]:
    """Эксклюзивная неблокирующая блокировка прогона; занято — `LockBusy`."""
    path = lock_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(f"прогон {run_id} исполняется другим процессом") from exc
        yield RunLock(run_id, fd)
    finally:
        os.close(fd)


def require(lock: RunLock, run_id: str) -> None:
    """Функция раннера исполняется только под блокировкой своего прогона."""
    if not isinstance(lock, RunLock):
        raise TypeError("нужен RunLock: функция раннера — только под блокировкой")
    if lock.run_id != run_id:
        raise ValueError(f"блокировка {lock.run_id!r} не от прогона {run_id!r}")
