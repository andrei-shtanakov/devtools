"""Install journal (audit only), writer lock, generation counter (spec 3.3).

The journal never decides whether a step runs: only `Step.check()` does.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from df.fsutil import sync_file, write_atomic
from df.paths import Layout

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCK_FLAGS = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_GEN_MAX_BYTES = 64
_GEN_RE = re.compile(r"0|[1-9][0-9]*")
_BUSY = "идёт другая административная операция"
_CHANGING = "состояние меняется"


class StepFailed(Exception):
    """A step (or a precondition) failed; `step` names it."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


class NeedsHuman(Exception):
    """`init` stops until an external action is performed."""

    def __init__(self, instruction: str) -> None:
        super().__init__(instruction)
        self.instruction = instruction


class _Interrupted(Exception):
    """Test-only interruption raised by `run_steps(fail_after=...)`."""


@dataclass(frozen=True)
class Step:
    """`check()` means "the fact is already achieved"."""

    name: str
    check: Callable[[], bool]
    do: Callable[[], None]


@dataclass(frozen=True)
class Stable[T]:
    """A snapshot read under an unchanged even generation."""

    value: T
    generation: int


@dataclass(frozen=True)
class Transitional:
    """A writer is mid-flight (odd generation); nothing was read."""

    generation: int


# --- writer lock -----------------------------------------------------------


def writer_lock(layout: Layout) -> contextlib.AbstractContextManager[int]:
    """Exclusive non-blocking `flock` on `layout.lock`; yields the fd."""
    return _writer_lock(layout)


@contextlib.contextmanager
def _writer_lock(layout: Layout) -> Iterator[int]:
    dir_fd = _open_install_dir(layout)
    try:
        fd = _open_lock_file(dir_fd, layout.lock.name)
    finally:
        os.close(dir_fd)
    try:
        _flock_or_refuse(fd)
        yield fd
    finally:
        os.close(fd)


def _open_install_dir(layout: Layout) -> int:
    """Walk root -> install_dir, creating missing parts, refusing symlinks."""
    parts = layout.install_dir.relative_to(layout.root).parts
    try:
        fd = os.open(layout.root, _DIR_FLAGS)
    except OSError as exc:
        raise StepFailed("lock", f"{layout.root}: {exc.strerror}") from exc
    for name in parts:
        try:
            nxt = _open_or_make_dir(fd, name)
        except OSError as exc:
            raise StepFailed("lock", f"{name}: {_why(exc)}") from exc
        finally:
            os.close(fd)
        fd = nxt
    return fd


def _open_or_make_dir(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)


def _why(exc: OSError) -> str:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return "не настоящий каталог (символическая ссылка?)"
    return exc.strerror or str(exc)


def _open_lock_file(dir_fd: int, name: str) -> int:
    try:
        fd = os.open(name, _LOCK_FLAGS, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise StepFailed("lock", f"{name}: {_why(exc)}") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise StepFailed("lock", f"{name}: не обычный файл")
    return fd


def _flock_or_refuse(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise StepFailed("lock", _BUSY) from exc
        raise


# --- generation counter ----------------------------------------------------


def generation_begin(layout: Layout) -> None:
    """Make the generation odd; an already odd value stays (dead writer)."""
    n = _read_generation(layout)
    if n % 2 == 1:
        return
    _write_generation(layout, n + 1)


def generation_end(layout: Layout) -> None:
    """Make the generation even; calling it on an even value is a bug."""
    n = _read_generation(layout)
    if n % 2 == 0:
        raise RuntimeError(f"generation_end on even generation {n}")
    _write_generation(layout, n + 1)


def _write_generation(layout: Layout, n: int) -> None:
    write_atomic(layout.generation, f"{n}\n", mode=0o644)


def _read_generation(layout: Layout) -> int:
    """Absent -> 0; otherwise a canonical decimal or StepFailed("snapshot")."""
    path = layout.generation
    try:
        fd = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise StepFailed("snapshot", f"generation: {_why(exc)}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StepFailed("snapshot", "generation: не обычный файл")
        raw = os.read(fd, _GEN_MAX_BYTES + 1)
    finally:
        os.close(fd)
    return _parse_generation(raw)


def _parse_generation(raw: bytes) -> int:
    if len(raw) > _GEN_MAX_BYTES:
        raise StepFailed("snapshot", "generation: слишком длинное значение")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StepFailed("snapshot", "generation: не ASCII") from exc
    text = text.removesuffix("\n")
    if not _GEN_RE.fullmatch(text):
        raise StepFailed("snapshot", f"generation: не число: {text!r}")
    return int(text)


def read_consistent[T](
    layout: Layout, read: Callable[[], T], *, attempts: int = 3
) -> Stable[T] | Transitional:
    """Read under an unchanged even generation; never opens the lock."""
    for _ in range(attempts):
        g1 = _read_generation(layout)
        if g1 % 2 == 1:
            return Transitional(g1)
        value = read()
        if _read_generation(layout) == g1:
            return Stable(value, g1)
    raise StepFailed("snapshot", _CHANGING)


# --- journal ---------------------------------------------------------------


def journal_event(
    journal: Path, *, run_id: str, step: str, event: str, detail: str = ""
) -> None:
    """Append one JSON line (audit) and sync it."""
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "step": step,
        "event": event,
        "detail": detail,
    }
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(journal, _APPEND_FLAGS, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        sync_file(fd)
    finally:
        os.close(fd)


def read_journal(journal: Path) -> list[dict[str, object]]:
    """Parse the journal for display; a malformed line is an error."""
    try:
        fd = os.open(journal, _READ_FLAGS)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, encoding="utf-8") as fh:
        return [_parse_line(n, line) for n, line in enumerate(fh, 1)]


def _parse_line(number: int, line: str) -> dict[str, object]:
    try:
        row = json.loads(line)
    except ValueError as exc:
        raise ValueError(f"journal line {number}: malformed") from exc
    if not isinstance(row, dict):
        raise ValueError(f"journal line {number}: not an object")
    return row


def run_steps(
    steps: Sequence[Step],
    journal: Path,
    *,
    run_id: str,
    fail_after: str | None = None,
) -> None:
    """Run steps; only `check()` decides. Caller already holds the lock."""
    for step in steps:
        _run_step(step, journal, run_id, fail_after)


def _run_step(step: Step, journal: Path, run_id: str, fail_after: str | None) -> None:
    def log(event: str, detail: str = "") -> None:
        journal_event(
            journal, run_id=run_id, step=step.name, event=event, detail=detail
        )

    if step.check():
        log("skip")
        return
    log("start")
    step.do()
    if fail_after == step.name:
        raise _Interrupted(step.name)
    if not step.check():
        log("failed", "postcondition not met")
        raise StepFailed(step.name, "постусловие не выполнено")
    log("done")
