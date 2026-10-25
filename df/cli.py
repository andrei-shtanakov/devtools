"""The administrative command `darkfactory` (spec 3.3, 3.9): `main`.

The entry (`df/launcher_entry.py`, Task 8a) or bootstrap (`df/bootstrap.py`)
calls `main(argv, mode=, read=, lock_fd=)`. A writer works from the snapshot
the entry read under the lock it holds; bootstrap takes the lock, settles
and reads the snapshot itself. Subcommands: `init` and `install` (Task 9);
`models list|set` (Task 10); `doctor --probe-models` (Task 11); `doctor
[--json]` (Task 12, `df.doctor`).

Exit codes (M7): 0 success; 2 a refusal or a configuration error, with the
step named; 3 `NeedsHuman` (the instruction on stderr); 1 a program error
with its traceback. `doctor`: 0 level 1 reached, 2 not reached (R68).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from df import (
    accept,
    doctor,
    fsutil,
    install,
    journal,
    launcher_entry,
    models,
    probe,
    state,
)
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

_TRANSITIONAL = doctor.TRANSITIONAL
_FINISH = "есть незавершённая установка: завершите darkfactory install"
_NO_PRODUCT = "продукт не зарегистрирован (этап 3)"
_NO_REGISTRY = "реестр продуктов не поддерживается до этапа 3"
_NO_INSTALL = "нет /etc/df/install.toml: начните с bootstrap init"
_UNSET = "—"
_LIST_COLUMNS = ("роль", "harness", "model", "effort", "источник", "проверка")
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
    if "--" in argv:  # before argparse, which would read past it (I-2)
        raise _form_error(argv)
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
    sub = getattr(args, "models_command", None)
    words = (args.command,) if sub is None else (args.command, sub)
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
        raise _form_error(argv)
    if mode == "reader" and wanted == "writer":
        raise Refusal(
            f"{command}: команда-писатель запущена без блокировки (режим reader)"
        )
    if mode == "writer" and wanted != "writer":
        raise UsageError(f"ошибка программы: {command} в режиме writer")
    if mode == "bootstrap" and command not in ("init", "install"):
        raise Refusal("bootstrap принимает только init или install")


def _form_error(argv: list[str]) -> UsageError:
    return UsageError(
        f"{' '.join(argv)!r}: форма команды не поддерживается "
        "(подкоманда — первым словом, без --)"
    )


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
    _add_models(sub)
    doctor = sub.add_parser(
        "doctor", exit_on_error=False, add_help=False, allow_abbrev=False
    )
    doctor.add_argument("--probe-models", action="store_true")
    doctor.add_argument("--product", type=_repository_id)
    doctor.add_argument("--json", action="store_true")
    try:
        return parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        raise UsageError(str(exc)) from exc


def _add_models(sub: argparse._SubParsersAction) -> None:  # noqa: SLF001
    """`models set <role> --harness H --model M [--effort E] [--param k=v]...
    [--product ID]` and `models list [--product ID] [--json]`."""
    opts = {"exit_on_error": False, "add_help": False, "allow_abbrev": False}
    group = sub.add_parser("models", **opts)
    words = group.add_subparsers(
        dest="models_command", required=True, parser_class=_Parser
    )
    put = words.add_parser("set", **opts)
    put.add_argument("role")
    put.add_argument("--harness", required=True)
    put.add_argument("--model", required=True)
    put.add_argument("--effort")
    put.add_argument("--param", action="append", default=[], metavar="K=V")
    put.add_argument("--product", type=_repository_id)
    show = words.add_parser("list", **opts)
    show.add_argument("--product", type=_repository_id)
    show.add_argument("--json", action="store_true")


def _repository_id(text: str) -> int:
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise ValueError(text)
    return int(text)


# ---------- reader ----------


def _reader(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    if args.command == "doctor":
        return _doctor(args, lay, read)
    if isinstance(read, Transitional):
        raise Refusal(_TRANSITIONAL)
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    return _models_list(args, lay, snap)


def _doctor(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`doctor [--json]` (Task 12) or `doctor --probe-models` (Task 11).

    `--probe-models` with `--json` is a usage error in v1; plain `doctor
    --product` is refused before any read (R57/R64, R70)."""
    if args.probe_models:
        if args.json:
            raise UsageError("doctor --probe-models: --json не поддерживается")
        return _probe_models(args, lay, read)
    if args.product is not None:
        raise Refusal(_NO_REGISTRY)
    return doctor.doctor_main(read, lay, euid=os.geteuid(), as_json=args.json)


