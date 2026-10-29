"""`darkfactory doctor`: level 1 «окружение» from facts (spec 3.8, 2.4);
with `--product P` also level 2 «роли» of P (stage 2a, Task 13).

Every check reads the one `Snapshot` the entry handed the command (reader
mode, no lock): the configuration, the registry entry and the models text
come from it, never from `/etc/df` again, and `current` is not read as a
link. The one exception is `publisher.toml` (stage 2a), which `etc.schemas`
opens live, after the snapshot: it is not part of the snapshot (the entry's
reader read is stage 1's), so a verdict on it is withheld while a writer is
active (`_writing`, like the temp files). The journal takes no part. The models section (probe status per role,
spec 3.6) is shown apart: probes are level 2 and never change level 1.

Level 2 is root's only (R13-3): it asks the roles and the publisher through
the real `sudo` (the seams `role_check`, `role_reads`,
`role_perimeter_status`, `review_merge`, and `setup_agent`'s
`publisher_self_check` and `exec_check_github`) and judges their answers with
the production parsers; `products.toml` is read live, like `publisher.toml`.
The perimeter journal's line comes from the same `self-check` answer and is
not a level-2 check.

Exit codes (R68): 0 — level 1 reached (with `--product`: level 2, R13-5);
2 — not reached (a transitional state included); 1 — a program error
(`df.cli.main`).
"""

from __future__ import annotations

import errno
import json
import os
import pwd
import re
import stat
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from df import (
    acl,
    admin,
    boundary,
    digest,
    fsutil,
    journal,
    launch,
    libexec,
    models,
    perimeter_client,
    probe,
    roles,
    roles_setup,
    setup_agent,
    stage2_config,
    state,
    sudoers,
)
from df import build as build_mod
from df import platform as plat
from df.config import (
    KNOWN_SCHEMAS,
    STAGE_OF,
    Account,
    ConfigError,
    load_accounts,
    read_optional,
)
from df.digest import TreeError
from df.journal import StepFailed, Transitional
from df.launch import SUDO
from df.paths import Layout
from df.publisher import request
from df.publisher.github import SelfCheckResult
from df.publisher.perimeter import MAX_EVENTS
from df.publisher.request import BadResponse, Response
from df.release import RELEASE_JSON
from df.state import ReadResult, Snapshot

Status = Literal["ok", "нарушено", "не проверено", "данные недоступны"]
OK: Status = "ok"
BROKEN: Status = "нарушено"
NOT_CHECKED: Status = "не проверено"
UNAVAILABLE: Status = "данные недоступны"
#: Root for `doctor` (tests substitute their own uid).
ROOT_UID = 0
EXIT_REACHED = 0
EXIT_NOT_REACHED = 2
#: Odd generation: a bracket is open -- a running or killed writer, with or
#: without `pending.toml` (the reader cannot tell which).
TRANSITIONAL = (
    "идёт или прервана административная операция: дождитесь её конца; "
    "если установка не завершена — darkfactory install, иначе повторите от "
    "root прерванную команду-писатель (darkfactory install, models set или "
    "cli set)"
)
#: Even generation and `Transitional`: the reader saw `pending.toml` (the
#: entry's stable read), where every other writer is refused (`_FINISH`).
TRANSITIONAL_PENDING = (
    "установка не завершена: завершите её от root darkfactory install"
)


def transitional_text(generation: int) -> str:
    """What recovers a `Transitional(generation)` read: an even generation
    is an unfinished install only; an odd one may be either case."""
    return TRANSITIONAL if generation % 2 else TRANSITIONAL_PENDING


NEED_SUDO = "нужен sudo"
DAMAGED = "релиз повреждён"
SAME_MODELS = "предупреждение: модели автора и ревьюера совпадают"
_REACHED = "уровень 1: достигнут"
_NOT_REACHED = "уровень 1: не достигнут"
_PENDING = "переходное состояние, завершите darkfactory install"
_LIVE_WRITE = "идёт административная запись, повторите doctor"
_NO_ENTRY = "нет записи реестра"
_NO_INSTALL = "нет install.toml"
#: `release.json` is root's record, a few KiB; more is not a record.
_RECORD_CAP = 1 << 20
_STAGE1_FILES = frozenset(n for n in KNOWN_SCHEMAS if n not in STAGE_OF)
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_SPECIAL_BITS = stat.S_ISUID | stat.S_ISGID
_SHOWN_PROBLEMS = 5
#: Directories of `/etc/df` that are not files of a schema (stage 2a): judged
#: by `lstat` alone -- a real directory -- and never opened.
_ETC_DIRS = frozenset({"secrets"})


@dataclass(frozen=True)
class Check:
    """One fact of level 1: its id, status, reason and when it was read.

    `reason` is the human summary (at most `_SHOWN_PROBLEMS` problems);
    `problems` is every problem the check found (the JSON carries all)."""

    id: str
    status: Status
    reason: str
    time: str
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleProbe:
    """One line of the models section: the role and its probe status."""

    role: str
    status: str
    current: bool


