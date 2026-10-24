"""`darkfactory doctor`: level 1 «окружение» from facts (spec 3.8, 2.4).

Every check reads the one `Snapshot` the entry handed the command (reader
mode, no lock): the configuration, the registry entry and the models text
come from it, never from `/etc/df` again, and `current` is not read as a
link. The journal takes no part. The models section (probe status per role,
spec 3.6) is shown apart: probes are level 2 and never change level 1.

Exit codes (R68): 0 — level 1 reached; 2 — not reached (a transitional
state included); 1 — a program error (`df.cli.main`).
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from df import acl, admin, boundary, digest, fsutil, journal, models, probe, state
from df import build as build_mod
from df import platform as plat
from df.config import KNOWN_SCHEMAS, STAGE_OF, ConfigError
from df.digest import TreeError
from df.journal import StepFailed, Transitional
from df.paths import Layout
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
TRANSITIONAL = (
    "идёт или прервана административная операция: дождитесь её конца; "
    "прерванную повторите от root (darkfactory install или darkfactory models set)"
)
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


def doctor_main(read: ReadResult, layout: Layout, *, euid: int, as_json: bool) -> int:
    """`darkfactory doctor [--json]`: level 1, warnings, the models section.

    `Transitional(g)`: only the transitional state, nothing of a snapshot is
    used. `Stable`: the snapshot as a reader parses it (`parse_snapshot`).
    """
    if isinstance(read, Transitional):
        return _transitional(read.generation, as_json)
    snap = state.parse_snapshot(read.value, layout, mode="reader")
    checks = level1_checks(snap, layout, euid=euid)
    notes = warnings(snap, layout, euid=euid)
    roles = model_lines(snap, layout, euid=euid)
    if as_json:
        print(render_json(checks, roles, notes))
    else:
        print(render_text(checks, roles, notes))
    return EXIT_REACHED if level1_reached(checks) else EXIT_NOT_REACHED


def _transitional(generation: int, as_json: bool) -> int:
    if as_json:
        data = {"level1": "not reached", "state": "transitional"}
        print(json.dumps({**data, "generation": generation}, ensure_ascii=False))
    else:
        print(f"{_NOT_REACHED} — install.transitional: {TRANSITIONAL}")
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


def _run(check_id: str, check: Callable[[_Context], str], ctx: _Context) -> Check:
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
    parsed with the snapshot); every other name of `/etc/df` is judged by the
    listing alone, never opened. A `write_atomic` temp of a known target is a
    live write while a writer's generation bracket is open (the counter moved
    or is odd since the snapshot), else a leftover of a crash."""
    if ctx.snap.models_text is not None:
        try:
            models.parse_models(ctx.snap.models_text)
        except ConfigError as exc:
            raise _Outcome(BROKEN, f"models.toml: {exc}") from exc
    known = _STAGE1_FILES | STAGE_OF.keys()
    unknown = sorted(n for n in _etc_names(ctx.layout) if n not in known)
    temps = [n for n in unknown if _write_temp_of(n) in known]
    others = [n for n in unknown if n not in temps]
    if temps and _writing(ctx):
        raise _Outcome(UNAVAILABLE, _LIVE_WRITE, temps)
    problems = [
        f"осталась временная запись после сбоя: {n}; удалите вручную (sudo rm)"
        for n in temps
    ]
    problems += [f"неизвестный файл /etc/df: {n}" for n in others]
    if not problems:
        return ""
    reasons = problems[: len(temps)]
    if others:
        reasons.append(f"неизвестные файлы /etc/df: {_summary(others)}")
    raise _Outcome(BROKEN, "; ".join(reasons), problems)


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
    data = {
        "level1": "reached" if level1_reached(checks) else "not reached",
        "checks": [asdict(c) for c in checks],
        "warnings": list(notes),
        "models": [asdict(p) for p in probes],
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _verdict(check: Check) -> str:
    return f"{check.status}: {check.reason}" if check.reason else check.status