def _probe_models(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`doctor --probe-models` (spec 3.6, R63): root only; the models text and
    the install configuration are the snapshot's (I2). Each role with a
    choice is probed once; records go to `probes_dir` (not install state, so
    no generation bracket). `--product` is refused until the registry lands
    (R64). Each role's outcome is printed; the exit code is 0 only when at
    least one probe ran and no role ended in an exception — a run where
    every role was refused («не проверено» / «не поддерживается») probed
    nothing and is a refusal, 2 (M7; PR #630 review)."""
    if os.geteuid() != ROOT_UID:
        raise Refusal("doctor --probe-models: нужен sudo")
    if isinstance(read, Transitional):
        raise Refusal(_TRANSITIONAL)
    if args.product is not None:
        raise Refusal(_NO_REGISTRY)
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    if snap.install is None:
        raise Refusal(_NO_INSTALL)
    cfg = _models_of(snap)
    probe.prepare_dirs(lay)
    failed = probed = False
    for role in models.ROLES:
        try:
            models.resolve(cfg, role, None)
        except ConfigError:
            print(f"{role}: модель не задана")
            continue
        try:
            # the runner is looked up now, not bound at import (the tests' seam)
            result = probe.run_probe(
                role, None, cfg, snap.install, lay, runner=probe.run_as
            )
        except Exception as exc:  # noqa: BLE001 - this role only; the rest go on
            failed = True
            print(f"{role}: ошибка пробы: {type(exc).__name__}: {exc}")
            continue
        probed = probed or isinstance(result, probe.ProbeRecord)
        print(f"{role}: {_probe_line(result)}")
    return EXIT_OK if probed and not failed else EXIT_REFUSED


def _probe_line(result: probe.ProbeRecord | str) -> str:
    if isinstance(result, str):
        return result
    reason = "" if result.reason is None else f" ({result.reason})"
    model = result.actual_model or "не сообщена"
    return (
        f"{result.outcome}{reason}; фактическая модель {model}; "
        f"auth_tracking {result.auth_tracking}"
    )


def _models_of(snap: Snapshot) -> models.ModelsConfig:
    text = snap.models_text
    return models.EMPTY if text is None else models.parse_models(text)


def _models_list(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """Every role in `ROLES` order from the snapshot's text (I2); a role
    without a choice is shown unset (R58). `--product` is refused until the
    registry lands (stage 3): a reader reads no registry, so it does not claim
    the product is unregistered (R57)."""
    if args.product is not None:
        raise Refusal(_NO_REGISTRY)
    cfg = _models_of(snap)
    root = os.geteuid() == ROOT_UID
    targets: dict[Path, tuple[Path, str] | str] = {}
    rows = [_list_row(cfg, role, lay, snap, root, targets) for role in models.ROLES]
    if args.json:
        print(json.dumps(rows, sort_keys=True, ensure_ascii=False, indent=2))
        return EXIT_OK
    table = [_LIST_COLUMNS, *(_list_cells(row) for row in rows)]
    widths = [max(len(line[i]) for line in table) for i in range(len(_LIST_COLUMNS))]
    for line in table:
        print("  ".join(c.ljust(n) for c, n in zip(line, widths, strict=True)).rstrip())
    return EXIT_OK


def _list_row(
    cfg: models.ModelsConfig,
    role: str,
    lay: Layout,
    snap: Snapshot,
    root: bool,
    targets: dict[Path, tuple[Path, str] | str],
) -> dict[str, object]:
    """One role's row; the probe status from its record in `probes_dir`
    (R65: a non-root reader cannot observe the auth, so a record shows
    «данные недоступны: нужен sudo»)."""
    unset: dict[str, object] = {
        "role": role,
        "harness": None,
        "model": None,
        "effort": None,
        "params": None,
        "source": None,
        "status": probe.NOT_CHECKED,
        "current": False,
        "auth_tracking": None,
    }
    try:
        choice, source = models.resolve(cfg, role, None)
    except ConfigError:
        return unset
    status, rec = probe.current_status(
        lay, role, choice, snap.install, root=root, targets=targets
    )
    return {
        **unset,
        "harness": choice.harness,
        "model": choice.model,
        "effort": choice.effort,
        "params": dict(choice.params),
        "source": source,
        "status": status.text,
        "current": status.current,
        "auth_tracking": None if rec is None else rec.auth_tracking,
    }


def _list_cells(row: dict[str, object]) -> tuple[str, ...]:
    source = "не задано" if row["source"] is None else str(row["source"])
    values = (row["role"], row["harness"], row["model"], row["effort"])
    cells = (_UNSET if v is None else str(v) for v in values)
    return (*cells, source, str(row["status"]))


# ---------- writer ----------


def _writer(
    args: argparse.Namespace, lay: Layout, raw: state.RawSnapshot, mode: Mode
) -> int:
    snap = state.parse_snapshot(raw, lay, mode=mode)
    pending = install.read_pending(lay)
    if (pending is not None) != snap.pending_present:
        raise StepFailed("snapshot", "pending.toml не совпадает со снимком")
    # G1 (I9) before anything else of a writer: a refused run changes nothing
    refusal = install.unfinished_damage(lay, snap, pending)
    if refusal is not None:
        raise StepFailed("damaged", refusal)
    if snap.pending_present and args.command not in PENDING_ALLOWED:
        raise Refusal(_FINISH)
    if args.command == "models":
        return _models_set(args, lay, snap)
    requested = _requested(args, snap, pending)
    if mode == "bootstrap" and _healthy(lay, snap):
        raise Refusal(
            f"команда исправна (релиз {snap.release_id}): используйте darkfactory"
        )
    _close_odd_generation(lay, snap)
    release_id = install.run_install(lay, requested, snap, pending)
    print(f"darkfactory: установлен релиз {release_id}; команда: {_command(lay)}")
    return EXIT_OK


def _models_set(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """Replace one role's common choice in `models.toml` (spec 3.6).

    The current text is the snapshot's, read under the lock this writer
    holds (R56); everything is checked before the generation opens, so a
    refusal changes nothing. One `write_atomic` between `generation_begin`
    and `generation_end`; the other entries stay as they were.
    """
    if args.product is not None:
        registry = os.path.lexists(lay.etc / "products.toml")
        raise Refusal(_NO_REGISTRY if registry else _NO_PRODUCT)
    choice = models.ModelChoice(
        args.harness, args.model, args.effort, _params(args.param)
    )
    text = snap.models_text
    current = models.EMPTY if text is None else models.parse_models(text)
    cfg = models.with_choice(current, args.role, choice)
    _close_odd_generation(lay, snap)
    journal.generation_begin(lay)
    try:
        fsutil.write_atomic(lay.etc / "models.toml", models.dump_models(cfg))
    except OSError:
        # unlike install, no `pending.toml` marks this bracket: a refused
        # write leaves the old or the new file whole, so it closes here (a
        # crash does not get here; the next writer closes it, M9)
        journal.generation_end(lay)
        raise
    journal.generation_end(lay)
    effort = "" if choice.effort is None else f" ({choice.effort})"
    print(
        f"darkfactory: {args.role}: {choice.harness} {choice.model}{effort}, "
        f"источник {models.COMMON}"
    )
    return EXIT_OK


def _params(items: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or key in params:
            raise UsageError(f"--param {item!r}: нужно K=V, каждый ключ один раз")
        params[key] = value
    return params


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
        raise Refusal(_NO_INSTALL)
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
    it and `generation_end`) protects nothing — made even, with a `warn`
    naming `current`. G3 (I9): called only after G1 passed, i.e. only in
    the consistent state, so it never erases the evidence of an install
    closed by hand halfway."""
    if snap.generation % 2 == 0 or snap.pending_present:
        return
    journal.generation_end(lay)
    journal.journal_event(
        lay.journal,
        run_id="generation",
        step="generation",
        event="warn",
        detail=(
            f"нечётное поколение {snap.generation} без pending.toml: закрыто, "
            f"current = {snap.release_id or 'нет'}"
        ),
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
