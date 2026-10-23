"""The administrative command `darkfactory` (spec 3.3, 3.9): `main`.

The entry (`df/launcher_entry.py`, Task 8a) or bootstrap (`df/bootstrap.py`)
calls `main(argv, mode=, read=, lock_fd=)`. A writer works from the snapshot
the entry read under the lock it holds; bootstrap takes the lock, settles
and reads the snapshot itself. Subcommands: `init` and `install` (Task 9);
`doctor` (Task 12) and `models` (Task 10) are refused here until they land.

Exit codes (M7): 0 success; 2 a refusal or a configuration error, with the
step named; 3 `NeedsHuman` (the instruction on stderr); 1 a program error
with its traceback.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from df import accept, install, journal, launcher_entry, state
from df.config import ConfigError, InstallConfig, Pending, parse_install
from df.digest import TreeError
from df.journal import NeedsHuman, Stable, StepFailed, Transitional
from df.manifest import ManifestError
from df.paths import Layout, default_layout
from df.sources import UnsupportedLock
from df.state import Mode, ReadResult, Snapshot

EXIT_OK = 0
EXIT_BUG = 1
EXIT_REFUSED = 2
EXIT_HUMAN = 3
#: Owner of root's objects (M14); tests substitute their own uid.
ROOT_UID = 0
#: Subcommands allowed while `pending.toml` exists (spec 3.3).
PENDING_ALLOWED = frozenset({"init", "install", "doctor"})
#: Subcommands that land in later tasks: refused at dispatch (M19).
LATER = {"doctor": "Task 12", "models": "Task 10"}

_TRANSITIONAL = "идёт или прервана административная операция: darkfactory install"
_FINISH = "есть незавершённая установка: завершите darkfactory install"
_REFUSALS = (
    StepFailed,
    TreeError,
    ConfigError,
    ManifestError,
    UnsupportedLock,
    OSError,
)


class UsageError(Exception):
    """The arguments or the call itself are refused (exit 2)."""


class Refusal(Exception):
    """A refusal of the command with its message (exit 2)."""


class _Parser(argparse.ArgumentParser):
    """`argparse` whose errors are `UsageError`, never `sys.exit` (N1)."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def main(
    argv: Sequence[str],
    *,
    mode: Mode,
    read: ReadResult | None,
    lock_fd: int | None,
    layout: Layout | None = None,
) -> int:
    """Run `darkfactory <argv>`; return the exit code (M7).

    `mode`, `read` and `lock_fd` come from the entry (writer: a lock fd and
    `Stable`; reader: no lock) or from bootstrap (`read` and `lock_fd` are
    `None`); any other combination is a program error, exit 2. `layout` is
    the test seam; production is `default_layout()`.
    """
    lay = layout if layout is not None else default_layout()
    try:
        return _main(list(argv), mode, read, lock_fd, lay)
    except NeedsHuman as exc:
        _say(exc.instruction)
        return EXIT_HUMAN
    except (Refusal, UsageError) as exc:
        _say(str(exc))
        return EXIT_REFUSED
    except StepFailed as exc:
        _say(str(exc))
        return EXIT_REFUSED
    except _REFUSALS as exc:
        _say(f"{type(exc).__name__}: {exc}")  # no step of its own (m3)
        return EXIT_REFUSED
    except Exception:  # noqa: BLE001 - a program error: traceback, exit 1
        traceback.print_exc()
        return EXIT_BUG


def _main(
    argv: list[str],
    mode: Mode,
    read: ReadResult | None,
    lock_fd: int | None,
    lay: Layout,
) -> int:
    _check_call(mode, read, lock_fd)
    args = _parse(argv)
    _check_mode(argv, mode, args)
    if launcher_entry.mode_of(argv) == "writer" and os.geteuid() != ROOT_UID:
        raise Refusal("команда-писатель требует root: запустите через sudo")
    # `init`'s configuration is checked before the lock creates anything (m5)
    args.config = _init_config(args) if args.command == "init" else None
    if mode == "bootstrap":
        with journal.writer_lock(lay):
            accept.settle_on_acquire(lay)
            raw = state.read_raw(lay)
            return _writer(args, lay, raw, mode)
    if mode == "writer":
        assert isinstance(read, Stable)
        return _writer(args, lay, read.value, mode)
    assert read is not None
    return _reader(args, lay, read)


def _check_call(mode: Mode, read: ReadResult | None, lock_fd: int | None) -> None:
    ok = {
        "writer": lock_fd is not None and isinstance(read, Stable),
        "reader": lock_fd is None and isinstance(read, (Stable, Transitional)),
        "bootstrap": lock_fd is None and read is None,
    }.get(mode, False)
    if not ok:
        raise UsageError(
            f"ошибка программы: режим {mode!r} с read={type(read).__name__}, "
            f"lock_fd={lock_fd!r}"
        )


def parsed_mode(args: argparse.Namespace) -> str:
    """The mode the parsed command needs: `writer` for `init`, `install` and
    `models set` (`WRITER_COMMANDS`), else `reader`."""
    words = (args.command, *getattr(args, "rest", ())[:1])
    writer = any(words[: len(cmd)] == cmd for cmd in launcher_entry.WRITER_COMMANDS)
    return "writer" if writer else "reader"


