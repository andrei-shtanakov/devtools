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

from df import fsutil
from df.fsutil import create_file, make_dir, sync_file, write_atomic
from df.paths import Layout

_NB = os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FOLLOW_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
ROOT_UID = 0
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_LOCK_FLAGS = os.O_RDWR | _NB
_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | _NB
_READ_FLAGS = os.O_RDONLY | _NB
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
    """A snapshot accepted for the caller's mode.

    Usually an even generation read twice unchanged; a writer holding the
    lock may also build one with an odd generation (interrupted writer).
    """

    value: T
    generation: int


@dataclass(frozen=True)
class Transitional:
    """The state is not settled: odd generation, nothing was read.

    Callers may also build one with an even generation when they see
    `pending.toml`; that detection belongs to snapshot code (Tasks 7/9),
    not to `read_consistent`.
    """

    generation: int


# --- writer lock -----------------------------------------------------------


def writer_lock(layout: Layout) -> contextlib.AbstractContextManager[int]:
    """Exclusive non-blocking `flock` on `layout.lock`; yields the fd."""
    return _writer_lock(layout)


@contextlib.contextmanager
def _writer_lock(layout: Layout) -> Iterator[int]:
    dir_fd = _open_install_dir(layout)
    try:
        fd = _open_lock_file(dir_fd, layout.lock)
    finally:
        os.close(dir_fd)
    try:
        _flock_or_refuse(fd, layout.lock)
        yield fd
    finally:
        os.close(fd)


def _open_install_dir(layout: Layout) -> int:
    """Walk root -> install_dir, creating missing parts.

    Components above `layout.var` follow system links (macOS /var);
    `df` and `install` are no-follow. Every directory must be trusted.
    Settled once each (spec 3.3 r6): every directory of the chain, root
    included, is synced exactly once, after its child entry exists -- its
    mode and the child's name -- and `install_dir` last, all before the
    lock is taken.
    """
    parts = layout.install_dir.relative_to(layout.root).parts
    first_nofollow = len(layout.var.relative_to(layout.root).parts) - 1
    path = layout.root
    fd = _step_open(path, lambda: os.open(path, _DIR_FOLLOW_FLAGS))
    for i, name in enumerate(parts):
        path = path / name
        own = i >= first_nofollow  # df's own: `var` itself and below
        flags = _DIR_FLAGS if own else _DIR_FOLLOW_FLAGS
        fd = _descend(fd, path, flags, own=own)
    try:
        fsutil.sync_dir(fd)
    except OSError as exc:
        os.close(fd)
        raise StepFailed("lock", f"{path}: {_why(exc)}") from exc
    return fd


def _descend(parent_fd: int, path: Path, flags: int, *, own: bool) -> int:
    """Open child `path.name` of `parent_fd`, then sync the parent (its
    mode, the child's name); always closes the parent. `own` (by position:
    `var` and below) -- only those may be repaired."""
    try:
        fd = _step_open(
            path, lambda: _open_or_make_dir(parent_fd, path, flags, own=own)
        )
        try:
            fsutil.sync_dir(parent_fd)
        except OSError as exc:
            os.close(fd)
            raise StepFailed("lock", f"{path.parent}: {_why(exc)}") from exc
        return fd
    finally:
        os.close(parent_fd)


