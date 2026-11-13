"""Лог вызова `dfl`: файл на вызов, ротация, перехват вывода раннера в файл."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from lite.failure_log import repo_key

KEEP = 20
EXIT_PREFIX = "# код выхода: "
QUIET_ENV = {"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}
PASSTHROUGH_LEVEL = 3
_NAME = re.compile(r"^\d{8}T\d{12}Z(?:-\d+)?-(?P<command>.+)\.log$")


@dataclass
class RunLog:
    """Открытый лог текущего вызова."""

    path: Path
    fh: TextIO


@dataclass
class LogInfo:
    """Запись о логе на диске: команда и код выхода (`None` — не закрыт)."""

    path: Path
    command: str
    code: int | None


def log_dir(cwd: Path, env: Mapping[str, str] = os.environ) -> Path:
    """Каталог логов репо `cwd`."""
    raw = env.get("XDG_STATE_HOME", "")
    state = Path(raw) if raw and Path(raw).is_absolute() else _home_state()
    return state / "darkfactory" / "logs" / repo_key(cwd)


def _home_state() -> Path:
    return Path.home() / ".local" / "state"


def open_log(cwd: Path, command: str, header: str, *, protect: Path | None) -> RunLog:
    """Создаёт лог вызова, пишет заголовок и чистит старые логи."""
    directory = log_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _new_path(directory, command)
    fh = open(path, "a", encoding="utf-8", errors="replace")
    fh.write(f"# {header}\n")
    fh.flush()
    prune(directory, KEEP, protect)
    return RunLog(path=path, fh=fh)


def _new_path(directory: Path, command: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    n = 0
    while True:
        infix = f"-{n}" if n else ""
        path = directory / f"{stamp}{infix}-{command}.log"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            n += 1
            continue
        return path


def close_log(log: RunLog, code: int) -> None:
    """Дописывает код выхода и закрывает лог."""
    log.fh.write(f"{EXIT_PREFIX}{code}\n")
    log.fh.close()


def prune(directory: Path, keep: int, protect: Path | None) -> None:
    """Удаляет самые старые логи (по имени), кроме `protect`."""
    if not directory.is_dir():
        return
    names = sorted(p for p in directory.glob("*.log") if p.is_file())
    for path in names[: max(0, len(names) - keep)]:
        if protect is not None and path == protect:
            continue
        path.unlink(missing_ok=True)


@contextmanager
def captured(log: RunLog | None, level: int) -> Iterator[None]:
    """На уровнях 0–2 уводит fd 1 и 2 (и потомков) в файл лога."""
    if log is None or level >= PASSTHROUGH_LEVEL:
        yield
        return
    _flush_all(log)
    saved = (os.dup(1), os.dup(2))
    old_env = {k: os.environ.get(k) for k in QUIET_ENV}
    try:
        os.dup2(log.fh.fileno(), 1)
        os.dup2(log.fh.fileno(), 2)
        os.environ.update(QUIET_ENV)
        yield
    finally:
        _flush_all(log)
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in saved:
            os.close(fd)
        _restore_env(old_env)


def _flush_all(log: RunLog) -> None:
    for stream in (sys.stdout, sys.stderr, log.fh):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass


def _restore_env(old: Mapping[str, str | None]) -> None:
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def list_logs(cwd: Path) -> list[LogInfo]:
    """Логи репо от старых к новым; код — из последней строки или `None`."""
    directory = log_dir(cwd)
    if not directory.is_dir():
        return []
    infos = []
    for path in sorted(directory.glob("*.log")):
        match = _NAME.match(path.name)
        if match:
            infos.append(LogInfo(path, match["command"], _exit_code(path)))
    return infos


def _exit_code(path: Path) -> int | None:
    lines = read_log(path).rstrip("\n").splitlines()
    last = lines[-1] if lines else ""
    if not last.startswith(EXIT_PREFIX):
        return None
    try:
        return int(last[len(EXIT_PREFIX) :])
    except ValueError:
        return None


def read_log(path: Path) -> str:
    """Текст лога; недекодируемые байты заменяются."""
    return path.read_text(encoding="utf-8", errors="replace")
