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
the real `sudo` (the seams `role_check`, `role_access`,
`role_perimeter_status`, `review_merge`, `publisher_precheck`, and `setup_agent`'s
`publisher_self_check` and `exec_check_github`) and judges their answers with
the production parsers; `products.toml` is read live, like `publisher.toml`.
The perimeter journal's line comes from the same `self-check` answer and is
not a level-2 check; nor is the operator's line (spec 4.5, O7: (i)
`sudoers.operator_rights`, (ii) never `ok`).

Level 3 «доверие» (stage 3a, Task 13; spec 2.4 rev 11) is shown apart, gated
as level 2: the binding, the history profile and `df-history`, root's
`trust-status` (`setup_trust.trust_status_call`, judged by
`request.parse_trust_status`), `unsupported_size` and the perimeter schema.
It never changes level 1, level 2 or the exit; the side effects of the
probes are listed (spec 8).

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
    admin_tree,
    boundary,
    digest,
    fsutil,
    journal,
    launch,
    libexec,
    models,
    perimeter_client,
    precheck_reap,
    probe,
    roles,
    setup_agent,
    setup_trust,
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
    load_groups,
    read_optional,
)
from df.digest import TreeError
from df.journal import StepFailed, Transitional
from df.launch import SUDO
from df.paths import Layout
from df.publisher import precheck, request, store
from df.publisher.github import SelfCheckResult
from df.publisher.perimeter import MAX_EVENTS, MIGRATION_REQUIRED, SCHEMA_VERSION
from df.publisher.request import (
    BadResponse,
    BlockingRow,
    Response,
    TrustStatus,
)
from df.publisher.trust_routes import UNFINISHED, UNVERIFIED, binding_problem
from df.release import RELEASE_JSON
from df.roles import HISTORY_GROUP, history_members
from df.stage2_config import TrustBinding, load_trust
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
    (`operator`: `--operator`, else `SUDO_USER`, R13-10).

    `Transitional(g)`: only the transitional state, nothing of a snapshot is
    used. `Stable`: the snapshot as a reader parses it (`parse_snapshot`).
    Exit 0 when the level asked for is reached: 1 without `product`, 2 with
    it (R13-5); level 3 is shown apart and never changes the exit.
    """
    if isinstance(read, Transitional):
        return _transitional(read.generation, as_json, product)
    snap = state.parse_snapshot(read.value, layout, mode="reader")
    checks = level1_checks(snap, layout, euid=euid)
    notes = warnings(snap, layout, euid=euid)
    probes = model_lines(snap, layout, euid=euid)
    if product is None:
        render = render_json if as_json else render_text
        print(render(checks, probes, notes))
        return EXIT_REACHED if level1_reached(checks) else EXIT_NOT_REACHED
    second = level2(snap, layout, product, euid=euid, operator=operator, level1=checks)
    third = level3(
        snap, layout, euid=euid, operator=operator, level1=checks, second=second
    )
    render2 = render_json_product if as_json else render_text_product
    print(render2(checks, second, probes, notes, third))
    reached = level2_reached(checks, second.checks)
    return EXIT_REACHED if reached else EXIT_NOT_REACHED


def _transitional(generation: int, as_json: bool, product: int | None) -> int:
    """The transitional state; with `product`, level 2 is not reached too."""
    if as_json:
        data: dict[str, object] = {"level1": "not reached", "state": "transitional"}
        if product is not None:
            data |= {"product": product, "level2": "not reached"}
        print(json.dumps({**data, "generation": generation}, ensure_ascii=False))
    else:
        text = transitional_text(generation)
        print(f"{_NOT_REACHED} — install.transitional: {text}")
        if product is not None:
            print(f"{_NOT_REACHED2} — уровень 1 не достигнут")
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


def etc_dirs(layout: Layout) -> frozenset[str]:
    """The directories of `/etc/df` the installer itself makes, which are not
    files of a schema: `secrets/` (stage 2a, `setup-agent`) and `history-gh/`
    (stage 3a, `setup-trust write`). Named by the layout, so a directory the
    writers make cannot drift from what `etc.schemas` knows; judged by
    `lstat` alone -- a real directory -- and never opened."""
    return frozenset({layout.secrets_dir.name, layout.history_gh.name})


def _is_etc_dir(layout: Layout, name: str) -> bool:
    """A name of `etc_dirs` that is a real directory (not a link, not a file)."""
    if name not in etc_dirs(layout):
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
    "roles.precheck",
    "publisher.answers",
    "publisher.precheck",
    "publisher.foreign",
    "publisher.owners",
    "product.registered",
    "github.machine_user",
    "github.app",
    "models.access",
)
PERIMETER_ID = "perimeter.journal"
#: How the perimeter line's text begins when the publisher opened the
#: database (`perimeter.schema` reads it: the schema is current).
PERIMETER_EVENTS = "событий"
OPERATOR_RIGHTS_ID = "operator.rights"
OPERATOR_ISOLATION_ID = "operator.isolation"
#: The run id of `publisher.answers`: never registered, so the publisher
#: answers `refused: run_unknown` (r2 N3: «отвечает» needs no run).
PROBE_RUN_ID = "doctor-probe"
#: What `publisher.precheck` sends `df-precheck`: no request at all (no
#: key of `df/precheck-request/v1`, so no token), which `precheck serve`
#: refuses itself with `PRECHECK_EXIT` and `PRECHECK_REFUSAL` on stderr
#: (`act_precheck.serve`; a test holds them equal).
PRECHECK_PROBE = b"{}"
PRECHECK_EXIT = 2
PRECHECK_REFUSAL = "df-precheck: invalid request"
#: How sudo's own refusals begin (no rule, unknown user, `-n`).
_SUDO_REFUSALS = ("sudo:", "Sorry, user")
#: Seconds `roles.precheck` gives its process listing: generous, unlike the
#: reap's `precheck_reap.PS_TIMEOUT` (a slow `ps` on a loaded host is not
#: «нарушено»; doctor has no rung margin to keep).
LEFTOVERS_TIMEOUT = 30.0
#: Seconds root waits for a role's `check`, `test -r` or a nested call.
ROLE_TIMEOUT = 60.0
TEST = "/bin/test"
#: `test` flags the operator's `gh` directory must fail for `df-exec`.
GH_PROBES = ("-r", "-x")
MODEL_ROLES = ("df-exec", "df-review")
#: `events` from which the perimeter line warns (spec 6.1, r4 N5).
PERIMETER_WARN = 9_000
#: Free space under which the perimeter line warns.
MIN_FREE = 64 * 1024 * 1024
NO_OPERATOR = "оператор неизвестен (нет --operator, SUDO_USER не задан)"
_LEVEL1_FIRST = "уровень 1 не достигнут"
#: What `_other_operator` reads beyond the expected sudoers text.
SUDOERS_MARGIN = 64 * 1024
UNREGISTERED = "продукт не зарегистрирован"
NO_MODEL_ACCESS = "доступ к моделям не подтверждён"
UNRESOLVED_OWNERS = "владения PR без окончательного результата"
DAMAGED_OWNERS = "файл владения PR повреждён"
TRUST_STAGE3 = "источники доверия: не проверено (этап 3)"
_APP_RIGHTS = ("contents", "pull_requests")
_WRITE_LEVELS = frozenset({"write", "admin"})
_PERIMETER_PROBLEM = "perimeter_"
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
    #: The operator's line (spec 4.5, O7): (i) `operator.rights`, (ii)
    #: `operator.isolation` -- not level-2 checks, (ii) never `ok`.
    rights: tuple[Check, ...] = ()


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
    snap: Snapshot,
    layout: Layout,
    product: int,
    *,
    euid: int,
    operator: str | None,
    level1: Sequence[Check],
) -> Level2:
    """The checks of level 2 for `product` (spec 2.4), in report order, and
    the perimeter line. Root only (R13-3): otherwise every check is
    «данные недоступны: нужен sudo». Only over a reached `level1` (R13-11,
    as R72): otherwise every check is «не проверено: уровень 1 не
    достигнут». In both cases nothing is asked -- no role runs release code
    a broken level 1 may have changed."""
    if euid != ROOT_UID:
        return _unasked(product, UNAVAILABLE, NEED_SUDO)
    if not level1_reached(level1):
        return _unasked(product, NOT_CHECKED, _LEVEL1_FIRST)
    ctx = _Level2(snap, layout, True, product=product, operator=operator)
    checks = [_run(i, check, ctx) for i, check in _LEVEL2]
    line = _run(PERIMETER_ID, _perimeter, ctx)
    rights = (_run(OPERATOR_RIGHTS_ID, _operator_rights, ctx), _isolation_line())
    products = ctx.products
    entry = products.get(product) if isinstance(products, dict) else None
    profile = None if entry is None else entry.profile
    return Level2(product, checks, line, profile, rights)


def _unasked(product: int, status: Status, reason: str) -> Level2:
    checks = [Check(i, status, reason, _now()) for i in LEVEL2_IDS]
    line = Check(PERIMETER_ID, status, reason, _now())
    rights = (Check(OPERATOR_RIGHTS_ID, status, reason, _now()), _isolation_line())
    return Level2(product, checks, line, None, rights)


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
    """`verify_service_user` for every role of `ROLE_USERS`."""
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
    accepts that text (the same bytes); the effective policy of the roles,
    `df-build` and the operator (decision 13); the I/O log of `df-publish`
    and of `df-precheck` (stage 2b plan decision 5)."""
    operator = _operator(ctx)
    path = sudoers.SUDOERS_FILE
    problems = sudoers.file_problems(ctx.layout, operator)
    other = None
    if any(libexec.TEXT_MISMATCH in p for p in problems):
        other = _other_operator(ctx.layout, path, operator)
    if other is not None:  # judge the file as `other`'s, without the operator
        problems = sudoers.file_problems(ctx.layout, other)
    names = (*roles.ROLE_USERS, plat.BUILD_USER)
    for name in names if other is not None else (*names, operator):
        problems += sudoers.policy_problems(
            name, layout=ctx.layout, operator=other or operator
        )
    problems += sudoers.io_log_problems(ctx.layout)
    if other is None and not problems:
        return ""
    note = _other_operator_note(str(other), operator)
    if not problems:
        raise _Outcome(UNAVAILABLE, note)
    raise _broken([*problems, note] if other is not None else problems)