def _check_mode(argv: list[str], mode: Mode, args: argparse.Namespace) -> None:
    """The subcommand's mode is the entry's (`mode_of`, (c)).

    I-2: the mode is decided on the raw argv (`mode_of`, as the entry does)
    but the dispatch is argparse's; any disagreement between the two, and
    any `--`, is refused before the euid check and any dispatch.
    """
    command = args.command
    wanted = launcher_entry.mode_of(argv)
    if "--" in argv or parsed_mode(args) != wanted:
        raise UsageError(
            f"{' '.join(argv)!r}: форма команды не поддерживается "
            "(подкоманда — первым словом, без --)"
        )
    if mode == "reader" and wanted == "writer":
        raise Refusal(
            f"{command}: команда-писатель запущена без блокировки (режим reader)"
        )
    if mode == "writer" and wanted != "writer":
        raise UsageError(f"ошибка программы: {command} в режиме writer")
    if mode == "bootstrap" and command not in ("init", "install"):
        raise Refusal("bootstrap принимает только init или install")


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(
        prog="darkfactory", exit_on_error=False, add_help=False, allow_abbrev=False
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    init = sub.add_parser(
        "init", exit_on_error=False, add_help=False, allow_abbrev=False
    )
    init.add_argument("--manifest-repo", required=True)
    init.add_argument("--manifest-sha", required=True)
    init.add_argument("--profile", required=True)
    init.add_argument(
        "--cli", action="append", nargs="+", required=True, metavar="NAME=PATH"
    )
    sub.add_parser("install", exit_on_error=False, add_help=False, allow_abbrev=False)
    for name in LATER:
        later = sub.add_parser(
            name, exit_on_error=False, add_help=False, allow_abbrev=False
        )
        later.add_argument("rest", nargs=argparse.REMAINDER)
    try:
        return parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        raise UsageError(str(exc)) from exc


# ---------- reader ----------


def _reader(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    if isinstance(read, Transitional):
        if args.command == "doctor":
            return _later(args.command)
        raise Refusal(_TRANSITIONAL)
    state.parse_snapshot(read.value, lay, mode="reader")
    return _later(args.command)


def _later(command: str) -> int:
    task = LATER.get(command)
    if task is None:
        raise Refusal(f"{command}: нет такой подкоманды здесь")
    raise Refusal(f"{command}: ещё не реализовано ({task})")


# ---------- writer ----------


def _writer(
    args: argparse.Namespace, lay: Layout, raw: state.RawSnapshot, mode: Mode
) -> int:
    snap = state.parse_snapshot(raw, lay, mode=mode)
    pending = install.read_pending(lay)
    if (pending is not None) != snap.pending_present:
        raise StepFailed("snapshot", "pending.toml не совпадает со снимком")
    if snap.pending_present and args.command not in PENDING_ALLOWED:
        raise Refusal(_FINISH)
    if args.command not in ("init", "install"):
        return _later(args.command)
    requested = _requested(args, snap, pending)
    if mode == "bootstrap" and _healthy(lay, snap):
        raise Refusal(
            f"команда исправна (релиз {snap.release_id}): используйте darkfactory"
        )
    _close_odd_generation(lay, snap)
    release_id = install.run_install(lay, requested, snap, pending)
    print(f"darkfactory: установлен релиз {release_id}; команда: {_command(lay)}")
    return EXIT_OK


def _requested(
    args: argparse.Namespace, snap: Snapshot, pending: Pending | None
) -> InstallConfig:
    """The configuration asked for: `init`'s arguments; `install`'s is
    `pending.toml`'s, else `install.toml`'s."""
    if args.command == "init":
        if snap.install is not None:
            raise Refusal("установка уже есть: darkfactory install")
        return args.config
    if pending is not None:
        return parse_install(pending.install_text)
    if snap.install is None:
        raise Refusal("нет /etc/df/install.toml: начните с bootstrap init")
    return snap.install


def _init_config(args: argparse.Namespace) -> InstallConfig:
    cli: dict[str, Path] = {}
    for item in (i for group in args.cli for i in group):
        name, sep, path = item.partition("=")
        if not sep or not name or not path or name in cli:
            raise UsageError(f"--cli {item!r}: нужно NAME=PATH, каждое имя один раз")
        cli[name] = Path(path)
    return InstallConfig(args.manifest_repo, args.manifest_sha, args.profile, cli)


def _healthy(lay: Layout, snap: Snapshot) -> bool:
    """Bootstrap refuses a healthy command (M16): no `pending.toml`, an
    active release, its entry `allowed`, `intact` and `command_ready` — all
    from the snapshot's values."""
    entry, rid = snap.entry, snap.release_id
    if snap.pending_present or rid is None or entry is None:
        return False
    return entry.status == "allowed" and install.command_ready_of(lay, rid, entry)


def _close_odd_generation(lay: Layout, snap: Snapshot) -> None:
    """M9: an odd generation without `pending.toml` (a crash between removing
    it and `generation_end`) protects nothing — made even, with a `warn`."""
    if snap.generation % 2 == 0 or snap.pending_present:
        return
    journal.generation_end(lay)
    journal.journal_event(
        lay.journal,
        run_id="generation",
        step="generation",
        event="warn",
        detail=f"нечётное поколение {snap.generation} без pending.toml: закрыто",
    )


def _command(lay: Layout) -> Path:
    """The command's path to print: the `/usr/local/bin` link if it is ours,
    else the full path (spec 3.9)."""
    link = lay.local_bin / lay.launcher.name
    try:
        if os.readlink(link) == str(lay.launcher):
            return link
    except OSError:
        pass
    return lay.launcher


def _say(message: str) -> None:
    print(f"darkfactory: {message}", file=sys.stderr)