def _step_open(path: Path, opener: Callable[[], int]) -> int:
    """Run `opener`, then require a trusted directory; fail as "lock"."""
    try:
        fd = opener()
    except OSError as exc:
        raise StepFailed("lock", f"{path}: {_why(exc)}") from exc
    try:
        _require_trusted_dir(fd, path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _require_trusted_dir(fd: int, path: Path) -> None:
    st = os.fstat(fd)
    if fsutil.owner_uid(st) not in (ROOT_UID, os.geteuid()):
        raise StepFailed("lock", f"{path}: не доверенный каталог (владелец)")
    if st.st_mode & _NO_WRITE_OTHERS:
        raise StepFailed("lock", f"{path}: не доверенный каталог (права)")


def _open_or_make_dir(parent_fd: int, path: Path, flags: int, *, own: bool) -> int:
    """Make `path` its final form (`fsutil.make_dir`: created `0755`, or
    found; only `df`'s own are repaired), then open it; the walk syncs (one
    sync per directory); a concurrent creator winning the race is fine."""
    make_dir(parent_fd, path.name, 0o755, path=path, repair=own, settle=False)
    return os.open(path.name, flags, dir_fd=parent_fd)


def _why(exc: OSError) -> str:
    if exc.errno == errno.ELOOP:
        return "символическая ссылка"
    if exc.errno == errno.EISDIR:
        return "не обычный файл"
    if exc.errno == errno.ENOTDIR:
        return "не каталог (символическая ссылка?)"
    return exc.strerror or errno.errorcode.get(exc.errno or 0, str(exc))


def _open_regular(
    dir_fd: int | None,
    path: Path,
    flags: int,
    step: str,
    *,
    writer: bool = False,
    exact_mode: int | None = None,
) -> int:
    """Open without blocking or following links; only regular files pass.

    `writer` adds the shared-file rules: owned by euid, a single link and,
    when given, exactly `exact_mode`.
    """
    target = path.name if dir_fd is not None else path
    try:
        fd = os.open(target, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise StepFailed(step, f"{path}: {_why(exc)}") from exc
    try:
        problem = _file_problem(os.fstat(fd), writer, exact_mode)
    except BaseException:
        os.close(fd)
        raise
    if problem is not None:
        os.close(fd)
        raise StepFailed(step, f"{path}: {problem}")
    return fd


def _file_problem(
    st: os.stat_result, writer: bool, exact_mode: int | None
) -> str | None:
    if not stat.S_ISREG(st.st_mode):
        return "не обычный файл"
    if not writer:
        return None
    if fsutil.owner_uid(st) != os.geteuid():
        return "владелец не текущий пользователь"
    if st.st_nlink != 1:
        return "несколько жёстких ссылок"
    mode = stat.S_IMODE(st.st_mode)
    if exact_mode is not None and mode != exact_mode:
        return f"права {mode:04o}, нужны {exact_mode:04o}"
    return None


def _open_lock_file(dir_fd: int, path: Path) -> int:
    return _open_private(dir_fd, path, _LOCK_FLAGS, "lock")


def _open_private(dir_fd: int | None, path: Path, flags: int, step: str) -> int:
    """Open a writer-side file (lock, journal): existing must be exactly 0600;
    a missing one is created exclusively at 0600 whatever the umask, durably
    (`fsutil.create_file`)."""
    try:
        return _open_regular(dir_fd, path, flags, step, writer=True, exact_mode=0o600)
    except StepFailed:
        if not _is_absent(path, dir_fd):
            raise
    return _create_private(dir_fd, path, flags, step)


def _is_absent(path: Path, dir_fd: int | None) -> bool:
    target = path.name if dir_fd is not None else path
    try:
        os.stat(target, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _create_private(dir_fd: int | None, path: Path, flags: int, step: str) -> int:
    try:
        return create_file(path, flags, 0o600, dir_fd=dir_fd)
    except FileExistsError:
        return _open_regular(dir_fd, path, flags, step, writer=True, exact_mode=0o600)
    except OSError as exc:
        raise StepFailed(step, f"{path}: {_why(exc)}") from exc


def _flock_or_refuse(fd: int, path: Path) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise StepFailed("lock", _BUSY) from exc
        raise StepFailed("lock", f"{path}: {_why(exc)}") from exc


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


def read_generation(layout: Layout) -> int:
    """The generation counter: absent -> 0; a malformed one is
    `StepFailed("snapshot")` (bootstrap's snapshot, Task 9)."""
    return _read_generation(layout)


def _write_generation(layout: Layout, n: int) -> None:
    write_atomic(layout.generation, f"{n}\n", mode=0o644)


def _read_generation(layout: Layout) -> int:
    """Absent -> 0; otherwise a canonical decimal or StepFailed("snapshot")."""
    path = layout.generation
    try:
        fd = _open_regular(None, path, _READ_FLAGS, "snapshot")
    except StepFailed:
        if _lexists(path):
            raise
        return 0
    try:
        raw = os.read(fd, _GEN_MAX_BYTES + 1)
    finally:
        os.close(fd)
    return _parse_generation(raw)


def _lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


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
    """Read under an unchanged even generation; never opens the lock.

    Exceptions from `read()` propagate as is: snapshot parts are replaced
    atomically, so `read()` must not fail on a concurrent change.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
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
    fd = _open_private(None, journal, _APPEND_FLAGS, "journal")
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
        fd = _open_regular(None, journal, _READ_FLAGS, "journal")
    except StepFailed:
        if _lexists(journal):
            raise
        return []
    with os.fdopen(fd, "rb") as fh:
        return [_parse_line(n, line) for n, line in enumerate(fh, 1)]


def _parse_line(number: int, raw: bytes) -> dict[str, object]:
    try:
        row = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"journal line {number}: not UTF-8") from exc
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

    try:
        _execute(step, log, fail_after)
    except _Interrupted:
        raise
    except NeedsHuman as exc:
        _log_keeping(exc, log, "stopped", exc.instruction)
        raise
    except Exception as exc:
        _log_keeping(exc, log, "failed", f"{type(exc).__name__}: {exc}")
        raise


def _log_keeping(
    original: Exception, log: Callable[..., None], event: str, detail: str
) -> None:
    """Log; if logging fails, attach the error to `original` as a note."""
    try:
        log(event, detail)
    except Exception as log_exc:  # noqa: BLE001 - original wins
        original.add_note(f"журнал: не записано {event}: {log_exc}")


def _execute(step: Step, log: Callable[..., None], fail_after: str | None) -> None:
    if step.check():
        log("skip")
        return
    log("start")
    step.do()
    if fail_after == step.name:
        raise _Interrupted(step.name)
    if not step.check():
        raise StepFailed(step.name, "постусловие не выполнено")
    log("done")