def _other_operator(layout: Layout, path: Path, operator: str) -> str | None:
    """R13-10: the operator another than `operator` the installed file was
    rendered for, named unambiguously (`installed_operator`); `None` when the
    file names none, this one, or cannot be read within its bound."""
    cap = len(sudoers.render(layout, operator).encode("utf-8")) + SUDOERS_MARGIN
    text = _bounded_text(path, cap)
    installed = None if text is None else sudoers.installed_operator(text, layout)
    return installed if installed != operator else None


def _bounded_text(path: Path, cap: int) -> str | None:
    """At most `cap` bytes of a regular file opened without following a
    link (`digest.open_regular`); larger, unreadable or not UTF-8 is `None`."""
    try:
        fd = digest.open_regular(path)
    except (OSError, TreeError):
        return None
    try:
        data = b""
        while len(data) <= cap and (chunk := os.read(fd, cap + 1 - len(data))):
            data += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > cap:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _operator(ctx: _Level2) -> str:
    """R13-10: the operator is `doctor --operator`, else `SUDO_USER` (the
    precedence of `roles setup`)."""
    if not ctx.operator:
        raise _Outcome(UNAVAILABLE, NO_OPERATOR)
    try:
        sudoers.render(ctx.layout, ctx.operator)
    except ValueError as exc:
        raise _Outcome(UNAVAILABLE, f"оператор недопустим: {exc}") from exc
    return ctx.operator


def _operator_rights(ctx: _Level2) -> str:
    """The operator's result (i) (spec 4.5, O7): `sudoers.operator_rights`.
    A file installed for another operator (R13-10) is not judged for this
    one: «данные недоступны» with `roles.sudoers`' hint."""
    operator = _operator(ctx)
    rights = sudoers.operator_rights(ctx.layout, operator)
    if not rights.ours:
        return ""
    other = None
    if any(libexec.TEXT_MISMATCH in p for p in rights.ours):
        other = _other_operator(ctx.layout, sudoers.SUDOERS_FILE, operator)
    if other is not None:
        raise _Outcome(UNAVAILABLE, _other_operator_note(other, operator))
    raise _broken(rights.ours)


