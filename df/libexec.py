"""The `libexec/` wrappers of stage 2a (spec 4.2; Task 4).

Each wrapper is a root-owned `0755` `sh` script of one `exec` line: the
administrative Python of the `current` record runs the stage-1 entry
(`bin/darkfactory-entry.py`, its contract unchanged) with a fixed subcommand
and the wrapper's own arguments. `sudo` and the roles run the wrappers, never
the Python itself, so the path of the interpreter is written here and nowhere
else. A new release with another admin Python makes them stale;
`wrapper_problems` shows it and `darkfactory roles setup` rewrites them.
"""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

from df import admin, fsutil
from df import platform as plat
from df.config import ReleaseEntry
from df.journal import StepFailed
from df.paths import Layout
from df.state import Snapshot

WRAPPER_MODE = 0o755
#: Paths relative to `Layout.opt`.
WRAPPERS: tuple[str, ...] = (
    "libexec/df-launch",
    "libexec/df-review-run",
    "libexec/df-publish",
    "bin/df-run-start",
)
#: The entry subcommand (with its fixed words) of each wrapper.
_SUBCOMMANDS = {
    "libexec/df-launch": "launch exec",
    "libexec/df-review-run": "launch exec df-review review",
    "libexec/df-publish": "publisher serve",
    # `start` or `resume` is the caller's first argument
    "bin/df-run-start": "run",
}
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_STEP = "libexec"


def wrapper_path(layout: Layout, name: str) -> Path:
    """Where wrapper `name` lives: through `Layout`, the one source of paths."""
    if name == "bin/df-run-start":
        return layout.run_start
    if name in WRAPPERS:
        return layout.libexec / name.removeprefix("libexec/")
    raise ValueError(f"not a wrapper: {name!r}")


def wrapper_text(layout: Layout, name: str, admin_python: Path) -> str:
    """The exact text of wrapper `name` (an entry of `WRAPPERS`)."""
    subcommand = _SUBCOMMANDS.get(name)
    if subcommand is None:
        raise ValueError(f"not a wrapper: {name!r}")
    python, entry = shlex.quote(str(admin_python)), shlex.quote(str(layout.entry))
    return f'#!/bin/sh\nexec {python} -I -B -S {entry} {subcommand} "$@"\n'


def admin_python_of(layout: Layout, snap: Snapshot) -> Path:
    """The admin Python of the `current` record; none is `StepFailed`."""
    entry: ReleaseEntry | None = snap.entry
    if snap.release_id is None or entry is None:
        raise StepFailed("release", "нет активного релиза: сначала darkfactory install")
    return admin.admin_python_path(layout, entry.admin_python, plat.detect_platform())


def install_wrappers(layout: Layout, admin_python: Path) -> None:
    """Write every wrapper atomically, root-owned `0755`."""
    for name in WRAPPERS:
        text = wrapper_text(layout, name, admin_python)
        path = wrapper_path(layout, name)
        try:
            fsutil.write_atomic(path, text, WRAPPER_MODE)
        except OSError as exc:
            raise StepFailed(_STEP, f"{path}: {exc}") from exc


def wrapper_problems(layout: Layout, admin_python: Path) -> list[str]:
    """Every wrapper: a regular root file, `0755`, the expected text, and a
    path that passes the trusted-path policy; empty means all hold."""
    problems: list[str] = []
    for name in WRAPPERS:
        path = wrapper_path(layout, name)
        text = wrapper_text(layout, name, admin_python)
        found = root_file_problems(path, text, WRAPPER_MODE)
        problems += found
        if not found:
            problems += plat.trusted_path_problems(path)
    return problems


def root_file_problems(path: Path, text: str, mode: int) -> list[str]:
    """`path` must be a regular file of root, exactly `mode`, holding `text`.

    Opened without following a link or blocking; what is read is bounded by
    the expected text, so a huge file costs nothing."""
    try:
        fd = os.open(path, _FLAGS)
    except FileNotFoundError:
        return [f"{path}: нет файла"]
    except OSError as exc:
        return [f"{path}: {exc.strerror or exc} (не обычный файл)"]
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return [f"{path}: не обычный файл"]
        problems = []
        if fsutil.owner_uid(st) != plat.ROOT_UID:
            problems.append(f"{path}: владелец uid {fsutil.owner_uid(st)}, не root")
        if stat.S_IMODE(st.st_mode) != mode:
            problems.append(
                f"{path}: права {stat.S_IMODE(st.st_mode):04o}, нужны {mode:04o}"
            )
        want = text.encode("utf-8")
        if os.read(fd, len(want) + 1) != want:
            problems.append(f"{path}: текст не совпадает с ожидаемым")
        return problems
    except OSError as exc:
        return [f"{path}: {exc.strerror or exc}"]
    finally:
        os.close(fd)
