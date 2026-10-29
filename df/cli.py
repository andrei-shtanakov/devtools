"""The administrative command `darkfactory` (spec 3.3, 3.9): `main`.

The entry (`df/launcher_entry.py`, Task 8a) or bootstrap (`df/bootstrap.py`)
calls `main(argv, mode=, read=, lock_fd=)`; the entry must also hand over
the layout of its own root (`layout=`, D2), bootstrap may omit it (then
`default_layout()`). A writer works from
the snapshot the entry read under the lock it holds; bootstrap takes the
lock, settles and reads the snapshot itself. Subcommands: `init` and
`install` (Task 9); `models list|set` (Task 10); `doctor --probe-models`
(Task 11); `doctor [--product P [--operator NAME]] [--json]` (Tasks 12, 13,
`df.doctor`); `cli set openssl PATH` (stage 2a, Task 4a); `perimeter
restore --from PATH | --empty [--set-aside-old]` (stage 2a, Task 11r); `setup-agent app-key --app-id N
--file PATH | machine-token --file PATH` (stage 2a, Task 12).

Exit codes (M7): 0 success; 2 a refusal or a configuration error, with the
step named; 3 `NeedsHuman` (the instruction on stderr); 1 a program error
with its traceback. `doctor`: 0 level 1 reached, 2 not reached (R68); with
`--product P` the same for level 2 (R13-5).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from df import (
    accept,
    admin,
    doctor,
    fsutil,
    inputs,
    install,
    journal,
    launch,
    launcher_entry,
    libexec,
    models,
    probe,
    roles,
    roles_setup,
    run_start,
    setup_agent,
    state,
)
from df import platform as plat
from df.config import (
    ConfigError,
    InstallConfig,
    Pending,
    dump_install,
    load_accounts,
    parse_install,
)
from df.digest import TreeError
from df.journal import NeedsHuman, Stable, StepFailed, Transitional
from df.manifest import ManifestError
from df.paths import MACHINE_TOKEN, Layout, default_layout
from df.publisher import main as publisher_main
from df.publisher import perimeter
from df.sources import UnsupportedLock
from df.stage2_config import OPERATIONS
from df.state import Mode, ReadResult, Snapshot

EXIT_OK = 0
EXIT_BUG = 1
EXIT_REFUSED = 2
EXIT_HUMAN = 3
#: Owner of root's objects (M14); tests substitute their own uid.
ROOT_UID = 0
#: Subcommands allowed while `pending.toml` exists (spec 3.3).
PENDING_ALLOWED = frozenset({"init", "install", "doctor"})

_FINISH = "есть незавершённая установка: завершите darkfactory install"
_NO_PRODUCT = "продукт не зарегистрирован (этап 3)"
_NO_REGISTRY = "реестр продуктов не поддерживается до этапа 3"
_NO_INSTALL = "нет /etc/df/install.toml: начните с bootstrap init"
_STALE_WRAPPERS = "обёртки ролей устарели: выполните darkfactory roles setup"
_NO_OPERATOR = "roles setup: укажите --operator (SUDO_USER не задан)"
#: The one approved CLI `cli set` may add to an existing install (owner 27.10).
CLI_SETTABLE = "openssl"
_UNSET = "—"
#: Subcommands of the roles' side (Tasks 5-7); readers.
SERVICE_COMMANDS = frozenset({"publisher"})
#: What `publisher serve` takes: the five operations and the self-check.
PUBLISHER_ARGS = (*OPERATIONS, "self-check")
#: Seconds root waits for `publisher perimeter-restore` (the barrier wait,
#: a copy of up to `perimeter.MAX_COPY` and its integrity check).
RESTORE_TIMEOUT = 600.0
_RESTORE_DONE = {
    "restored": (
        "база периметра восстановлена: admission = restored_pending; в работу "
        "она входит после консервативного события на каждый корень, когда "
        "эпохи доступны (до того — unavailable: restore_pending)"
    ),
    "resumed": (
        "завершено прерванное ранее восстановление --set-aside-old: на месте "
        "копия, указанная в прерванном вызове (admission = restored_pending); "
        "источник этого вызова не применялся. Если прерванный вызов был с тем "
        "же источником, повторять не нужно; иначе повторите команду со своим"
    ),
}
_SETUP_FAILED = "секрет записан, проверка не прошла: "
_NOT_DURABLE = "записан, но не закреплён: повторите команду"
_GEN_OPEN = "секрет записан; нечётное поколение не закрыто"


class _GenerationNotClosed(Exception):
    """`setup-agent`: the secret is written, closing the generation failed."""


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
    the entry's layout of its own root (D2: one root per process); only
    bootstrap, which has no entry, resolves `default_layout()`.
    """
    try:
        return _main(list(argv), mode, read, lock_fd, layout)
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
    layout: Layout | None,
) -> int:
    _check_call(mode, read, lock_fd, layout)
    lay = default_layout() if layout is None else layout
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