def _other_operator_note(other: str, operator: str) -> str:
    return (
        f"файл установлен для оператора {other}, проверка для {operator} — "
        f"укажите --operator {other}"
    )


def _isolation_line() -> Check:
    """The operator's result (ii): never checked, never `ok` (O7)."""
    return Check(OPERATOR_ISOLATION_ID, NOT_CHECKED, sudoers.OPERATOR_EXCEPTION, _now())


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


def _precheck_leftovers(ctx: _Level2) -> str:
    """Processes of `df-precheck` older than `precheck_reap.REAP_AFTER`
    (2b Task 10): sudo's `command_timeout` does not end a stopped command,
    the next precheck reaps them. Listed as a note of `ok`; processes that
    cannot be read are «нарушено»."""
    account = load_accounts(ctx.layout.accounts).get(roles.PRECHECK_USER)
    if account is None:
        raise _Outcome(NOT_CHECKED, f"нет записи {roles.PRECHECK_USER}")
    try:
        listing = precheck_reap.list_processes(timeout=LEFTOVERS_TIMEOUT)
        old = precheck_reap.leftovers(account.uid, listing)
    except precheck_reap.ListingError as exc:
        raise _broken([f"процессы не прочитаны: {exc}"]) from exc
    if not old:
        return ""
    pids = ", ".join(str(p.pid) for p in old)
    after = int(precheck_reap.REAP_AFTER)
    return (
        f"процессы {roles.PRECHECK_USER} старше {after} с: {pids} "
        "(их уберёт следующая предпроверка)"
    )


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
    `secrets/` and the other model role's home (`df-review`'s list holds
    `df-exec`'s home, where the machine user's token lives)."""
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


#: Names the OS runtime puts into a process's environment itself, at
#: interpreter start: macOS's CoreFoundation sets `__CF_USER_TEXT_ENCODING`
#: (the release's first statement sees it after an `execve` with exactly
#: `launch.ENV_ALLOW`, plan Task 14a F3). Not inherited, so not a leak of
#: the caller; accepted on that platform only.
RUNTIME_ENV: dict[plat.PlatformName, frozenset[str]] = {
    "macos-arm64": frozenset({"__CF_USER_TEXT_ENCODING"}),
    "linux-x86_64": frozenset(),
}


def _env_problems(role: str, env: object) -> list[str]:
    if not isinstance(env, list):
        return [f"{role}: env не список"]
    allowed = {*launch.ENV_ALLOW, *RUNTIME_ENV[plat.detect_platform()]}
    extra = sorted(str(n) for n in env if n not in allowed)
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
        return (BROKEN, f"{what}: ответ не объект JSON; {_tail(result)}")
    return report


def _tail(result: plat.RunResult) -> str:
    """The last stderr line, else the exit code."""
    lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else f"код {result.returncode}"


def _gh_denied(ctx: _Level2) -> list[_Part]:
    """R13-1: `df-exec` can neither read nor search `~<operator>/.config/gh`
    (kernel access as the role, `test -r` and `test -x`: a `0711` directory
    still lets a file inside be opened by name)."""
    if not ctx.operator:
        return [(UNAVAILABLE, f"df-exec: ~/.config/gh: {NO_OPERATOR}")]
    try:
        path = operator_gh(ctx.operator)
    except KeyError:
        return [(BROKEN, f"нет учётной записи оператора {ctx.operator!r}")]
    if not os.path.lexists(path):
        return [(OK, f"нет {path}")]
    return [p for flag in GH_PROBES for p in _gh_probe(path, flag)]


def _gh_probe(path: Path, flag: str) -> list[_Part]:
    """One `test <flag>` as `df-exec`: denied is exit 1 with nothing on
    stderr -- sudo's own refusal is also exit 1, but it speaks."""
    result = role_access("df-exec", flag, path)
    if not result.launched or result.timed_out:
        return [(UNAVAILABLE, f"df-exec: test {flag} {path}: не выполнен")]
    if result.returncode == 1 and not result.stderr.strip():
        return []
    if result.returncode == 0:
        return [(BROKEN, f"df-exec: test {flag} {path} проходит")]
    return [(UNAVAILABLE, f"df-exec: test {flag} {path}: {_tail(result)}")]


def _publisher_key(ctx: _Level2) -> list[_Part]:
    """`df-publisher` answers `self-check` and signed with the key of P's App
    (the key file is the publisher's to read): positive evidence only -- an
    installation of that App, or a problem of it past the signing."""
    try:
        answer = _self_check(ctx)
        product = _registered(ctx)
    except _Outcome as exc:
        return [(exc.status, f"df-publisher: ключ App: {exc.reason}")]
    app = product.app_id
    if f"app_key_unusable: {app}" in answer.problems:
        key = ctx.layout.app_key(app)
        return [(BROKEN, f"df-publisher: ключ App {app} не годен: {key}")]
    installs = _installation_apps(ctx)
    signed = any(installs.get(i.id) == app for i in answer.installations) or any(
        setup_agent.problem_app(p, installs) == app for p in answer.problems
    )
    if not signed:
        return [(NOT_CHECKED, f"df-publisher: self-check не проверял App {app}")]
    return []


def _installation_apps(ctx: _Level2) -> dict[int, int | None]:
    """Installation id -> its App by `products.toml` (`_lookup` read it)."""
    products = ctx.products if isinstance(ctx.products, dict) else {}
    return setup_agent.installation_apps(products.values())


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