class _Outcome(Exception):
    """A check's non-`ok` verdict, raised from inside a helper."""

    def __init__(
        self, status: Status, reason: str, problems: Sequence[str] = ()
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.problems = tuple(problems)


def _broken(problems: Sequence[str]) -> _Outcome:
    """«нарушено» with the summary as reason and every problem kept."""
    return _Outcome(BROKEN, _summary(problems), problems)


@dataclass
class _Context:
    """What the checks of one run share: the snapshot and `release.json`."""

    snap: Snapshot
    layout: Layout
    root: bool
    record: dict[str, object] | _Outcome | None = None

    def release_dir(self) -> Path:
        if self.snap.release_id is None or self.snap.entry is None:
            raise _Outcome(NOT_CHECKED, _NO_ENTRY)
        return self.layout.release_dir(self.snap.release_id)

    def release_record(self) -> dict[str, object]:
        """`release.json` of the active release, read once per run."""
        if self.record is None:
            try:
                self.record = _read_record(self.release_dir() / RELEASE_JSON)
            except _Outcome as exc:
                self.record = exc
        if isinstance(self.record, _Outcome):
            raise self.record
        return self.record


# ---------- the command ----------


def doctor_main(
    read: ReadResult,
    layout: Layout,
    *,
    euid: int,
    as_json: bool,
    product: int | None = None,
    operator: str | None = None,
) -> int:
    """`darkfactory doctor [--product P] [--json]`: level 1, warnings, the
    models section; with `product` also level 2 of P and the perimeter line
    (`operator`: `SUDO_USER`, R13-2).

    `Transitional(g)`: only the transitional state, nothing of a snapshot is
    used. `Stable`: the snapshot as a reader parses it (`parse_snapshot`).
    Exit 0 when the level asked for is reached: 1 without `product`, 2 with
    it (R13-5).
    """
    if isinstance(read, Transitional):
        return _transitional(read.generation, as_json)
    snap = state.parse_snapshot(read.value, layout, mode="reader")
    checks = level1_checks(snap, layout, euid=euid)
    notes = warnings(snap, layout, euid=euid)
    probes = model_lines(snap, layout, euid=euid)
    if product is None:
        render = render_json if as_json else render_text
        print(render(checks, probes, notes))
        return EXIT_REACHED if level1_reached(checks) else EXIT_NOT_REACHED
    second = level2(snap, layout, product, euid=euid, operator=operator)
    render2 = render_json_product if as_json else render_text_product
    print(render2(checks, second, probes, notes))
    reached = level2_reached(checks, second.checks)
    return EXIT_REACHED if reached else EXIT_NOT_REACHED


def _transitional(generation: int, as_json: bool) -> int:
    if as_json:
        data = {"level1": "not reached", "state": "transitional"}
        print(json.dumps({**data, "generation": generation}, ensure_ascii=False))
    else:
        text = transitional_text(generation)
        print(f"{_NOT_REACHED} — install.transitional: {text}")
    return EXIT_NOT_REACHED


# ---------- level 1 ----------


def level1_checks(snap: Snapshot, layout: Layout, *, euid: int) -> list[Check]:
    """The checks of level 1 (spec 3.8), facts only, in report order.

    The configuration, the registry entry and the models text are `snap`'s;
    the journal is not read. Without root a check that cannot read what it
    needs is «данные недоступны: нужен sudo» (R71), never `ok`.
    """
    ctx = _Context(snap, layout, euid == ROOT_UID)
    checks = [
        _run("install.pending", _pending, ctx),
        _run("etc.schemas", _schemas, ctx),
        *_later_stage_files(layout),
        _run("release.active", _active, ctx),
    ]
    digest_check = _run("release.digest", _digest, ctx)
    checks += [
        digest_check,
        _run("release.admin", _admin, ctx),
        _run("release.inputs", _inputs, ctx),
        _run("release.perms", _perms, ctx),
        _run("release.boundary", _boundary_of(digest_check), ctx),
        _run("trusted.paths", _trusted, ctx),
        _run("cli.approved", _approved, ctx),
        _run("build.user", _build_user, ctx),
    ]
    return checks


def level1_reached(checks: Sequence[Check]) -> bool:
    """Every check `ok`, but «не проверено (этап N)» of a later-stage file."""
    return bool(checks) and all(not _blocks(c) for c in checks)


def _blocks(check: Check) -> bool:
    return check.status != OK and not _later_stage(check)


def _later_stage(check: Check) -> bool:
    words = check.reason.split(" ")
    return (
        check.status == NOT_CHECKED
        and check.id.startswith("etc.")
        and len(words) == 2
        and words[0] == "этап"
        and words[1].isdigit()
    )


def _run[C: _Context](check_id: str, check: Callable[[C], str], ctx: C) -> Check:
    """`check(ctx)` -> `ok`; its `_Outcome` is the verdict; a read error is
    «данные недоступны» (without root: «нужен sudo»)."""
    problems: tuple[str, ...] = ()
    try:
        reason, status = check(ctx), OK
    except _Outcome as exc:
        status, reason, problems = exc.status, exc.reason, exc.problems
    except (OSError, StepFailed) as exc:
        status = UNAVAILABLE
        reason = str(exc) if ctx.root else NEED_SUDO
    except (ConfigError, TreeError, ValueError) as exc:  # this check only (M6)
        status, reason = BROKEN, f"{type(exc).__name__}: {exc}"
    return Check(check_id, status, reason, _now(), problems)


def _need_root(ctx: _Context) -> None:
    if not ctx.root:
        raise _Outcome(UNAVAILABLE, NEED_SUDO)


def _pending(ctx: _Context) -> str:
    """Defence in depth: the entry hands a reader `Transitional` whenever
    `pending.toml` exists (`launcher_entry.take_snapshot`), so through the
    command this shows as `install.transitional`."""
    if ctx.snap.pending_present:
        raise _Outcome(BROKEN, _PENDING)
    return ""


def _schemas(ctx: _Context) -> str:
    """The snapshot's texts parse (`install.toml` and the registry entry were
    parsed with the snapshot); `publisher.toml` is opened and parsed here, read
    after the snapshot (writers rename atomically, so it may only be newer);
    every other name of `/etc/df` is judged by the listing alone, never
    opened. A `write_atomic` temp of a known target is a
    live write while a writer's generation bracket is open (the counter moved
    or is odd since the snapshot), else a leftover of a crash."""
    if ctx.snap.models_text is not None:
        try:
            models.parse_models(ctx.snap.models_text)
        except ConfigError as exc:
            raise _Outcome(BROKEN, f"models.toml: {exc}") from exc
    broken_publisher = _publisher_problem(ctx.layout)
    if broken_publisher and _writing(ctx):
        raise _Outcome(UNAVAILABLE, _LIVE_WRITE, [broken_publisher])
    known = _STAGE1_FILES | STAGE_OF.keys()
    unknown = sorted(
        n
        for n in _etc_names(ctx.layout)
        if n not in known and not _is_etc_dir(ctx.layout, n)
    )
    temps = [n for n in unknown if _write_temp_of(n) in known]
    others = [n for n in unknown if n not in temps]
    if temps and _writing(ctx):
        raise _Outcome(UNAVAILABLE, _LIVE_WRITE, temps)
    problems = [
        f"осталась временная запись после сбоя: {n}; удалите вручную (sudo rm)"
        for n in temps
    ]
    problems += [f"неизвестный файл /etc/df: {n}" for n in others]
    reasons = problems[: len(temps)]
    if broken_publisher:
        problems.append(broken_publisher)
        reasons.append(broken_publisher)
    if not problems:
        return ""
    if others:
        reasons.append(f"неизвестные файлы /etc/df: {_summary(others)}")
    raise _Outcome(BROKEN, "; ".join(reasons), problems)


def _publisher_problem(layout: Layout) -> str:
    """The reason `publisher.toml` (stage 2a) does not parse; empty if it does
    or is absent. Returned, not raised, so the other problems of the check
    are reported with it."""
    try:
        text = read_optional(layout.etc / "publisher.toml")
        if text is not None:
            stage2_config.parse_publisher(text)
    except ConfigError as exc:
        return f"publisher.toml: {exc}"
    return ""


def _writing(ctx: _Context) -> bool:
    """A writer's bracket is open: the generation counter (not a snapshot
    part) is odd or differs from the snapshot's."""
    now = journal.read_generation(ctx.layout)
    return now != ctx.snap.generation or now % 2 == 1


def _write_temp_of(name: str) -> str | None:
    """The target of a leftover `write_atomic` temp (a crash before its
    rename; still blocking: it may hold a partial configuration)."""
    found = fsutil.WRITE_TEMP.fullmatch(name)
    return None if found is None else found["name"]


def _is_etc_dir(layout: Layout, name: str) -> bool:
    """A name of `_ETC_DIRS` that is a real directory (not a link, not a file)."""
    if name not in _ETC_DIRS:
        return False
    try:
        return stat.S_ISDIR(os.lstat(layout.etc / name).st_mode)
    except OSError:
        return False


def _etc_names(layout: Layout) -> list[str]:
    try:
        return os.listdir(layout.etc)
    except FileNotFoundError as exc:
        raise _Outcome(BROKEN, f"нет {layout.etc}") from exc


def _later_stage_files(layout: Layout) -> list[Check]:
    """«не проверено (этап N)» for each present file of a later stage."""
    try:
        names = set(os.listdir(layout.etc))
    except OSError:
        return []  # `etc.schemas` reports the directory itself
    return [
        Check(f"etc.{name}", NOT_CHECKED, f"этап {STAGE_OF[name]}", _now())
        for name in sorted(names & STAGE_OF.keys())
    ]


def _active(ctx: _Context) -> str:
    rid, entry = ctx.snap.release_id, ctx.snap.entry
    if rid is None:
        raise _Outcome(BROKEN, "нет current: релиз не активен")
    if entry is None:
        raise _Outcome(BROKEN, f"current {rid}: нет записи в releases.toml")
    if entry.status != "allowed":
        raise _Outcome(BROKEN, f"релиз {rid} отозван: {entry.reason}")
    return ""


def _digest(ctx: _Context) -> str:
    """Recomputed digest = `release.json` = `releases.toml` (spec 3.8)."""
    tree = ctx.release_dir()
    recorded = ctx.release_record().get("digest")
    assert ctx.snap.entry is not None  # `release_dir` checked it
    try:
        on_disk = digest.release_digest(tree)
    except TreeError as exc:
        raise _Outcome(BROKEN, f"{DAMAGED}: {exc}") from exc
    if not on_disk == recorded == ctx.snap.entry.digest:
        raise _Outcome(
            BROKEN,
            f"{DAMAGED}: на диске {on_disk}, release.json {recorded}, "
            f"releases.toml {ctx.snap.entry.digest}",
        )
    return ""


def _admin(ctx: _Context) -> str:
    """`admin_ready` with both values of the snapshot's entry (rev 8, r11)."""
    ctx.release_dir()
    rid, entry = ctx.snap.release_id, ctx.snap.entry
    assert rid is not None and entry is not None
    ready = admin.admin_ready(
        ctx.layout,
        rid,
        expected_admin_digest=entry.admin_digest,
        expected_python=entry.admin_python,
    )
    if not ready:
        raise _Outcome(BROKEN, "административный код или Python не готов")
    return ""


def _inputs(ctx: _Context) -> str:
    """`manifest_sha` and the profile of the release = `install.toml`."""
    record = ctx.release_record()
    install = ctx.snap.install
    if install is None:
        raise _Outcome(BROKEN, _NO_INSTALL)
    wanted = {"manifest_sha": install.manifest_sha, "profile": install.profile}
    differ = [k for k, v in wanted.items() if record.get(k) != v]
    if differ:
        raise _Outcome(BROKEN, f"не равны install.toml: {', '.join(differ)}")
    return ""


def _perms(ctx: _Context) -> str:
    """The tree is root's, without group/other write, setuid/setgid or an
    ACL (links: owner only)."""
    tree = ctx.release_dir()
    problems = _object_problems(tree)
    for dirpath, dirs, files in os.walk(tree, onerror=_raise):
        for name in sorted([*dirs, *files]):
            problems += _object_problems(Path(dirpath) / name)
    if problems:
        raise _broken(problems)
    return ""


def _object_problems(path: Path) -> list[str]:
    st = os.lstat(path)
    problems: list[str] = []
    owner = fsutil.owner_uid(st)
    if owner != plat.ROOT_UID:
        problems.append(f"{path}: владелец uid {owner}, не root")
    if stat.S_ISLNK(st.st_mode):
        return problems
    if st.st_mode & _NO_WRITE_OTHERS:
        problems.append(f"{path}: запись для группы или всех")
    if st.st_mode & _SPECIAL_BITS:
        problems.append(f"{path}: бит setuid или setgid")
    if acl.acl_present_path(path):
        problems.append(f"{path}: есть ACL")
    return problems


def _raise(exc: OSError) -> None:
    raise exc


def _boundary_of(digest_check: Check) -> Callable[[_Context], str]:
    """`static_problems` only over a release whose digest holds (R72): its
    `src/<r>` equals the trusted checkout (R40/F14)."""

    def check(ctx: _Context) -> str:
        if digest_check.status == BROKEN:
            raise _Outcome(NOT_CHECKED, "release.digest нарушен")
        if digest_check.status != OK:
            raise _Outcome(NOT_CHECKED, f"release.digest: {digest_check.status}")
        _need_root(ctx)  # `static_problems` turns a refused read into a line
        tree = ctx.release_dir()
        repos = _src_repos(tree)
        if repos != sorted(_repos(ctx.release_record())):
            raise _Outcome(BROKEN, "состав src/ не совпадает с release.json")
        try:
            expected = {
                r: boundary.expected_entrypoints(tree / "src" / r) for r in repos
            }
        except (TreeError, ValueError) as exc:
            raise _Outcome(BROKEN, str(exc)) from exc
        problems = boundary.static_problems(tree, plat.detect_platform(), expected)
        if problems:
            raise _broken(problems)
        return ""

    return check


def _src_repos(tree: Path) -> list[str]:
    """The repos of the release by its `src/` listing: covered by the digest,
    unlike `release.json` (M5)."""
    try:
        return sorted(os.listdir(tree / "src"))
    except FileNotFoundError:
        return []


def _repos(record: dict[str, object]) -> list[str]:
    repos = record.get("repos")
    if not isinstance(repos, list):
        raise _Outcome(BROKEN, "release.json: repos не список")
    names = [r.get("name") for r in repos if isinstance(r, dict)]
    if len(names) != len(repos) or not all(isinstance(n, str) for n in names):
        raise _Outcome(BROKEN, "release.json: repos без имён")
    return [str(n) for n in names]


def _trusted(ctx: _Context) -> str:
    """`/opt/df`, `/etc/df`, `/var/lib/df`, the admin Python and the
    command pass the trusted-path policy (spec 3.10)."""
    lay = ctx.layout
    paths = [lay.opt, lay.etc, lay.var, lay.launcher]
    entry = ctx.snap.entry
    if entry is not None:
        python = admin.admin_python_path(
            lay, entry.admin_python, plat.detect_platform()
        )
        paths.insert(3, python)
    problems = [p for path in paths for p in plat.trusted_path_problems(path)]
    if problems:
        raise _broken(problems)
    if entry is None:
        raise _Outcome(NOT_CHECKED, f"{_NO_ENTRY}: административный Python")
    return ""


def _approved(ctx: _Context) -> str:
    install = ctx.snap.install
    if install is None:
        raise _Outcome(BROKEN, _NO_INSTALL)
    problems = plat.approved_cli_problems(install.approved_cli, plat.detect_platform())
    if problems:
        raise _broken(problems)
    return ""


def _build_user(ctx: _Context) -> str:
    _need_root(ctx)  # `verify_build_user` turns a refused read into a problem
    problems = build_mod.verify_build_user(ctx.layout)
    if problems:
        raise _broken(problems)
    return ""


def _read_record(path: Path) -> dict[str, object]:
    """`release.json`: absent is unavailable, not ok; not JSON is broken."""
    try:
        fd = digest.open_regular(path)
    except FileNotFoundError as exc:
        raise _Outcome(UNAVAILABLE, f"{RELEASE_JSON}: нет файла") from exc
    except TreeError as exc:
        raise _Outcome(BROKEN, f"{RELEASE_JSON}: {exc}") from exc
    try:
        data = os.read(fd, _RECORD_CAP + 1)
    finally:
        os.close(fd)
    try:
        if len(data) > _RECORD_CAP:
            raise ValueError(f"больше {_RECORD_CAP} байт")
        record = json.loads(data.decode("utf-8"))
        if not isinstance(record, dict):
            raise ValueError("не объект")
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError too
        raise _Outcome(BROKEN, f"{RELEASE_JSON} не разобран: {exc}") from exc
    return record


def _summary(problems: Sequence[str]) -> str:
    shown = "; ".join(problems[:_SHOWN_PROBLEMS])
    rest = len(problems) - _SHOWN_PROBLEMS
    return shown if rest <= 0 else f"{shown}; и ещё {rest}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- level 2: roles, for product P (stage 2a, Task 13) ----------


LEVEL2_IDS = (
    "roles.users",
    "roles.sudoers",
    "roles.libexec",
    "roles.isolation",
    "publisher.answers",
    "publisher.foreign",
    "product.registered",
    "github.machine_user",
    "github.app",
    "models.access",
)
PERIMETER_ID = "perimeter.journal"
#: The run id of `publisher.answers`: never registered, so the publisher
#: answers `refused: run_unknown` (r2 N3: «отвечает» needs no run).
PROBE_RUN_ID = "doctor-probe"
#: Seconds root waits for a role's `check`, `test -r` or a nested call.
ROLE_TIMEOUT = 60.0
TEST = "/bin/test"
MODEL_ROLES = ("df-exec", "df-review")
#: `events` from which the perimeter line warns (spec 6.1, r4 N5).
PERIMETER_WARN = 9_000
#: Free space under which the perimeter line warns.
MIN_FREE = 64 * 1024 * 1024
NO_OPERATOR = "оператор неизвестен (SUDO_USER не задан)"
UNREGISTERED = "продукт не зарегистрирован"
NO_MODEL_ACCESS = "доступ к моделям не подтверждён"
TRUST_STAGE3 = "источники доверия: не проверено (этап 3)"
_APP_RIGHTS = ("contents", "pull_requests")
_WRITE_LEVELS = frozenset({"write", "admin"})
_RANK = {BROKEN: 3, UNAVAILABLE: 2, NOT_CHECKED: 1}
_HTTP = re.compile(r": HTTP (\d{3})$")
_MIB = 1024 * 1024

#: A problem of a check that judges several facts: its status and text.
_Part = tuple[Status, str]


@dataclass(frozen=True)
class Level2:
    """Level 2 of one product: its checks, the perimeter line (not a level-2
    check) and the product's profile (`None`: not registered)."""

    product: int
    checks: list[Check]
    perimeter: Check
    profile: str | None


@dataclass
class _Level2(_Context):
    """What the level-2 checks of one run share: the product, the operator,
    `products.toml` and the one `self-check` answer (read once each)."""

    product: int = 0
    operator: str | None = None
    products: dict[int, stage2_config.Product] | _Outcome | None = None
    answer: SelfCheckResult | _Outcome | None = None
    answered: set[str] = field(default_factory=set)


def level2(
    snap: Snapshot, layout: Layout, product: int, *, euid: int, operator: str | None
) -> Level2:
    """The checks of level 2 for `product` (spec 2.4), in report order, and
    the perimeter line. Root only (R13-3): otherwise every check is
    «данные недоступны: нужен sudo» and nothing is asked."""
    if euid != ROOT_UID:
        checks = [Check(i, UNAVAILABLE, NEED_SUDO, _now()) for i in LEVEL2_IDS]
        line = Check(PERIMETER_ID, UNAVAILABLE, NEED_SUDO, _now())
        return Level2(product, checks, line, None)
    ctx = _Level2(snap, layout, True, product=product, operator=operator)
    checks = [_run(i, check, ctx) for i, check in _LEVEL2]
    line = _run(PERIMETER_ID, _perimeter, ctx)
    products = ctx.products
    entry = products.get(product) if isinstance(products, dict) else None
    return Level2(product, checks, line, None if entry is None else entry.profile)


def level2_reached(level1: Sequence[Check], checks: Sequence[Check]) -> bool:
    """Level 1, and every check of level 2 `ok` (`github.app` with its stage-3
    note)."""
    return (
        level1_reached(level1) and bool(checks) and all(c.status == OK for c in checks)
    )


def _judge(parts: Sequence[_Part]) -> str:
    """The notes of `ok` parts, or the worst status with every problem."""
    problems = [text for status, text in parts if status != OK]
    if not problems:
        return "; ".join(text for _status, text in parts)
    worst = max((s for s, _t in parts if s != OK), key=lambda s: _RANK[s])
    raise _Outcome(worst, _summary(problems), problems)


# --- 1-3: accounts, sudoers, wrappers ---


def _users(ctx: _Level2) -> str:
    """`verify_service_user` for the three roles."""
    problems = [
        f"{name}: {problem}"
        for name in roles.ROLE_USERS
        for problem in plat.verify_service_user(ctx.layout, name)
    ]
    if problems:
        raise _broken(problems)
    return ""


def _sudoers(ctx: _Level2) -> str:
    """The installed file is root's `0440` text of `render` and `visudo`
    accepts that text (the same bytes); the effective policy of five names
    (decision 13: the roles, `df-build`, the operator); the I/O log."""
    operator = _operator(ctx)
    text = sudoers.render(ctx.layout, operator)
    path = sudoers.SUDOERS_FILE
    problems = libexec.root_file_problems(path, text, roles_setup.SUDOERS_MODE)
    if not problems:
        problems = [f"{path}: {p}" for p in sudoers.validate(text)]
    for name in (*roles.ROLE_USERS, plat.BUILD_USER, operator):
        problems += sudoers.policy_problems(name, layout=ctx.layout, operator=operator)
    problems += sudoers.io_log_problems(ctx.layout)
    if problems:
        raise _broken(problems)
    return ""


def _operator(ctx: _Level2) -> str:
    """R13-2: the operator is `SUDO_USER`, as `roles setup` takes it."""
    if not ctx.operator:
        raise _Outcome(UNAVAILABLE, NO_OPERATOR)
    return ctx.operator


def _libexec(ctx: _Level2) -> str:
    """`wrapper_problems` for the admin Python of the active release."""
    try:
        python = libexec.admin_python_of(ctx.layout, ctx.snap)
    except StepFailed as exc:
        raise _Outcome(NOT_CHECKED, exc.detail) from exc
    problems = libexec.wrapper_problems(ctx.layout, python)
    if problems:
        raise _broken(problems)
    return ""


# --- 4: isolation, judged from inside the roles ---


def _isolation(ctx: _Level2) -> str:
    """`df-launch <role> check` of both model roles, the operator's `gh`
    directory as `df-exec` (R13-1), and the publisher's key by its
    `self-check` answer (root never reads `secrets/`)."""
    accounts = load_accounts(ctx.layout.accounts)
    parts: list[_Part] = []
    for role in MODEL_ROLES:
        parts += _role_report(ctx.layout, role, accounts.get(role))
    parts += _gh_denied(ctx)
    parts += _publisher_key(ctx)
    return _judge(parts)


def _denied_paths(layout: Layout, role: str) -> tuple[Path, ...]:
    """What `role` must be refused (`EACCES`) in its own report (R13-1):
    `secrets/` and the other model role's home (`df-review`'s holds the
    machine user's token under `df-exec`'s home)."""
    other = "df-review" if role == "df-exec" else "df-exec"
    return (layout.secrets_dir, layout.role_home(other))


def _role_report(layout: Layout, role: str, account: Account | None) -> list[_Part]:
    if account is None or account.state != "ready":
        return [(BROKEN, f"{role}: нет готовой записи в accounts.toml")]
    report = _report(role_check(layout, role), f"{role} check")
    if not isinstance(report, dict):
        return [report]
    problems = [
        f"{role}: {key} {report.get(key)!r}, в accounts.toml {want}"
        for key, want in (
            ("uid", account.uid),
            ("euid", account.uid),
            ("gid", account.gid),
        )
        if report.get(key) != want
    ]
    fds = report.get("fds")
    if fds != []:
        problems.append(f"{role}: открытые дескрипторы {fds!r}")
    problems += _env_problems(role, report.get("env"))
    denied = report.get("denied")
    if not isinstance(denied, dict):
        denied = {}
    for path in _denied_paths(layout, role):
        got = denied.get(str(path))
        if got != errno.EACCES:
            problems.append(f"{role}: {path}: errno {got!r}, нужен EACCES")
    return [(BROKEN, p) for p in problems]


def _env_problems(role: str, env: object) -> list[str]:
    if not isinstance(env, list):
        return [f"{role}: env не список"]
    extra = sorted(str(n) for n in env if n not in launch.ENV_ALLOW)
    return [f"{role}: лишние переменные окружения {extra}"] if extra else []


def _report(
    result: plat.RunResult, what: str, *, codes: tuple[int, ...] = (0,)
) -> dict[str, object] | _Part:
    """The one JSON object a role printed (exit code in `codes`), or why
    there is none."""
    if not result.launched or result.timed_out:
        state = "срок истёк" if result.timed_out else "не стартовал"
        return (UNAVAILABLE, f"{what}: {state}")
    if result.returncode not in codes:
        return (BROKEN, f"{what}: {_tail(result)}")
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    if not isinstance(report, dict):
        return (BROKEN, f"{what}: ответ не объект JSON")
    return report


def _tail(result: plat.RunResult) -> str:
    """The last stderr line, else the exit code."""
    lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else f"код {result.returncode}"


def _gh_denied(ctx: _Level2) -> list[_Part]:
    """R13-1: `df-exec` cannot read `~<operator>/.config/gh` (kernel access
    as the role: `test -r` exit 1 with nothing on stderr -- sudo's own
    refusal is also exit 1, but it speaks)."""
    if not ctx.operator:
        return [(UNAVAILABLE, f"df-exec: ~/.config/gh: {NO_OPERATOR}")]
    try:
        path = operator_gh(ctx.operator)
    except KeyError:
        return [(BROKEN, f"нет учётной записи оператора {ctx.operator!r}")]
    if not os.path.lexists(path):
        return [(OK, f"нет {path}")]
    result = role_reads("df-exec", path)
    if not result.launched or result.timed_out:
        return [(UNAVAILABLE, f"df-exec: test -r {path}: не выполнен")]
    if result.returncode == 1 and not result.stderr.strip():
        return []
    if result.returncode == 0:
        return [(BROKEN, f"df-exec читает {path}")]
    return [(UNAVAILABLE, f"df-exec: test -r {path}: {_tail(result)}")]


def _publisher_key(ctx: _Level2) -> list[_Part]:
    """`df-publisher` answers `self-check` and found the key of P's App: no
    `app_key_unusable: <app_id>` (the key file is the publisher's to read)."""
    try:
        answer = _self_check(ctx)
    except _Outcome as exc:
        return [(exc.status, f"df-publisher: {exc.reason}")]
    try:
        product = _registered(ctx)
    except _Outcome as exc:
        return [(exc.status, f"df-publisher: ключ App: {exc.reason}")]
    if f"app_key_unusable: {product.app_id}" in answer.problems:
        key = ctx.layout.app_key(product.app_id)
        return [(BROKEN, f"df-publisher: ключ App {product.app_id} не годен: {key}")]
    return []


# --- 5-6: the publisher answers its senders and refuses a foreign one ---


def _answers(ctx: _Level2) -> str:
    """`perimeter-status` of `PROBE_RUN_ID` from each model role through the
    real `sudo`: `refused: run_unknown` is the publisher answering."""
    parts: list[_Part] = []
    for role in MODEL_ROLES:
        part = _answer_of(ctx, role)
        if part is None:
            ctx.answered.add(role)
        else:
            parts.append(part)
    return _judge(parts)


def _answer_of(ctx: _Level2, role: str) -> _Part | None:
    data = perimeter_client.payload(
        PROBE_RUN_ID,
        perimeter_client.RootId("github.com", ctx.product, "main"),
        perimeter_client.TrustView("0" * 40, 0),
        f"doctor-{uuid.uuid4().hex}",
    )
    result = role_perimeter_status(ctx.layout, role, data)
    what = f"{role}: perimeter-status"
    if not result.launched or result.timed_out or result.stdout_truncated:
        return (UNAVAILABLE, f"{what}: нет ответа")
    try:
        resp = _response(result.stdout, what)
    except _Outcome as exc:
        return (BROKEN, f"{exc.reason}; {_tail(result)}")
    if (resp.outcome, resp.reason) != ("refused", "run_unknown"):
        return (BROKEN, f"{what}: {resp.outcome}: {resp.reason}, ждали run_unknown")
    return None


def _foreign(ctx: _Level2) -> str:
    """R13-7: `df-review` -> `df-publish merge` is refused by sudo itself
    (exit 1, nothing on stdout). Judged only after `df-review` reached the
    publisher (`publisher.answers`): a sudo that cannot even run as
    `df-review` exits 1 silently too."""
    if "df-review" not in ctx.answered:
        raise _Outcome(
            NOT_CHECKED, "df-review не получил ответа издателя (publisher.answers)"
        )
    result = review_merge(ctx.layout)
    if not result.launched or result.timed_out:
        raise _Outcome(UNAVAILABLE, "df-review → df-publish merge: не выполнен")
    if result.returncode == 1 and not result.stdout:
        return ""
    raise _Outcome(
        BROKEN,
        f"df-review → df-publish merge не отвергнут sudo: код {result.returncode}, "
        f"stdout {len(result.stdout)} байт",
    )


# --- 7-10: the product ---


def _product_check(ctx: _Level2) -> str:
    """P is in `products.toml` (read live, like `publisher.toml`)."""
    return f"профиль {_lookup(ctx).profile}"


def _lookup(ctx: _Level2) -> stage2_config.Product:
    if ctx.products is None:
        try:
            ctx.products = stage2_config.load_products(ctx.layout.etc / "products.toml")
        except (ConfigError, OSError) as exc:
            status = BROKEN if isinstance(exc, ConfigError) else UNAVAILABLE
            ctx.products = _Outcome(status, f"products.toml: {exc}")
    if isinstance(ctx.products, _Outcome):
        raise ctx.products
    product = ctx.products.get(ctx.product)
    if product is None:
        raise _Outcome(BROKEN, f"{UNREGISTERED}: {ctx.product} нет в products.toml")
    return product


def _registered(ctx: _Level2) -> stage2_config.Product:
    """R13-4: the checks that need P are «не проверено» without it."""
    try:
        return _lookup(ctx)
    except _Outcome as exc:
        raise _Outcome(NOT_CHECKED, UNREGISTERED) from exc


def _machine_user(ctx: _Level2) -> str:
    """`check-github` as `df-exec`: the login is P's `machine_user`, and its
    rights on P are those of P's profile."""
    product = _registered(ctx)
    result = setup_agent.exec_check_github(ctx.layout)
    if result.launched and not result.timed_out and result.returncode not in (0, 1):
        raise _github_refusal(result)
    report = _report(result, "check-github", codes=(0, 1))
    if not isinstance(report, dict):
        raise _Outcome(*report)
    login = report.get("login")
    problems = []
    if login != product.machine_user:
        problems.append(
            f"логин {login!r}, у продукта machine_user {product.machine_user!r}"
        )
    rows = report.get("products")
    row = rows.get(str(product.repository_id)) if isinstance(rows, dict) else None
    if not isinstance(row, dict):
        problems.append(f"в ответе check-github нет продукта {product.repository_id}")
    else:
        problems += _right_problems(product.profile, row.get("permissions"))
    if problems:
        raise _broken(problems)
    return f"{login}; профиль {product.profile}"


def _github_refusal(result: plat.RunResult) -> _Outcome:
    """`check-github` refused (exit 2): 401 is a revoked token; the network
    or a 5xx is unavailable data; anything else is broken."""
    tail = _tail(result)
    code = _HTTP.search(tail)
    if code is not None and code[1] == "401":
        return _Outcome(BROKEN, f"токен отозван: {tail}")
    if UNAVAILABLE in tail or (code is not None and code[1].startswith("5")):
        return _Outcome(UNAVAILABLE, f"GitHub: {tail}")
    return _Outcome(BROKEN, f"check-github: {tail}")


def _right_problems(profile: str, permissions: object) -> list[str]:
    """`write`: `push` and not `admin`; `fork`: no `push`."""
    if not isinstance(permissions, dict):
        return ["permissions не объект"]
    want = {"push": True, "admin": False} if profile == "write" else {"push": False}
    return [
        f"permissions.{key} = {permissions.get(key)!r}, профилю {profile} нужно {value}"
        for key, value in want.items()
        if permissions.get(key) is not value
    ]


def _app(ctx: _Level2) -> str:
    """`self-check`: no problems; P's installation is there with P and the
    write rights of `_APP_RIGHTS`. Trust sources are stage 3's."""
    product = _registered(ctx)
    answer = _self_check(ctx)
    problems = [f"self-check: {p}" for p in answer.problems]
    found = [i for i in answer.installations if i.id == product.installation_id]
    if not found:
        problems.append(
            f"нет установки {product.installation_id} App {product.app_id} "
            "в ответе self-check"
        )
    for inst in found:
        if product.repository_id not in inst.repository_ids:
            problems.append(
                f"установка {inst.id} без репозитория {product.repository_id}"
            )
        problems += [
            f"установка {inst.id}: {name} = {inst.permissions.get(name)!r}, нужно write"
            for name in _APP_RIGHTS
            if inst.permissions.get(name) not in _WRITE_LEVELS
        ]
    if problems:
        raise _broken(problems)
    return TRUST_STAGE3


def _models_access(ctx: _Level2) -> str:
    """Each model role has a current probe for P's choice (spec 3.6); in v1
    none has (decision 9): «доступ к моделям не подтверждён»."""
    _registered(ctx)
    cfg = _models_of(ctx.snap)
    if cfg is None:
        raise _Outcome(BROKEN, "models.toml не разобран")
    targets: dict[Path, tuple[Path, str] | str] = {}
    problems: list[str] = []
    for role in models.ROLES:
        try:
            choice, _source = models.resolve(cfg, role, ctx.product)
        except ConfigError:
            problems.append(f"{role}: модель не задана")
            continue
        status, _rec = probe.current_status(
            ctx.layout, role, choice, ctx.snap.install, root=True, targets=targets
        )
        if not status.current:
            problems.append(f"{role}: {status.text}")
    if problems:
        reason = f"{NO_MODEL_ACCESS}: {_summary(problems)}"
        raise _Outcome(NOT_CHECKED, reason, problems)
    return ""


_LEVEL2: tuple[tuple[str, Callable[[_Level2], str]], ...] = (
    ("roles.users", _users),
    ("roles.sudoers", _sudoers),
    ("roles.libexec", _libexec),
    ("roles.isolation", _isolation),
    ("publisher.answers", _answers),
    ("publisher.foreign", _foreign),
    ("product.registered", _product_check),
    ("github.machine_user", _machine_user),
    ("github.app", _app),
    ("models.access", _models_access),
)


# --- the self-check answer and the perimeter line ---


def _self_check(ctx: _Level2) -> SelfCheckResult:
    """The publisher's `self-check`, asked once per run."""
    if ctx.answer is None:
        try:
            ctx.answer = _ask_self_check(ctx.layout)
        except _Outcome as exc:
            ctx.answer = exc
    if isinstance(ctx.answer, _Outcome):
        raise ctx.answer
    return ctx.answer


def _ask_self_check(layout: Layout) -> SelfCheckResult:
    try:
        raw = setup_agent.publisher_self_check(layout, purpose="doctor")
    except OSError as exc:
        raise _Outcome(UNAVAILABLE, f"self-check: нет ответа: {exc}") from exc
    resp = _response(raw, "self-check")
    if resp.outcome == "unknown":
        raise _Outcome(UNAVAILABLE, f"self-check: unknown: {resp.reason}")
    if resp.outcome != "done":
        raise _Outcome(BROKEN, f"self-check: {resp.outcome}: {resp.reason}")
    try:
        return SelfCheckResult.from_json(dict(resp.result))
    except ValueError as exc:
        raise _Outcome(BROKEN, f"self-check: ответ не разобран: {exc}") from exc


def _response(raw: bytes, what: str) -> Response:
    """One `df/publisher-response/v1` line, parsed as the clients do."""
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        raise _Outcome(BROKEN, f"{what}: нет ответа ({len(lines)} строк)")
    try:
        return request.parse_response(lines[0])
    except BadResponse as exc:
        raise _Outcome(BROKEN, f"{what}: ответ не разобран: {exc}") from exc


def _perimeter(ctx: _Level2) -> str:
    """The perimeter journal's operational line (r4 N5, spec 6.1), from the
    `self-check` answer: events, database size, free space."""
    try:
        stats = _self_check(ctx).perimeter
    except _Outcome as exc:
        raise _Outcome(UNAVAILABLE, exc.reason) from exc
    if stats is None:
        why = next(
            (p for p in _self_check(ctx).problems if p.startswith("perimeter_")),
            "в ответе self-check нет perimeter",
        )
        raise _Outcome(UNAVAILABLE, why)
    text = (
        f"событий {stats.events}, база {stats.db_bytes // _MIB} МиБ, "
        f"свободно {stats.free_bytes // _MIB} МиБ"
    )
    limit = " ".join(f"{MAX_EVENTS:,}".split(","))
    if stats.events > MAX_EVENTS:
        raise _Outcome(
            BROKEN,
            f"{text}; журнал периметра превысил предел {limit}: операции "
            "периметра отвечают unavailable",
        )
    notes = [text]
    if stats.events >= PERIMETER_WARN:
        notes.append(f"предупреждение: журнал периметра приближается к пределу {limit}")
    if stats.free_bytes < MIN_FREE:
        notes.append(f"предупреждение: свободно меньше {MIN_FREE // _MIB} МиБ")
    return "; ".join(notes)


# --- the external calls (the tests' seams) ---


def role_check(layout: Layout, role: str) -> plat.RunResult:
    """`df-launch <role> check` from root (`sudo -n -u <role>`). The tests'
    seam."""
    wrapper = layout.libexec / "df-launch"
    argv = [SUDO, "-n", "-u", role, str(wrapper), role, "check"]
    return plat.run_command(argv, timeout=ROLE_TIMEOUT)


def role_reads(role: str, path: Path) -> plat.RunResult:
    """`test -r <path>` as `role` from root: the kernel's answer for the
    role's uid and groups. The tests' seam."""
    argv = [SUDO, "-n", "-u", role, TEST, "-r", str(path)]
    return plat.run_command(argv, timeout=ROLE_TIMEOUT)


def role_perimeter_status(layout: Layout, role: str, data: bytes) -> plat.RunResult:
    """`perimeter-status` as `role` through its own `sudo` rule (root runs
    `sudo -n -u <role> sudo -n -u df-publisher df-publish ...`). The tests'
    seam."""
    return _through_role(layout, role, "perimeter-status", data)


def review_merge(layout: Layout) -> plat.RunResult:
    """`df-publish merge` asked by `df-review`, which has no such rule; empty
    stdin. The tests' seam."""
    return _through_role(layout, "df-review", "merge", b"")


def _through_role(
    layout: Layout, role: str, operation: str, data: bytes
) -> plat.RunResult:
    publish = layout.libexec / "df-publish"
    inner = [SUDO, "-n", "-u", perimeter_client.PUBLISHER, str(publish), operation]
    return plat.run_with_input(
        [SUDO, "-n", "-u", role, *inner], data, timeout=ROLE_TIMEOUT
    )


def operator_gh(operator: str) -> Path:
    """`~<operator>/.config/gh` by the account database; an unknown account
    is `KeyError`. The tests' seam."""
    return Path(pwd.getpwnam(operator).pw_dir) / ".config" / "gh"


# ---------- warnings and the models section ----------


def warnings(snap: Snapshot, layout: Layout, *, euid: int) -> list[str]:
    """Warnings that never change the level: a non-empty quarantine and the
    same `(harness, model)` for `author` and `reviewer` (spec 3.6)."""
    notes = _quarantine_note(layout, root=euid == ROOT_UID)
    cfg = _models_of(snap)
    if cfg is None:
        return notes
    pairs = []
    for role in ("author", "reviewer"):
        try:
            choice, _source = models.resolve(cfg, role, None)
        except ConfigError:
            return notes
        pairs.append((choice.harness, choice.model))
    return [*notes, SAME_MODELS] if pairs[0] == pairs[1] else notes


def _quarantine_note(layout: Layout, *, root: bool) -> list[str]:
    where = "staging/.quarantine"
    try:
        names = os.listdir(layout.quarantine)
    except FileNotFoundError:
        return []
    except OSError as exc:
        why = str(exc) if root else NEED_SUDO
        return [f"предупреждение: {where}: данные недоступны: {why}"]
    if not names:
        return []
    return [f"предупреждение: {where} не пуст ({len(names)}): остаток очистки"]


def model_lines(snap: Snapshot, layout: Layout, *, euid: int) -> list[RoleProbe]:
    """Each role's probe status (`current_status`, Task 11); not level 1."""
    cfg = _models_of(snap)
    if cfg is None:
        return [RoleProbe(r, "models.toml не разобран", False) for r in models.ROLES]
    targets: dict[Path, tuple[Path, str] | str] = {}
    lines: list[RoleProbe] = []
    for role in models.ROLES:
        try:
            choice, _source = models.resolve(cfg, role, None)
        except ConfigError:
            lines.append(RoleProbe(role, "модель не задана", False))
            continue
        status, _rec = probe.current_status(
            layout,
            role,
            choice,
            snap.install,
            root=euid == ROOT_UID,
            targets=targets,
        )
        lines.append(RoleProbe(role, status.text, status.current))
    return lines


def _models_of(snap: Snapshot) -> models.ModelsConfig | None:
    text = snap.models_text
    if text is None:
        return models.EMPTY
    try:
        return models.parse_models(text)
    except ConfigError:
        return None  # `etc.schemas` reports it


# ---------- rendering ----------


def render_brief(checks: Sequence[Check]) -> str:
    """«уровень 1: достигнут», else the first blocking check."""
    first = next((c for c in checks if _blocks(c)), None)
    if first is None:
        return _REACHED if checks else f"{_NOT_REACHED} — проверок нет"
    return f"{_NOT_REACHED} — {first.id}: {_verdict(first)}"


def render_text(
    checks: Sequence[Check], roles: Sequence[RoleProbe], notes: Sequence[str] = ()
) -> str:
    """The brief line, every check, the warnings and the models section."""
    lines = [render_brief(checks), *(f"  {c.id}: {_verdict(c)}" for c in checks)]
    lines += list(notes)
    lines.append("модели (уровень 2, на уровень 1 не влияют):")
    lines += [f"  {r.role}: {r.status}" for r in roles]
    return "\n".join(lines)


def render_json(
    checks: Sequence[Check], probes: Sequence[RoleProbe], notes: Sequence[str] = ()
) -> str:
    """`{"level1", "checks", "warnings", "models"}`, sorted keys."""
    return _dumps(_json_data(checks, probes, notes))


def _json_data(
    checks: Sequence[Check], probes: Sequence[RoleProbe], notes: Sequence[str]
) -> dict[str, object]:
    return {
        "level1": "reached" if level1_reached(checks) else "not reached",
        "checks": [asdict(c) for c in checks],
        "warnings": list(notes),
        "models": [asdict(p) for p in probes],
    }


def _dumps(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


_NOT_REACHED2 = "уровень 2: не достигнут"
_LATER_LEVELS = (
    "уровень 3: не проверено (этап 3)",
    "уровень 4: не проверено (этап 4)",
)


def render_brief_product(checks: Sequence[Check], second: Level2) -> list[str]:
    """Level 1's brief line, then level 2: reached, else its first blocking
    check (or level 1); levels 3-4 and the profile (R13-5)."""
    first = next((c for c in second.checks if c.status != OK), None)
    if not level1_reached(checks):
        line = f"{_NOT_REACHED2} — уровень 1 не достигнут"
    elif first is not None:
        line = f"{_NOT_REACHED2} — {first.id}: {_verdict(first)}"
    else:
        line = "уровень 2: достигнут"
    profile = second.profile or "неизвестен"
    return [render_brief(checks), line, *_LATER_LEVELS, f"профиль: {profile}"]


def render_text_product(
    checks: Sequence[Check],
    second: Level2,
    roles: Sequence[RoleProbe],
    notes: Sequence[str] = (),
) -> str:
    """The brief lines, every check of levels 1 and 2, the perimeter line, the
    warnings and the models section."""
    lines = render_brief_product(checks, second)
    lines += [f"  {c.id}: {_verdict(c)}" for c in [*checks, *second.checks]]
    lines.append(f"  {second.perimeter.id}: {_verdict(second.perimeter)}")
    lines += list(notes)
    lines.append("модели (уровень 2, на уровень 1 не влияют):")
    lines += [f"  {r.role}: {r.status}" for r in roles]
    return "\n".join(lines)


def render_json_product(
    checks: Sequence[Check],
    second: Level2,
    probes: Sequence[RoleProbe],
    notes: Sequence[str] = (),
) -> str:
    """`render_json`'s keys and `product`, `profile`, `level2`,
    `level2_checks`, `perimeter`, `level3`, `level4`."""
    data = _json_data(checks, probes, notes)
    reached = level2_reached(checks, second.checks)
    data |= {
        "product": second.product,
        "profile": second.profile,
        "level2": "reached" if reached else "not reached",
        "level2_checks": [asdict(c) for c in second.checks],
        "perimeter": asdict(second.perimeter),
        "level3": "not checked (stage 3)",
        "level4": "not checked (stage 4)",
    }
    return _dumps(data)


def _verdict(check: Check) -> str:
    return f"{check.status}: {check.reason}" if check.reason else check.status