def _check_call(
    mode: Mode, read: ReadResult | None, lock_fd: int | None, layout: Layout | None
) -> None:
    """The entry's call must carry its layout; bootstrap's may omit it (then
    `default_layout()`, D2)."""
    ok = {
        "writer": lock_fd is not None and isinstance(read, Stable),
        "reader": lock_fd is None and isinstance(read, (Stable, Transitional)),
        "bootstrap": lock_fd is None and read is None,
    }.get(mode, False)
    if not ok or (mode != "bootstrap" and layout is None):
        raise UsageError(
            f"ошибка программы: режим {mode!r} с read={type(read).__name__}, "
            f"lock_fd={lock_fd!r}, layout={layout!r}"
        )


def parsed_mode(args: argparse.Namespace) -> str:
    """The mode the parsed command needs: `writer` for `init`, `install`,
    `models set`, `cli set`, `roles setup`, `setup-agent` and `perimeter
    restore` (`WRITER_COMMANDS`), else `reader`."""
    sub = (
        getattr(args, "models_command", None)
        or getattr(args, "roles_command", None)
        or getattr(args, "cli_command", None)
        or getattr(args, "perimeter_command", None)
        or getattr(args, "setup_command", None)
    )
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
    _add_cli(sub)
    _add_perimeter(sub)
    _add_setup_agent(sub)
    _add_service(sub)
    doctor = sub.add_parser(
        "doctor", exit_on_error=False, add_help=False, allow_abbrev=False
    )
    doctor.add_argument("--probe-models", action="store_true")
    doctor.add_argument("--product", type=models.repository_id)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--operator")
    try:
        args = parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        raise UsageError(str(exc)) from exc
    if args.command == "launch":
        _check_launch(args)
    if args.command == "run":
        _check_run(args)
    return args


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
    put.add_argument("--product", type=models.repository_id)
    show = words.add_parser("list", **opts)
    show.add_argument("--product", type=models.repository_id)
    show.add_argument("--json", action="store_true")


def _add_cli(sub: argparse._SubParsersAction) -> None:  # noqa: SLF001
    """`cli set NAME PATH`; the name is checked by `_cli_set`, not here, so
    that another name gets its own message."""
    opts = {"exit_on_error": False, "add_help": False, "allow_abbrev": False}
    group = sub.add_parser("cli", **opts)
    words = group.add_subparsers(
        dest="cli_command", required=True, parser_class=_Parser
    )
    put = words.add_parser("set", **opts)
    put.add_argument("name")
    put.add_argument("path")


def _add_perimeter(sub: argparse._SubParsersAction) -> None:  # noqa: SLF001
    """`perimeter restore --from PATH | --empty [--set-aside-old]` (writer)."""
    opts = {"exit_on_error": False, "add_help": False, "allow_abbrev": False}
    group = sub.add_parser("perimeter", **opts)
    words = group.add_subparsers(
        dest="perimeter_command", required=True, parser_class=_Parser
    )
    put = words.add_parser("restore", **opts)
    source = put.add_mutually_exclusive_group(required=True)
    source.add_argument("--from", dest="source", metavar="PATH")
    source.add_argument("--empty", action="store_true")
    put.add_argument("--set-aside-old", action="store_true")


def _add_setup_agent(sub: argparse._SubParsersAction) -> None:  # noqa: SLF001
    """`setup-agent app-key --app-id N --file PATH` and `setup-agent
    machine-token --file PATH` (writers)."""
    opts = {"exit_on_error": False, "add_help": False, "allow_abbrev": False}
    group = sub.add_parser("setup-agent", **opts)
    words = group.add_subparsers(
        dest="setup_command", required=True, parser_class=_Parser
    )
    key = words.add_parser("app-key", **opts)
    key.add_argument("--app-id", required=True, type=_app_id)
    key.add_argument("--file", required=True)
    words.add_parser("machine-token", **opts).add_argument("--file", required=True)