def _precheck_chain(ctx: _Level2) -> str:
    """Final review I1: the chain publisher → sudo → `df-precheck` wrapper →
    entry → `precheck serve`, through the real `sudo`, as the publisher runs
    it (`publisher_precheck`). The program's own refusal of `PRECHECK_PROBE`
    (exit `PRECHECK_EXIT`, `PRECHECK_REFUSAL`, nothing on stdout) proves the
    sudoers rule, the wrapper, the entry and its digest, the role's record,
    the program, its `site/` and imports; anything else is «нарушено» with
    its cause -- also a refusal over a tree an older installer built
    without files of its list (`admin_tree.INCOMPLETE_TREE` on stderr: v1
    served, v2 refused; 3a Task 1), which names the reinstall; with stderr
    truncated that line may be lost, so the refusal proves nothing
    («данные недоступны»). Like every
    `precheck serve`, the probe reaps leftovers first (`roles.precheck` has
    listed them before)."""
    result = publisher_precheck(ctx.layout)
    what = f"{perimeter_client.PUBLISHER} → {roles.PRECHECK_USER}"
    if not result.launched or result.timed_out or result.stdout_truncated:
        raise _Outcome(UNAVAILABLE, f"{what}: нет ответа")
    tail = _tail(result)
    refused = result.returncode == PRECHECK_EXIT and tail.startswith(PRECHECK_REFUSAL)
    if refused and not result.stdout:
        if result.stderr_truncated:  # the incomplete-tree line may be dropped
            raise _Outcome(UNAVAILABLE, f"{what}: stderr усечён, отказ не доказан")
        incomplete = _incomplete_tree(result)
        if incomplete:
            raise _Outcome(BROKEN, f"{what}: {incomplete}")
        return ""
    raise _Outcome(BROKEN, f"{what}: {_precheck_cause(result, tail)}")


def _incomplete_tree(result: plat.RunResult) -> str:
    """The stderr line of `precheck serve` naming an incomplete tree, or ""."""
    lines = result.stderr.decode("utf-8", "replace").splitlines()
    return next((ln for ln in lines if admin_tree.INCOMPLETE_TREE in ln), "")


def _precheck_cause(result: plat.RunResult, tail: str) -> str:
    """Why the probe was not refused by the program: it answered, sudo
    refused (no rule: `roles setup` was not re-run), a program error
    (`EXIT_BUG`: its last traceback line), or another refusal by name."""
    if result.returncode == 0 or result.stdout:
        return (
            "ответ на заведомо невалидный запрос: "
            f"код {result.returncode}, stdout {len(result.stdout)} байт"
        )
    if tail.startswith(_SUDO_REFUSALS):
        return f"sudo отказал: {tail}; повторите `darkfactory roles setup`"
    if result.returncode == 1:
        return f"программная ошибка precheck serve: {tail}"
    return f"отказ: {tail}"


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


def _owners(ctx: _Level2) -> str:
    """Spec 5.4, the operator's step 1: the PR ownerships of P whose request
    has no final result (product, PR, `request_id`, last phase, time). They
    are a property, not a fault (step 5): the check stays `ok` and names
    them. A damaged ownership file of P (or one whose name has no product)
    is «нарушено»; another product's never touches P's line."""
    mine = [
        o
        for o in store.owners(ctx.layout)
        if o.repository_id in (ctx.product, None) and not o.final
    ]
    damaged = [f"{_owner_row(o)}: {o.damaged}" for o in mine if o.damaged]
    rows = [_owner_row(o) for o in mine if not o.damaged]
    held = f"{UNRESOLVED_OWNERS} ({ctx.product}): {_summary(rows)}" if rows else ""
    if damaged:
        problems = [*damaged, *([held] if held else [])]
        raise _Outcome(BROKEN, f"{DAMAGED_OWNERS}: {_summary(problems)}", problems)
    return held


def _owner_row(o: store.Owner) -> str:
    if o.damaged:
        return f"PR {o.pr if o.pr is not None else '?'}"
    phase = "фазы нарушены" if o.phases_damaged else f"фаза {o.last_phase or 'intent'}"
    return f"PR {o.pr}: request_id {o.request_id or '?'}, {phase}, с {o.taken_at}"


# --- 7-10: the product ---


def _product_check(ctx: _Level2) -> str:
    """P is in `products.toml` (read live, like `publisher.toml`)."""
    return f"профиль {_lookup(ctx).profile}"


def _lookup(ctx: _Level2) -> stage2_config.Product:
    # TODO: (stage 3, R13-9) `products.toml` is read live, outside the
    # snapshot; once `setup-repo` writes it, withhold a verdict during a live
    # write (`_writing`), as `etc.schemas` does for `publisher.toml`.
    if ctx.products is None:
        ctx.products = _read_products(ctx.layout.etc / "products.toml")
    if isinstance(ctx.products, _Outcome):
        raise ctx.products
    product = ctx.products.get(ctx.product)
    if product is None:
        raise _Outcome(BROKEN, f"{UNREGISTERED}: {ctx.product} нет в products.toml")
    return product


def _read_products(path: Path) -> dict[int, stage2_config.Product] | _Outcome:
    """R13-12: a file that cannot be read (open, not a regular file, read)
    is «данные недоступны»; text that does not parse or decode is
    «нарушено». Absent is no products."""
    try:
        text = read_optional(path)
    except ConfigError as exc:
        if not isinstance(exc.__cause__, UnicodeDecodeError):
            return _Outcome(UNAVAILABLE, f"products.toml: {exc}")
        return _Outcome(BROKEN, f"products.toml: {exc}")
    try:
        return {} if text is None else stage2_config.parse_products(text)
    except ConfigError as exc:
        return _Outcome(BROKEN, f"products.toml: {exc}")


def _registered(ctx: _Level2) -> stage2_config.Product:
    """R13-4: the checks that need P are «не проверено» without it; an
    unreadable `products.toml` says so (unavailable), never «не
    зарегистрирован»."""
    try:
        return _lookup(ctx)
    except _Outcome as exc:
        if exc.reason.startswith(UNREGISTERED):
            raise _Outcome(NOT_CHECKED, UNREGISTERED) from exc
        status = UNAVAILABLE if exc.status == UNAVAILABLE else NOT_CHECKED
        raise _Outcome(status, f"product.registered: {exc.reason}") from exc