def _app_id(text: str) -> int:
    """A canonical positive decimal."""
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise argparse.ArgumentTypeError(f"--app-id {text!r}: нужно целое > 0")
    return int(text)


def _add_service(sub: argparse._SubParsersAction) -> None:  # noqa: SLF001
    """`roles setup [--operator NAME]` (writer); `launch [exec] <role> <op>`,
    `publisher serve <op>` and `run start|resume` (readers; the wrappers of
    `df.libexec` call them, one exact shape each); `publisher perimeter-init`
    and `publisher perimeter-restore (--stdin | --empty) [--set-aside-old]`
    (readers as `df-publisher`, which `roles setup` and `perimeter restore`
    run; no wrapper or sudoers rule reaches them)."""
    opts = {"exit_on_error": False, "add_help": False, "allow_abbrev": False}
    group = sub.add_parser("roles", **opts)
    words = group.add_subparsers(
        dest="roles_command", required=True, parser_class=_Parser
    )
    words.add_parser("setup", **opts).add_argument("--operator")
    publisher = sub.add_parser("publisher", **opts)
    words = publisher.add_subparsers(
        dest="publisher_command", required=True, parser_class=_Parser
    )
    words.add_parser("serve", **opts).add_argument("operation", choices=PUBLISHER_ARGS)
    words.add_parser("perimeter-init", **opts)
    put = words.add_parser("perimeter-restore", **opts)
    source = put.add_mutually_exclusive_group(required=True)
    source.add_argument("--stdin", action="store_true")
    source.add_argument("--empty", action="store_true")
    put.add_argument("--set-aside-old", action="store_true")
    sub.add_parser("run", **opts).add_argument("words", nargs=argparse.REMAINDER)
    sub.add_parser("launch", **opts).add_argument("words", nargs="+")


def _check_launch(args: argparse.Namespace) -> None:
    """`launch exec <role> <op>` or `launch <role> <op>`: exactly that."""
    words: list[str] = args.words
    args.exec_form = words[0] == "exec"
    rest = words[1:] if args.exec_form else words
    if len(rest) != 2:
        raise UsageError(f"launch: нужно [exec] <роль> <операция>, получено {words}")
    args.role, args.operation = rest
    if args.role not in roles.MODEL_ROLE_USERS:
        raise UsageError(f"launch: неизвестная роль {args.role!r}")
    if args.operation not in launch.ROLE_OPS[args.role]:
        raise UsageError(f"launch: {args.role}: неверная операция {args.operation!r}")
    reason = None if args.exec_form else launch.human_refusal(args.role, args.operation)
    if reason is not None:
        raise UsageError(f"launch: {reason}")


def _check_run(args: argparse.Namespace) -> None:
    """`run start --product ID` or `run resume RUN_ID [--accept-model-change
    --from-step N]`: exactly that, judged by `run_start.parse`."""
    try:
        run_start.parse(args.words)
    except StepFailed as exc:
        raise UsageError(f"run: {exc.detail}") from exc


# ---------- reader ----------


def _reader(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    if args.command == "launch":
        return _launch(args, lay, read)
    if args.command == "run":
        return _run(args, lay, read)
    if args.command in SERVICE_COMMANDS:
        return _publisher(args, lay, read)
    if args.command == "doctor":
        return _doctor(args, lay, read)
    if isinstance(read, Transitional):
        raise Refusal(doctor.transitional_text(read.generation))
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    return _models_list(args, lay, snap)


def _launch(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`launch exec <role> <op>` from the one snapshot; `launch <role> <op>`
    (the human's entry) needs none."""
    words = [args.role, args.operation]
    if not args.exec_form:
        return launch.human_main(words, layout=lay)
    if isinstance(read, Transitional):
        raise Refusal(doctor.transitional_text(read.generation))
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    return launch.exec_main(words, layout=lay, snap=snap)


def _run(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`run start|resume`: root registers and hands over; anyone else is the
    human, whose `sudo` runs the same wrapper as root."""
    if os.geteuid() != ROOT_UID:
        return run_start.human_main(args.words, layout=lay)
    if isinstance(read, Transitional):
        raise Refusal(doctor.transitional_text(read.generation))
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    return run_start.main(args.words, layout=lay, snap=snap)


def _publisher(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`publisher serve <op>` as `df-publisher`, from the one snapshot."""
    if args.publisher_command == "perimeter-init":
        return _perimeter_init(lay)
    if args.publisher_command == "perimeter-restore":
        return _perimeter_restore_role(args, lay)
    if isinstance(read, Transitional):
        raise Refusal(doctor.transitional_text(read.generation))
    snap = state.parse_snapshot(read.value, lay, mode="reader")
    return publisher_main.main([args.operation], layout=lay, snapshot=snap)


_PERIMETER_TEXTS = {
    "lost": perimeter.LOST_TEXT,
    "integrity": perimeter.INTEGRITY_TEXT,
}


def _require_publisher(lay: Layout, command: str) -> None:
    """Only the `df-publisher` of `accounts.toml` (its files are born the
    role's)."""
    account = load_accounts(lay.accounts).get(publisher_main.PUBLISHER_USER)
    if account is None or account.state != "ready" or os.geteuid() != account.uid:
        raise Refusal(f"publisher {command}: только от учётки df-publisher")


def _perimeter_init(lay: Layout) -> int:
    """`publisher perimeter-init`: the perimeter database, as `df-publisher`."""
    _require_publisher(lay, "perimeter-init")
    try:
        outcome = perimeter.create_db(lay)
    except perimeter.Unavailable as exc:
        text = _PERIMETER_TEXTS.get(exc.reason, f"perimeter-init: {exc.reason}")
        raise Refusal(text) from exc
    print(f"perimeter-init: {outcome}")
    return EXIT_OK


def _perimeter_restore_role(args: argparse.Namespace, lay: Layout) -> int:
    """`publisher perimeter-restore`: `perimeter.restore` as `df-publisher`;
    the copy comes on stdin (`--stdin`), at most `MAX_COPY` bytes are read
    (one more is a refusal of `restore`). Only root runs it (`perimeter
    restore`, through `run_as`)."""
    _require_publisher(lay, "perimeter-restore")
    copy = sys.stdin.buffer.read(perimeter.MAX_COPY + 1) if args.stdin else None
    try:
        outcome = perimeter.restore(lay, copy, set_aside_old=args.set_aside_old)
    except perimeter.Unavailable as exc:
        raise Refusal(perimeter.restore_text(exc.reason)) from exc
    print(f"perimeter-restore: {outcome}")
    return EXIT_OK


def _doctor(args: argparse.Namespace, lay: Layout, read: ReadResult) -> int:
    """`doctor [--product P [--operator NAME]] [--json]` (Tasks 12, 13) or
    `doctor --probe-models` (Task 11).

    `--probe-models` with `--json` is a usage error in v1. `--product P` adds
    level 2 of P; the operator is `--operator`, else `SUDO_USER`, as for
    `roles setup` (`_given_operator`, R13-10); without `--product` it is not
    used."""
    if args.probe_models:
        if args.json:
            raise UsageError("doctor --probe-models: --json не поддерживается")
        return _probe_models(args, lay, read)
    return doctor.doctor_main(
        read,
        lay,
        euid=os.geteuid(),
        as_json=args.json,
        product=args.product,
        operator=_given_operator(args),
    )


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
        raise Refusal(doctor.transitional_text(read.generation))
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
    if args.command == "roles":
        return _roles_setup(args, lay, snap)
    if args.command == "cli":
        return _cli_set(args, lay, snap)
    if args.command == "perimeter":
        return _perimeter_restore(args, lay, snap)
    if args.command == "setup-agent":
        return _setup_agent(args, lay, snap)
    requested = _requested(args, snap, pending)
    if mode == "bootstrap" and _healthy(lay, snap):
        raise Refusal(
            f"команда исправна (релиз {snap.release_id}): используйте darkfactory"
        )
    _close_odd_generation(lay, snap)
    release_id = install.run_install(lay, requested, snap, pending)
    print(f"darkfactory: установлен релиз {release_id}; команда: {_command(lay)}")
    _warn_stale_wrappers(lay, release_id)
    return EXIT_OK


def _roles_setup(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """`roles setup`: the operator is `--operator`, else `SUDO_USER`. The
    steps work under the lock this writer holds. Only the write of
    `publisher.toml` under `/etc/df` takes a generation bracket (inside
    `roles_setup`); the snapshot's own parts never change."""
    operator = _operator_of(args)
    roles_setup.run(lay, operator=operator, snap=snap)
    print(f"darkfactory: роли и обёртки настроены; оператор {operator}")
    return EXIT_OK


def _operator_of(args: argparse.Namespace) -> str:
    operator = _given_operator(args)
    if operator is None:
        raise Refusal(_NO_OPERATOR)
    return operator


def _given_operator(args: argparse.Namespace) -> str | None:
    """`--operator`, else `SUDO_USER`; `None` without either."""
    if args.operator is not None:  # an explicit "" is refused later, not replaced
        return args.operator
    return os.environ.get("SUDO_USER") or None


def _warn_stale_wrappers(lay: Layout, release_id: str) -> None:
    """Plan decision 4 (r2): wrappers written for another admin Python are
    stale; say so (the install itself succeeded, exit 0). Silent on a host
    that never ran `roles setup`."""
    if not libexec.wrappers_present(lay):  # nothing to read on a stage-1 host
        return
    entry = accept.registry_entry(lay, release_id)
    if entry is None:
        return
    python = admin.admin_python_path(lay, entry.admin_python, plat.detect_platform())
    if libexec.wrappers_stale(lay, python):
        _say(_STALE_WRAPPERS)


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


def _cli_set(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """Set the optional approved `openssl` in `install.toml` (spec 3.7).

    Only `openssl`, an absolute path that passes the approved-CLI policy
    (spec 3.10); every other field stays. The same value again writes
    nothing and opens no generation. Otherwise one `write_atomic` between
    `generation_begin` and `generation_end`, as `models set`. The config
    digest changes with it; it is compared only within one install run
    (`pending.toml` and that run's marks), never against a finished one.
    """
    if args.name != CLI_SETTABLE:
        raise Refusal(
            f"cli set: {args.name!r} не задаётся этой командой: "
            f"в v1 только {CLI_SETTABLE}"
        )
    path = Path(args.path)
    if not args.path or not path.is_absolute():
        raise Refusal(f"cli set {CLI_SETTABLE}: путь {args.path!r} не абсолютный")
    cfg = snap.install
    if cfg is None:
        raise Refusal(_NO_INSTALL)
    problems = plat.approved_cli_problems({CLI_SETTABLE: path}, plat.detect_platform())
    if problems:
        raise Refusal("; ".join(problems))
    _close_odd_generation(lay, snap)  # before the no-op too (PR #643)
    if cfg.approved_cli.get(CLI_SETTABLE) == path:
        print(f"darkfactory: {CLI_SETTABLE} уже {path}: install.toml не изменён")
        return EXIT_OK
    new = replace(cfg, approved_cli={**cfg.approved_cli, CLI_SETTABLE: path})
    journal.generation_begin(lay)
    try:
        fsutil.write_atomic(
            lay.etc / "install.toml", dump_install(new), install.INSTALL_MODE
        )
    except OSError:
        journal.generation_end(lay)  # as `models set`: the old file is whole
        raise
    journal.generation_end(lay)
    print(f"darkfactory: {CLI_SETTABLE} = {path} записан в /etc/df/install.toml")
    return EXIT_OK


def _perimeter_restore(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """`perimeter restore` (Task 11r): root reads the copy (`--from`, on a
    trusted path, at most `perimeter.MAX_COPY` bytes) and runs `publisher
    perimeter-restore` as `df-publisher` with it on stdin; the protocol is
    `perimeter.restore`. A database put in place is recorded in root's
    `perimeter.created` (as `perimeter-init`); `resumed` (an earlier restore
    finished, this source not applied) is `NeedsHuman`, exit 3. No `/etc/df`
    file changes, so no generation bracket; one journal line records the
    outcome."""
    copy = None if args.empty else _restore_copy(args.source)
    python = libexec.admin_python_of(lay, snap)
    _close_odd_generation(lay, snap)
    words = ["perimeter-restore", "--empty" if copy is None else "--stdin"]
    if args.set_aside_old:
        words.append("--set-aside-old")
    result = roles_setup.run_as_publisher(
        lay, python, words, timeout=RESTORE_TIMEOUT, stdin=copy
    )
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    outcome = lines[-1].removeprefix("perimeter-restore: ") if lines else ""
    ok = roles_setup.publisher_succeeded(result) and outcome in _RESTORE_DONE
    detail = outcome if ok else roles_setup.publisher_failure(result)
    if ok:
        try:  # a database is in place: "created once", as perimeter-init says
            roles_setup.record_created(lay)
        except OSError as exc:
            ok, detail = False, f"{outcome}; {lay.perimeter_created} не записан: {exc}"
    source = "--empty" if copy is None else f"--from {args.source}"
    event = "failed" if not ok else "needs-human" if outcome == "resumed" else "done"
    journal.journal_event(
        lay.journal,
        run_id=uuid.uuid4().hex,
        step="perimeter-restore",
        event=event,
        detail=f"{source}: {detail}",
    )
    if not ok:
        raise Refusal(f"perimeter restore: {detail}")
    if outcome == "resumed":  # not the source asked for: never exit 0
        raise NeedsHuman(_RESTORE_DONE[outcome])
    print(f"darkfactory: {_RESTORE_DONE[outcome]}")
    return EXIT_OK


def _restore_copy(source: str) -> bytes:
    """The copy `--from` names: an absolute path that passes the trusted-path
    policy of root's reads of operator-given files (spec 3.10, as `cli set`),
    a regular file read without following a link, at most `MAX_COPY`."""
    path = Path(source)
    if not source or not path.is_absolute():
        raise Refusal(f"perimeter restore --from: путь {source!r} не абсолютный")
    problems = plat.trusted_path_problems(path)
    if problems:
        raise Refusal("perimeter restore --from: " + "; ".join(problems))
    try:
        return inputs.read_capped(path, perimeter.MAX_COPY)
    except TreeError as exc:
        raise Refusal(f"perimeter restore --from: {exc}") from exc


def _setup_agent(args: argparse.Namespace, lay: Layout, snap: Snapshot) -> int:
    """`setup-agent app-key | machine-token` (Task 12): root writes the
    secret (`df.setup_agent`), then checks it -- `app-key` by the publisher's
    `self-check`, `machine-token` by `df-exec`'s `check-github`. A refusal
    before the write changes nothing and is not journalled; a failed write
    is journalled `failed`, a failed check `needs-human` (exit 3), else
    `done`; a secret renamed into place but not made durable is
    `needs-human` too. No `/etc/df` file of the snapshot changes: no generation
    bracket; an odd generation is closed after the write, before the checks
    (their readers need a stable snapshot). Neither the journal nor any
    output carries the secret."""
    source = Path(args.file)
    key = args.setup_command == "app-key"
    if key:
        what = f"app-key {lay.app_key(args.app_id)}"
    else:
        target = lay.role_home(setup_agent.EXEC).joinpath(*MACHINE_TOKEN)
        what = f"machine-token {target}"

    def close() -> None:
        try:
            _close_odd_generation(lay, snap)
        except (OSError, StepFailed) as exc:  # after the write: not `failed`
            raise _GenerationNotClosed(f"{type(exc).__name__}: {exc}") from exc

    try:
        if key:
            check = setup_agent.setup_app_key(
                lay, args.app_id, source, before_check=close
            )
        else:
            check = setup_agent.setup_machine_token(lay, source, before_check=close)
    except fsutil.NotDurable as exc:  # the old secret is already replaced
        _journal_setup(lay, "needs-human", f"{what}: {_NOT_DURABLE}: {exc}")
        raise NeedsHuman(f"{what}: {_NOT_DURABLE} ({exc})") from exc
    except _GenerationNotClosed as exc:
        _journal_setup(lay, "needs-human", f"{what}: {_GEN_OPEN}: {exc}")
        raise NeedsHuman(f"{what}: {_GEN_OPEN} ({exc})") from exc
    except OSError as exc:
        _journal_setup(lay, "failed", f"{what}: {type(exc).__name__}: {exc}")
        raise
    event = "done" if check.failure is None else "needs-human"
    outcome = check.failure or "; ".join(check.warnings) or "проверка пройдена"
    _journal_setup(lay, event, f"{what}: {outcome}")
    print(f"darkfactory: записан {what}")
    for line in check.lines:
        print(f"darkfactory: {line}")
    for warning in check.warnings:
        _say(f"предупреждение: {warning}")
    if check.failure is not None:
        raise NeedsHuman(_SETUP_FAILED + check.failure)
    return EXIT_OK


def _journal_setup(lay: Layout, event: str, detail: str) -> None:
    journal.journal_event(
        lay.journal,
        run_id=uuid.uuid4().hex,
        step=setup_agent.STEP,
        event=event,
        detail=detail,
    )


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
    """`journal.close_odd_generation` with the snapshot's values (M9)."""
    journal.close_odd_generation(
        lay,
        generation=snap.generation,
        pending_present=snap.pending_present,
        current=snap.release_id,
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