def _machine_user(ctx: _Level2) -> str:
    """`check-github` as `df-exec`: the login is P's `machine_user`, and its
    rights on P are those of P's profile."""
    product = _registered(ctx)
    # TODO: (stage 3, R13-9) `check-github` reports every product and any
    # refused GET aborts the whole report, so another product's repository
    # (404/403, network) fails P too; needs `check-github --product P` or
    # per-row errors in `launch_release.check_github`.
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
    """`check-github` refused (exit 2): 401 is a revoked token; the network,
    429 or a 5xx is unavailable data; anything else is broken."""
    tail = _tail(result)
    code = _HTTP.search(tail)
    if code is not None and code[1] == "401":
        return _Outcome(BROKEN, f"токен отозван: {tail}")
    transient = code is not None and (code[1] == "429" or code[1].startswith("5"))
    if UNAVAILABLE in tail or transient:
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
    """`self-check`: no problem of P's App (R13-8); P's installation is there
    with P and the write rights of `_APP_RIGHTS`. Trust sources are stage
    3's; other Apps' problems are a note."""
    product = _registered(ctx)
    answer = _self_check(ctx)
    mine, others = _app_problems(ctx, answer.problems, product.app_id)
    problems = [f"self-check: {p}" for p in mine]
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
    note = f"; другие App: {_summary(others)}" if others else ""
    return TRUST_STAGE3 + note


def _app_problems(
    ctx: _Level2, problems: Sequence[str], app_id: int
) -> tuple[list[str], list[str]]:
    """R13-8, by Task 12's attribution (`problem_app`): `(P's, other Apps')`.
    A problem of no attributable App counts as P's; `perimeter_*` belong to
    the perimeter line only."""
    installs = _installation_apps(ctx)
    mine: list[str] = []
    others: list[str] = []
    for problem in problems:
        if problem.startswith(_PERIMETER_PROBLEM):
            continue
        owner = setup_agent.problem_app(problem, installs)
        (mine if owner in (app_id, None, -1) else others).append(problem)
    return mine, others


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
    ("roles.precheck", _precheck_leftovers),
    ("publisher.answers", _answers),
    ("publisher.precheck", _precheck_chain),
    ("publisher.foreign", _foreign),
    ("publisher.owners", _owners),
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
            (p for p in _self_check(ctx).problems if p.startswith(_PERIMETER_PROBLEM)),
            "в ответе self-check нет perimeter",
        )
        raise _Outcome(UNAVAILABLE, why)
    text = (
        f"{PERIMETER_EVENTS} {stats.events}, база {stats.db_bytes // _MIB} МиБ, "
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


# ---------- level 3: trust, for product P (stage 3a, Task 13) ----------


LEVEL3_IDS = (
    "trust.binding",
    "trust.history",
    "trust.status",
    "trust.unsupported_size",
    "perimeter.schema",
)
NO_BINDING = "нет trust.toml: привязки нет, выполните darkfactory setup-trust"
#: Spec 6.2 (O14): a binding without the history token key.
NO_HISTORY_KEY = (
    "привязка без ключа history_gh_config_dir: нет профиля токена истории (O14); "
    "повторите sudo darkfactory setup-trust write с --history-token-file"
)
#: Spec 8 «Пробы с побочными эффектами» (rev 11): what the probes change.
SIDE_EFFECT_PRECHECK = (
    "publisher.precheck: настоящий precheck serve убирает остатки "
    f"{roles.PRECHECK_USER} старше {int(precheck_reap.REAP_AFTER)} с"
)
SIDE_EFFECT_TRUST = (
    "trust-status: настоящий precheck serve убирает остатки "
    f"{roles.PRECHECK_USER} старше {int(precheck_reap.REAP_AFTER)} с; издатель "
    "создаёт наблюдение, может зарегистрировать находку и принять новую голову "
    "корня"
)
#: `blocked` with no finding of this call: `trust-status` carries only this
#: call's findings while the state counts every blocking event of the root
#: (`trust_routes.route_trust_status`), so the cause is not in the answer.
UNNAMED_BLOCK = (
    "корень заблокирован; причина в ответе trust-status не названа "
    "(зарегистрированное событие, консервативное событие или восстановление базы)"
)
#: Spec 6.1 L2818: `doctor` lists the known roots; the publisher does not
#: answer them yet (a field of `trust-status`, follow-up after #706).
OTHER_ROOTS = (
    "не проверено: события корней кроме текущего не перечислены (нет данных издателя)"
)
_UNSUPPORTED = "unsupported_size: "
_NOT_REACHED3 = "уровень 3: не достигнут"


@dataclass(frozen=True)
class Level3:
    """The trust checks of level 3 (spec 2.4 rev 11: shown apart, each with
    its status) and the side effects of the probes that ran (spec 8)."""

    checks: list[Check]
    side_effects: tuple[str, ...]


@dataclass
class _Level3(_Context):
    """What the level-3 checks of one run share: the operator, level 2's
    perimeter line, the binding and the one `trust-status` answer."""

    operator: str | None = None
    perimeter: Check | None = None
    binding: TrustBinding | _Outcome | None = None
    status: TrustStatus | _Outcome | None = None
    asked: bool = False


def level3(
    snap: Snapshot,
    layout: Layout,
    *,
    euid: int,
    operator: str | None,
    level1: Sequence[Check],
    second: Level2,
) -> Level3:
    """The trust checks for the product of `second` and the side effects of
    the probes (level 2's `publisher.precheck`, this `trust-status`).
    Gated as level 2 (root, level 1 reached): nothing is asked otherwise.
    Never changes level 1 or 2."""
    if euid != ROOT_UID:
        return _unasked3(UNAVAILABLE, NEED_SUDO)
    if not level1_reached(level1):
        return _unasked3(NOT_CHECKED, _LEVEL1_FIRST)
    ctx = _Level3(snap, layout, True, operator=operator, perimeter=second.perimeter)
    checks = [_run(i, check, ctx) for i, check in _LEVEL3]
    effects = [SIDE_EFFECT_PRECHECK, *([SIDE_EFFECT_TRUST] if ctx.asked else [])]
    return Level3(checks, tuple(effects))


def _unasked3(status: Status, reason: str) -> Level3:
    return Level3([Check(i, status, reason, _now()) for i in LEVEL3_IDS], ())


def _binding_check(ctx: _Level3) -> str:
    """`trust.toml` reads and parses (live, like `publisher.toml`)."""
    binding = _binding(ctx)
    host, repo, branch = binding.root_id
    return f"корень {host} {repo} {branch}; df-trust {binding.df_trust_full_name}"


def _binding(ctx: _Level3) -> TrustBinding:
    if ctx.binding is None:
        ctx.binding = _read_binding(ctx)
    if isinstance(ctx.binding, _Outcome):
        raise ctx.binding
    return ctx.binding


def _read_binding(ctx: _Level3) -> TrustBinding | _Outcome:
    """The binding root reads itself; unparsable during a live write is
    withheld (`_writing`, as `etc.schemas` does for `publisher.toml`)."""
    try:
        binding = load_trust(ctx.layout.etc / "trust.toml")
    except ConfigError as exc:
        if _writing(ctx):
            return _Outcome(UNAVAILABLE, _LIVE_WRITE)
        return _Outcome(BROKEN, f"trust.toml: {exc}")
    return _Outcome(BROKEN, NO_BINDING) if binding is None else binding


def _bound(ctx: _Level3) -> TrustBinding:
    """The binding for a check that needs it: «не проверено» without it."""
    try:
        return _binding(ctx)
    except _Outcome as exc:
        raise _Outcome(NOT_CHECKED, f"trust.binding: {exc.reason}") from exc


def _history(ctx: _Level3) -> str:
    """Spec 6.2 (O14) and 4.1 rev 11: the binding has the history key, at
    `/etc/df/history-gh`; the profile is root's, group `df-history` by its
    `groups.toml` record, `PROFILE_DIR_MODE`/`PROFILE_FILE_MODE`, no ACL
    (judged by `lstat`, never opened); the group's members are exactly
    `history_members(operator)`."""
    binding = _bound(ctx)
    if binding.history_gh_config_dir is None:
        raise _Outcome(BROKEN, NO_HISTORY_KEY)
    profile = ctx.layout.history_gh
    if binding.history_gh_config_dir != str(profile):
        raise _broken([f"history_gh_config_dir ≠ {profile}"])
    gid = _history_gid(ctx.layout)
    parts: list[_Part] = [(BROKEN, p) for p in _profile_problems(profile, gid)]
    if ctx.operator is None:
        parts.append((UNAVAILABLE, f"состав {HISTORY_GROUP}: {NO_OPERATOR}"))
    else:
        members = history_members(ctx.operator)
        problems = plat.group_problems(ctx.layout, HISTORY_GROUP, members)
        parts += [(BROKEN, p) for p in problems]
    _judge(parts)
    return f"профиль {profile}; состав {HISTORY_GROUP} точный"


def _history_gid(layout: Layout) -> int | None:
    """The GID `groups.toml` records for `df-history` (`None`: no record;
    `group_problems` names it)."""
    try:
        record = load_groups(layout.groups).get(HISTORY_GROUP)
    except ConfigError:
        return None
    return None if record is None else record.gid


def _profile_problems(profile: Path, gid: int | None) -> list[str]:
    """The history profile as `setup_trust.write_binding` leaves it: the
    files of `profile_files` (names only; the token is never read)."""
    try:
        st = os.lstat(profile)
    except FileNotFoundError:
        return [f"нет {profile}"]
    if not stat.S_ISDIR(st.st_mode):
        return [f"{profile}: не каталог"]
    problems = _profile_object(profile, st, gid, setup_trust.PROFILE_DIR_MODE)
    names = set(os.listdir(profile))
    wanted = setup_trust.profile_files("")
    problems += [f"нет {profile / n}" for n in sorted(set(wanted) - names)]
    for name in sorted(names):
        path = profile / name
        found = os.lstat(path)
        if not stat.S_ISREG(found.st_mode):
            problems.append(f"{path}: не обычный файл")
            continue
        problems += _profile_object(path, found, gid, setup_trust.PROFILE_FILE_MODE)
    return problems


def _profile_object(
    path: Path, st: os.stat_result, gid: int | None, mode: int
) -> list[str]:
    problems: list[str] = []
    owner = fsutil.owner_uid(st)
    if owner != plat.ROOT_UID:
        problems.append(f"{path}: владелец uid {owner}, не root")
    if gid is not None and st.st_gid != gid:
        problems.append(f"{path}: группа gid {st.st_gid}, {HISTORY_GROUP} — {gid}")
    if stat.S_IMODE(st.st_mode) != mode:
        problems.append(
            f"{path}: права {stat.S_IMODE(st.st_mode):04o}, нужны {mode:04o}"
        )
    if acl.acl_present_path(path):
        problems.append(f"{path}: есть ACL")
    return problems


def _trust_status(ctx: _Level3) -> str:
    """Root's `trust-status` (spec 5.2), its schema and root checked by
    `request.parse_trust_status`: `clear` is `ok` with the unverified
    premises listed (the closed set of `trust_routes.UNVERIFIED`); `blocked`
    is «нарушено» with its findings; an unfinished observation (matched by
    the prefix `trust_observation_unfinished:`) or any other `unavailable`
    is «данные недоступны»."""
    status = _status(ctx)
    unknown = sorted(set(status.unverified) - UNVERIFIED)
    if unknown:
        raise _broken([f"неизвестная непроверенная предпосылка {c}" for c in unknown])
    root = _bound(ctx).root_id
    mine, old = _events_of(status, root)
    notes = [
        *(f"не проверено: {code}" for code in status.unverified),
        *_listing_notes(status, old),
    ]
    if status.state == "clear":
        head = f"clear; снимок {status.snapshot_sha}, эпоха {status.last_epoch}"
        return "; ".join([head, *notes])
    if status.state == "blocked":
        found = [
            setup_trust.explain(f"находка {f['kind']} ({f['role']}): {f['detail']}")
            for f in status.findings
        ]
        if status.blocking is not None:
            listed = [setup_trust.explain(_event_text(e)) for e in mine]
            raise _broken(["blocked", *(listed or found or [UNNAMED_BLOCK]), *notes])
        raise _broken(["blocked", *(found or [UNNAMED_BLOCK]), *notes])
    raise _Outcome(UNAVAILABLE, _unavailable_reason(status))


#: How doctor names each kind of a listed blocking event.
_KIND_TEXT = {
    "finding": "находка",
    "record_violation": "нарушение (record-violation)",
    "app_installation": "установка App на источнике доверия",
    "conservative": "консервативное событие (К)",
    "db_restore": "восстановление базы",
    "unknown": "событие неизвестного вида",
}


def _events_of(
    status: TrustStatus, root: tuple[str, int, str]
) -> tuple[list[BlockingRow], list[BlockingRow]]:
    """The listed blocking events: the current root's and the old roots'."""
    events = () if status.blocking is None else status.blocking.events
    return (
        [e for e in events if e.root == root],
        [e for e in events if e.root != root],
    )


def _event_text(event: BlockingRow) -> str:
    """`событие N (<kind>, <state>[ E]): <reason>`; a К without a cause
    says so."""
    when = event.state if event.epoch is None else f"{event.state} E{event.epoch}"
    reason = event.reason
    if reason is None:
        reason = "причина не названа" if event.kind == "conservative" else ""
    head = f"событие {event.event_id} ({_KIND_TEXT[event.kind]}, {when})"
    return f"{head}: {reason}" if reason else head


def _old_root_text(event: BlockingRow) -> str:
    host, repo, branch = event.root
    return (
        f"прежний корень {host} {repo} {branch}: {_event_text(event)} — не "
        "блокирует текущий корень"
    )


def _listing_notes(status: TrustStatus, old: Sequence[BlockingRow]) -> list[str]:
    """The old roots' events (labelled), how many were not listed; without
    the publisher's listing, the gap itself (`OTHER_ROOTS`)."""
    if status.blocking is None:
        return [OTHER_ROOTS]
    notes = [_old_root_text(e) for e in old]
    omitted = status.blocking.omitted
    if omitted:
        notes.append(f"не проверено: ещё {omitted} блокирующих событий не перечислены")
    return notes


def _unavailable_reason(status: TrustStatus) -> str:
    """Why an `unavailable` answer is: an unfinished observation (matched by
    the prefix `trust_observation_unfinished:`) or the publisher's reason."""
    reason = status.reason or "без причины"
    if reason.startswith(f"{UNFINISHED}:"):
        return f"наблюдение не завершено ({reason}): повторите doctor"
    return setup_trust.explain(f"unavailable: {reason}")


def _status(ctx: _Level3) -> TrustStatus:
    """The one `trust-status` of the run (asked only with a binding)."""
    if ctx.status is None:
        try:
            ctx.status = _ask_trust_status(ctx, _bound(ctx))
        except _Outcome as exc:
            ctx.status = exc
    if isinstance(ctx.status, _Outcome):
        raise ctx.status
    return ctx.status


def _ask_trust_status(ctx: _Level3, binding: TrustBinding) -> TrustStatus:
    """`setup_trust.trust_status_call` (its call timeout; Task 16 sets the v2
    ones): no answer -- a timeout included -- is «не проверено», never
    «нарушено»."""
    ctx.asked = True
    try:
        return setup_trust.read_trust_status(
            ctx.layout, binding.root_id, purpose="doctor"
        )
    except OSError as exc:
        raise _Outcome(NOT_CHECKED, f"trust-status: нет ответа: {exc}") from exc
    except BadResponse as exc:
        raise _Outcome(BROKEN, f"trust-status: {exc}") from exc


def _unsupported_size(ctx: _Level3) -> str:
    """Spec 6.1 «Размер вердикта»: no `unsupported_size` -- `ok` only on a
    `clear` root (spec 2.4: an unavailable fact is never green). Root
    derives what it can itself: its own binding's limits (`binding_problem`,
    the publisher's first check). A blocked root does not name its cause
    (`UNNAMED_BLOCK`): «не проверено», with the exit of `unsupported_size:
    epochs` as one possibility only; `unavailable` is «данные недоступны»
    with `trust.status`'s reason."""
    problem = binding_problem(_bound(ctx))
    if problem is not None:
        raise _broken([f"{_UNSUPPORTED}{problem}"])
    status = _status(ctx)
    # Defensive: the publisher ends an `unsupported_size` verdict by К and
    # names it in the К event's reason (`blocking`), not in this reason.
    if status.reason is not None and _UNSUPPORTED in status.reason:
        raise _broken([setup_trust.explain(status.reason)])
    if status.state == "clear":
        return ""
    if status.state != "blocked":
        raise _Outcome(UNAVAILABLE, _unavailable_reason(status))
    if status.blocking is None:  # an answer without the listing
        raise _Outcome(NOT_CHECKED, f"{UNNAMED_BLOCK}; {EPOCHS_MAYBE}")
    return _size_of_listing(status, _bound(ctx).root_id)


def _size_of_listing(status: TrustStatus, root: tuple[str, int, str]) -> str:
    """A blocked root with the publisher's listing: a К event of the current
    root naming `unsupported_size: <field>` is «нарушено» with that field
    (the exit of `epochs` only then, `setup_trust.explain`). The publisher
    always names a typed status in its К event (`trust_state.apply_status`
    with the verdict's reason), so a К event without a cause is another К
    (a changed context, a restore) -- a note of `ok`. Events not listed
    are «не проверено». The old roots' events never count (labelled in
    `trust.status`)."""
    assert status.blocking is not None
    mine, _old = _events_of(status, root)
    k_events = [e for e in mine if e.kind == "conservative"]
    named = [e for e in k_events if e.reason and _UNSUPPORTED in e.reason]
    if named:
        raise _broken([setup_trust.explain(_event_text(e)) for e in named])
    if status.blocking.omitted:
        omitted = status.blocking.omitted
        raise _Outcome(NOT_CHECKED, f"ещё {omitted} блокирующих событий не перечислены")
    others = [_event_text(e) for e in k_events]
    return f"{_K_NOT_SIZE}: {'; '.join(others)}" if others else ""


#: A К event without a named cause is not an `unsupported_size` one.
_K_NOT_SIZE = "К без названной причины (не unsupported_size)"


#: The exit of `unsupported_size: epochs`, offered only as one possible cause.
EPOCHS_MAYBE = (
    "размер не проверяем; если причина — unsupported_size: epochs (журнал эпох "
    f"длиннее 64), выход: {setup_trust.EPOCHS_EXIT}"
)


def _perimeter_schema(ctx: _Level3) -> str:
    """The perimeter database's schema (Task 8): schema `"1"` is
    «нарушено» with `MIGRATION_REQUIRED` (the publisher refuses to open it
    but for `roles setup`'s migration). Read from level 2's perimeter line
    (the `self-check` answer) and the `trust-status` reason; a database the
    publisher opened is the current schema."""
    line = ctx.perimeter
    try:
        reason = _status(ctx).reason or ""
    except _Outcome as exc:
        reason = exc.reason
    if MIGRATION_REQUIRED in reason or (line and MIGRATION_REQUIRED in line.reason):
        raise _Outcome(BROKEN, MIGRATION_REQUIRED)
    if (
        line is not None
        and line.status in (OK, BROKEN)
        and line.reason.startswith(PERIMETER_EVENTS)
    ):
        return f"схема {SCHEMA_VERSION}"
    raise _Outcome(UNAVAILABLE, f"{PERIMETER_ID}: {line.reason if line else 'нет'}")


_LEVEL3: tuple[tuple[str, Callable[[_Level3], str]], ...] = (
    ("trust.binding", _binding_check),
    ("trust.history", _history),
    ("trust.status", _trust_status),
    ("trust.unsupported_size", _unsupported_size),
    ("perimeter.schema", _perimeter_schema),
)


# --- the external calls (the tests' seams) ---


def role_check(layout: Layout, role: str) -> plat.RunResult:
    """`df-launch <role> check` from root (`sudo -n -u <role>`). The tests'
    seam."""
    wrapper = layout.libexec / "df-launch"
    argv = [SUDO, "-n", "-u", role, str(wrapper), role, "check"]
    return plat.run_command(argv, timeout=ROLE_TIMEOUT)


def role_access(role: str, flag: str, path: Path) -> plat.RunResult:
    """`test <flag> <path>` as `role` from root: the kernel's answer for the
    role's uid and groups. The tests' seam."""
    argv = [SUDO, "-n", "-u", role, TEST, flag, str(path)]
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


def publisher_precheck(layout: Layout) -> plat.RunResult:
    """The publisher's own precheck command (`precheck.command`) as
    `df-publisher` (root runs `sudo -n -u df-publisher sudo -n -u df-precheck
    <libexec>/df-precheck`) with `PRECHECK_PROBE` on stdin. The tests'
    seam."""
    argv = [SUDO, "-n", "-u", perimeter_client.PUBLISHER, *precheck.command(layout)]
    return plat.run_with_input(argv, PRECHECK_PROBE, timeout=ROLE_TIMEOUT)


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
_LEVEL4 = "уровень 4: не проверено (этап 4)"
#: Stage 3a checks only the trust part of level 3 (spec 2.4): the protection
#: plan, `ACT_MODE[P]` and the agents' rights are stage 3b's, so level 3 is
#: never «достигнут» here.
_LEVEL3_REST = "уровень 3: не проверено (этап 3b); проверки доверия: ok"
_LEVEL3_HEADER = "уровень 3 (доверие, этап 3a; на уровни 1 и 2 не влияет):"
_SIDE_EFFECTS = "побочные эффекты проб (спека §8):"


def level3_brief(checks: Sequence[Check], second: Level2, third: Level3) -> str:
    """Level 3's brief line: not reached below level 2, else the first
    trust check that is not `ok`, else `_LEVEL3_REST`."""
    first = next((c for c in third.checks if c.status != OK), None)
    if not level2_reached(checks, second.checks):
        return f"{_NOT_REACHED3} — уровень 2 не достигнут"
    if first is not None:
        return f"{_NOT_REACHED3} — {first.id}: {_verdict(first)}"
    return _LEVEL3_REST


def render_brief_product(
    checks: Sequence[Check], second: Level2, third: Level3 | None = None
) -> list[str]:
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
    level3_line = (
        "уровень 3: не проверено (этап 3)"
        if third is None
        else level3_brief(checks, second, third)
    )
    return [render_brief(checks), line, level3_line, _LEVEL4, f"профиль: {profile}"]


def render_text_product(
    checks: Sequence[Check],
    second: Level2,
    roles: Sequence[RoleProbe],
    notes: Sequence[str] = (),
    third: Level3 | None = None,
) -> str:
    """The brief lines, every check of levels 1 and 2, the perimeter and
    operator lines; level 3's checks apart and the side effects of the
    probes; the warnings and the models section."""
    lines = render_brief_product(checks, second, third)
    lines += [f"  {c.id}: {_verdict(c)}" for c in [*checks, *second.checks]]
    lines += [f"  {c.id}: {_verdict(c)}" for c in (second.perimeter, *second.rights)]
    if third is not None:
        lines.append(_LEVEL3_HEADER)
        lines += [f"  {c.id}: {_verdict(c)}" for c in third.checks]
        if third.side_effects:
            lines.append(_SIDE_EFFECTS)
            lines += [f"  {e}" for e in third.side_effects]
    lines += list(notes)
    lines.append("модели (уровень 2, на уровень 1 не влияют):")
    lines += [f"  {r.role}: {r.status}" for r in roles]
    return "\n".join(lines)


def render_json_product(
    checks: Sequence[Check],
    second: Level2,
    probes: Sequence[RoleProbe],
    notes: Sequence[str] = (),
    third: Level3 | None = None,
) -> str:
    """`render_json`'s keys and `product`, `profile`, `level2`,
    `level2_checks`, `perimeter`, `operator_rights`, `level3`,
    `level3_checks`, `side_effects`, `level4`."""
    data = _json_data(checks, probes, notes)
    reached = level2_reached(checks, second.checks)
    data |= {
        "product": second.product,
        "profile": second.profile,
        "level2": "reached" if reached else "not reached",
        "level2_checks": [asdict(c) for c in second.checks],
        "perimeter": asdict(second.perimeter),
        "operator_rights": [asdict(c) for c in second.rights],
        "level3": "not checked (stage 3)"
        if third is None
        else _level3_state(checks, second, third),
        "level3_checks": [] if third is None else [asdict(c) for c in third.checks],
        "side_effects": [] if third is None else list(third.side_effects),
        "level4": "not checked (stage 4)",
    }
    return _dumps(data)


def _level3_state(checks: Sequence[Check], second: Level2, third: Level3) -> str:
    """`not reached`, or `not checked (stage 3b)` when the trust part holds."""
    trust_ok = all(c.status == OK for c in third.checks)
    held = level2_reached(checks, second.checks) and trust_ok
    return "not checked (stage 3b)" if held else "not reached"


def _verdict(check: Check) -> str:
    return f"{check.status}: {check.reason}" if check.reason else check.status
