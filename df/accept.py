"""Acceptance automaton of spec 3.3: marks, facts, settle and the driver.

The rules are data (`df.install_table`); this module reads the facts of the
table from disk, makes their sources durable before every classification
(`settle`) and runs the loop `settle -> read -> classify -> action -> check`
(`drive`). Actions are injected: `drive` is their only executor.

Ownership is read through one seam, `fsutil.owner_uid`, so a test can model root and
`df-build` while running as one user. Root never opens a tree of `df-build`:
its emptiness is decided by `df-build` itself (`builder_tree_nonempty`).
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn, Protocol, cast

from df import fsutil
from df import install_table as table
from df import platform as plat
from df.config import (
    PY_VERSION,
    STATE_SCHEMAS,
    TOML_ERRORS,
    ConfigError,
    ReleaseEntry,
    load_pending,
    load_releases,
    read_optional,
)
from df.digest import TreeError, tree_digest
from df.journal import StepFailed, journal_event
from df.paths import Layout
from df.toml_emit import dumps

Stage = Literal["built", "copied", "verified", "admin"]
STAGE_ORDER: tuple[Stage, ...] = ("built", "copied", "verified", "admin")
MarkFact = Literal["none", "built", "copied", "verified", "admin"]
RootState = Literal["absent", "b-empty", "b-full", "root-700", "root-755", "root-x"]

#: Owner of root's objects; tests substitute their own uid.
ROOT_UID = 0
MARK_SCHEMA = STATE_SCHEMAS["accept/<id>.toml"]
MARK_MODE = 0o600
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_FIND_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
_FIND_TIMEOUT = 10.0


# ---------- the acceptance mark ----------


@dataclass(frozen=True)
class AcceptMark:
    """The single mark of an acceptance (spec 3.3 "Отметка приёма").

    `install_digest` is the bare hex of `pending.toml`; `tree_digest` is set
    from `copied` on, `admin_digest` and `admin_python` (exact `X.Y.Z`)
    together by `admin`. `built_boot_id` and `uv_version_declared` (the uv
    version `run_build` declared, R29) are written by `built` and carried
    unchanged by every later stage.
    """

    install_digest: str
    stage: Stage
    built_boot_id: str
    tree_digest: str | None
    admin_digest: str | None
    admin_python: str | None
    uv_version_declared: str

    def __post_init__(self) -> None:
        _need(bool(_HEX64.fullmatch(self.install_digest)), "install_digest")
        _need(self.stage in STAGE_ORDER, "stage")
        _need(bool(self.built_boot_id), "built_boot_id")
        _need(bool(self.uv_version_declared), "uv_version_declared")
        copied = self.stage != "built"
        _need(_digest_or_none(self.tree_digest, required=copied), "tree_digest")
        admin = self.stage == "admin"
        _need(_digest_or_none(self.admin_digest, required=admin), "admin_digest")
        python = self.admin_python
        ok = (
            python is None
            if not admin
            else bool(python and PY_VERSION.fullmatch(python))
        )
        _need(ok, "admin_python")


def _need(ok: bool, key: str) -> None:
    if not ok:
        raise ConfigError(f"accept mark: invalid {key}")


def _digest_or_none(value: str | None, *, required: bool) -> bool:
    if value is None:
        return not required
    return required and bool(_DIGEST.fullmatch(value))


def dump_mark(mark: AcceptMark) -> str:
    """Render a mark as `df/accept/v1` TOML (absent optional fields omitted)."""
    data: dict[str, object] = {"schema": MARK_SCHEMA}
    for f in fields(mark):
        value = getattr(mark, f.name)
        if value is not None:
            data[f.name] = value
    return dumps(data)


def parse_mark(text: str) -> AcceptMark:
    """Strictly parse a mark; any defect is a `ConfigError`."""
    try:
        data = tomllib.loads(text)
    except TOML_ERRORS as exc:
        raise ConfigError(f"accept mark: invalid TOML: {exc}") from exc
    _need(data.pop("schema", None) == MARK_SCHEMA, "schema")
    names = {f.name for f in fields(AcceptMark)}
    _need(set(data) <= names, "key")
    _need(all(isinstance(v, str) for v in data.values()), "value type")
    try:
        return AcceptMark(**{n: data.get(n) for n in names})
    except TypeError as exc:
        raise ConfigError(f"accept mark: {exc}") from exc


def load_mark(
    layout: Layout, release_id: str, *, install_digest: str
) -> AcceptMark | None:
    """The valid mark of `release_id`, else `None`.

    Absent, malformed or non-regular (F32), of another configuration, or a
    `built` mark from another boot (the builder's tree is not synced) — `None`.
    """
    try:
        text = read_optional(layout.mark_file(release_id))
        mark = None if text is None else parse_mark(text)
    except ConfigError:
        return None
    if mark is None or mark.install_digest != install_digest:
        return None
    if mark.stage == "built" and mark.built_boot_id != fsutil.boot_id():
        return None
    return mark


def write_mark(layout: Layout, release_id: str, mark: AcceptMark) -> None:
    """Write the mark (wraps `write_atomic`); a lower stage is refused.

    Compared with the valid mark of the same configuration: another
    configuration's or a stale `built` mark is absent and may be overwritten.
    The carried fields must equal the recorded ones. Effects: `write_atomic()`.
    """
    if not layout.marks_dir.is_dir():
        raise StepFailed("mark", f"нет каталога отметок {layout.marks_dir}")
    old = load_mark(layout, release_id, install_digest=mark.install_digest)
    if old is not None:
        if STAGE_ORDER.index(mark.stage) < STAGE_ORDER.index(old.stage):
            raise StepFailed(
                "mark", f"отметка не понижается: {old.stage} → {mark.stage}"
            )
        for key in ("built_boot_id", "uv_version_declared"):
            if getattr(mark, key) != getattr(old, key):
                raise StepFailed("mark", f"{key} отметки не меняется")
    fsutil.write_atomic(layout.mark_file(release_id), dump_mark(mark), MARK_MODE)


def drop_mark(layout: Layout, release_id: str) -> None:
    """Remove the mark (wraps `remove_durably`; route `cleanup` only).

    Effects: `remove_durably()`.
    """
    fsutil.remove_durably(layout.mark_file(release_id))


# ---------- facts ----------


class AdminReady(Protocol):
    """`admin_ready` of Task 8a: admin code digest and exact admin Python."""

    def __call__(
        self,
        layout: Layout,
        release_id: str,
        *,
        expected_admin_digest: str,
        expected_python: str,
    ) -> bool: ...


@dataclass(frozen=True)
class Facts:
    """Exactly the facts of the table (spec 3.3), named as its keys.

    `mark` is the valid mark behind `m`; it is not a key of the table.
    """

    P: bool
    reg: bool
    cur: bool
    L: bool
    intact: bool
    m: MarkFact
    r: RootState
    full: bool
    A: bool
    S: bool
    mark: AcceptMark | None = None

    def table(self) -> dict[str, object]:
        """The point of the table: exactly the keys of `DOMAINS`."""
        return {k: getattr(self, k) for k in table.DOMAINS}


def classify(facts: Facts) -> str:
    """Phase of `facts` by the table (`"unknown"` if no row holds it)."""
    return table.classify(facts.table())


def builder_tree_nonempty(layout: Layout, path: Path) -> bool:
    """`df-build` decides whether its tree is empty; root never opens it.

    Any output, a non-zero code, no launch or the deadline mean non-empty.
    """
    result = plat.run_as(
        plat.BUILD_USER,
        [
            "/usr/bin/find",
            str(path),
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-print",
            "-quit",
        ],
        env=_FIND_ENV,
        cwd=layout.build_home,
        network=False,
        timeout=_FIND_TIMEOUT,
    )
    if not result.launched or result.timed_out or result.returncode != 0:
        return True
    return bool(result.stdout)


def root_state(layout: Layout, release_id: str, *, build_uid: int) -> RootState:
    """Fact `r`: owner and mode of `releases/<id>` (`lstat`, no open).

    A non-directory or a foreign owner is outside the table: `StepFailed`
    ("facts"), a human decides (F20).
    """
    path = layout.release_dir(release_id)
    try:
        st = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return "absent"
    if not stat.S_ISDIR(st.st_mode):
        raise StepFailed("facts", f"{path}: не каталог")
    owner = fsutil.owner_uid(st)
    if owner == ROOT_UID:
        mode = stat.S_IMODE(st.st_mode)
        return {0o700: "root-700", 0o755: "root-755"}.get(mode, "root-x")
    if owner == build_uid:
        return "b-full" if builder_tree_nonempty(layout, path) else "b-empty"
    raise StepFailed("facts", f"{path}: владелец {owner} — ни root, ни df-build")


def read_facts(
    layout: Layout,
    release_id: str,
    *,
    install_digest: str,
    build_uid: int,
    launcher_ready: Callable[[], bool],
    admin_ready: AdminReady,
) -> Facts:
    """Read the facts of the table for `release_id`.

    Admin readiness takes its expected digest and Python from one source:
    the mark for `A` (before registration), the registry entry for `intact`
    (after it); neither the profile nor the manifest is read.
    """
    entry = _registry_entry(layout, release_id)
    mark = load_mark(layout, release_id, install_digest=install_digest)
    r = root_state(layout, release_id, build_uid=build_uid)
    tree = layout.release_dir(release_id)
    intact = entry is not None and (
        r == "root-755"
        and _digest_equals(tree, entry.digest)
        and admin_ready(
            layout,
            release_id,
            expected_admin_digest=entry.admin_digest,
            expected_python=entry.admin_python,
        )
    )
    full = (
        mark is not None
        and mark.tree_digest is not None
        and r in table.ROOT
        and _digest_equals(tree, mark.tree_digest)
    )
    a = (
        mark is not None
        and mark.stage == "admin"
        and admin_ready(
            layout,
            release_id,
            expected_admin_digest=cast(str, mark.admin_digest),
            expected_python=cast(str, mark.admin_python),
        )
    )
    return Facts(
        P=os.path.lexists(layout.pending),
        reg=entry is not None,
        cur=_current_is(layout, release_id),
        L=launcher_ready(),
        intact=intact,
        m=mark.stage if mark is not None else "none",
        r=r,
        full=full,
        A=a,
        S=os.path.lexists(layout.staging_tree(release_id)),
        mark=mark,
    )


def _registry_entry(layout: Layout, release_id: str) -> ReleaseEntry | None:
    path = layout.etc / "releases.toml"
    try:
        if not os.path.lexists(path):
            return None
        entries = load_releases(path).releases
    except ConfigError as exc:
        raise StepFailed("facts", str(exc)) from exc
    return next((e for e in entries if e.id == release_id), None)


def _digest_equals(tree: Path, expected: str) -> bool:
    """A tree the digest refuses (`TreeError`: its content) differs; an
    `OSError` reading it is not a fact of the table: `StepFailed("facts")`,
    a human decides (fail-closed, no cleanup on an I/O error)."""
    try:
        return tree_digest(tree) == expected
    except TreeError:
        return False
    except OSError as exc:
        raise StepFailed("facts", f"{tree}: не читается: {exc}") from exc


def _current_is(layout: Layout, release_id: str) -> bool:
    """`current` is the relative link `releases/<id>` (protocol `activate`)."""
    try:
        target = os.readlink(layout.current)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StepFailed("facts", f"{layout.current}: не ссылка: {exc}") from exc
    return target == f"releases/{release_id}"


# ---------- settle ----------

PathsOf = Callable[[Layout, str | None], tuple[Path, ...]]


def _always(get: Callable[[Layout], Path]) -> PathsOf:
    return lambda layout, _id: (get(layout),)


def _release_root(layout: Layout, release_id: str | None) -> tuple[Path, ...]:
    """The root of `releases/<id>` only if root owns it (its mode is a fact)."""
    if release_id is None:
        return ()
    path = layout.release_dir(release_id)
    try:
        st = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return ()
    root_dir = stat.S_ISDIR(st.st_mode) and fsutil.owner_uid(st) == ROOT_UID
    return (path,) if root_dir else ()


def _staging(layout: Layout, release_id: str | None) -> tuple[Path, ...]:
    if release_id is None:
        return (layout.staging,)
    return (layout.staging, layout.staging_dir(release_id))


def local_bin_trusted(layout: Layout) -> bool:
    """`local_bin=trusted`: `/usr/local/bin` passes the trusted-path policy.

    The one definition for the route guard and for settling `local_bin` (F23).
    """
    return not plat.trusted_path_problems(layout.local_bin)


def _local_bin(layout: Layout, _id: str | None) -> tuple[Path, ...]:
    return (layout.local_bin,) if local_bin_trusted(layout) else ()


#: Names of `install_table.DIRS` -> paths of `Layout` (keys exactly `DIRS`).
SETTLE_PATHS: Mapping[str, PathsOf] = {
    "install": _always(lambda lay: lay.install_dir),
    "accept": _always(lambda lay: lay.marks_dir),
    "etc_df": _always(lambda lay: lay.etc),
    "opt_df": _always(lambda lay: lay.opt),
    "releases": _always(lambda lay: lay.releases),
    "release_root": _release_root,
    "staging": _staging,
    "admin": _always(lambda lay: lay.admin),
    "admin_python": _always(lambda lay: lay.admin_python),
    "opt_bin": _always(lambda lay: lay.bin_dir),
    "local_bin": _local_bin,
    "home_parent": _always(lambda lay: lay.build_home.parent),
}
#: `SETTLE_FIRST` names (not fact sources) -> paths; synced before the facts.
FIRST_PATHS: Mapping[str, PathsOf] = {
    "quarantine": _always(lambda lay: lay.quarantine),
}


def _paths(
    layout: Layout, release_id: str | None, dirs: tuple[str, ...]
) -> tuple[Path, ...]:
    first = [p for d in table.SETTLE_FIRST for p in FIRST_PATHS[d](layout, release_id)]
    rest = [p for d in dirs for p in SETTLE_PATHS[d](layout, release_id)]
    return (*first, *rest)


def settle_paths(layout: Layout, release_id: str) -> tuple[Path, ...]:
    """Paths `settle` syncs: the quarantine first, then `settle_dirs()`."""
    return _paths(layout, release_id, table.settle_dirs())


def acquire_paths(layout: Layout, pending_id: str | None) -> tuple[Path, ...]:
    """Paths of `acquire_dirs()`, the quarantine first; no id — no id paths."""
    return _paths(layout, pending_id, table.acquire_dirs())


def _sync_existing(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.is_dir():
            fsutil.sync_dir(path)


def settle(layout: Layout, release_id: str) -> None:
    """Item 1 of the cycle: sync every existing `settle_paths` directory.

    Tree contents are not touched: they are trusted only through the mark.
    """
    _sync_existing(settle_paths(layout, release_id))


def settle_on_acquire(layout: Layout) -> None:
    """Settle at lock acquisition (spec 3.3); the caller holds `writer_lock`.

    The install directory first, then the id of `pending.toml` (read after it
    is durable), then every other `acquire_paths` directory. An unreadable
    `pending.toml` or id settles only id-independent paths (F33): the
    snapshot and `recover_pending` report it.
    """
    _sync_existing((layout.install_dir,))
    rest = acquire_paths(layout, _pending_id(layout))
    _sync_existing(tuple(p for p in rest if p != layout.install_dir))


def _pending_id(layout: Layout) -> str | None:
    try:
        pending = load_pending(layout.pending)
        if pending is None:
            return None
        layout.release_dir(pending.release_id)
    except ConfigError:
        return None
    return pending.release_id


# ---------- the driver ----------


def drive(
    layout: Layout,
    release_id: str,
    *,
    actions: Mapping[str, Callable[[Facts], None]],
    read: Callable[[], Facts],
    journal: Path,
    run_id: str,
    max_iter: int = 40,
) -> None:
    """The cycle of spec 3.3: the only executor of acceptance actions.

    Each iteration: `settle` -> `read` -> `classify`; `committed` returns,
    `damaged` stops; otherwise the row's action runs, then `settle`, and the
    new phase must be one of the row's `post`. At most `max_iter` actions run
    (`max_iter < 0` is a `ValueError`), then a final classification. Failures are journaled
    (`failed`) and re-raised; directory syncs that lacked F_FULLFSYNC are
    journaled as `sync-note` for the evidence (decision (a)).
    """

    if max_iter < 0:
        raise ValueError(f"max_iter must be >= 0: {max_iter}")

    def log(step: str, event: str, detail: str = "") -> None:
        journal_event(journal, run_id=run_id, step=step, event=event, detail=detail)

    try:
        _drive(layout, release_id, actions, read, log, max_iter)
    except Exception as exc:  # noqa: BLE001 - noted, then re-raised
        try:
            _journal_sync_notes(log)
        except Exception as log_exc:  # noqa: BLE001 - the original failure wins
            exc.add_note(f"журнал: не записано sync-note: {log_exc}")
        raise
    _journal_sync_notes(log)


def _drive(
    layout: Layout,
    release_id: str,
    actions: Mapping[str, Callable[[Facts], None]],
    read: Callable[[], Facts],
    log: Callable[..., None],
    max_iter: int,
) -> None:
    # max_iter actions, then one more classification: reaching the target
    # with the last allowed action is success.
    for _ in range(max_iter):
        facts, row = _observe(layout, release_id, read, log)
        if _terminal(row, log):
            return
        _step(layout, release_id, row, facts, actions, read, log)
    _facts, row = _observe(layout, release_id, read, log)
    if _terminal(row, log):
        return
    _fail(log, StepFailed("drive", f"нет продвижения за {max_iter} итераций"))


def _terminal(row: table.Row, log: Callable[..., None]) -> bool:
    """`committed` is True; a `stop` row (damaged) raises; otherwise False."""
    if row.action == "stop":
        _fail(log, StepFailed(row.phase, "релиз повреждён (§3.8)"))
    return row.phase == "committed"


def _observe(
    layout: Layout,
    release_id: str,
    read: Callable[[], Facts],
    log: Callable[..., None],
) -> tuple[Facts, table.Row]:
    """Head of an iteration: settle, read, classify; a failure is journaled."""
    try:
        settle(layout, release_id)
        facts = read()
        return facts, table.row_of(classify(facts))
    except Exception as exc:  # noqa: BLE001 - journaled, then re-raised
        _fail(log, exc, "" if isinstance(exc, StepFailed) else "facts")


def _step(
    layout: Layout,
    release_id: str,
    row: table.Row,
    facts: Facts,
    actions: Mapping[str, Callable[[Facts], None]],
    read: Callable[[], Facts],
    log: Callable[..., None],
) -> None:
    log(row.action, "start", row.phase)
    try:
        actions[row.action](facts)
        settle(layout, release_id)
        after = classify(read())
        if after not in row.post:
            raise StepFailed(row.action, f"{row.phase} → {after}")
    except Exception as exc:  # noqa: BLE001 - journaled, then re-raised
        _fail(log, exc, row.action)
    log(row.action, "done", f"{row.phase} → {after}")


def _fail(log: Callable[..., None], exc: Exception, step: str = "") -> NoReturn:
    """Journal `failed` (a journal error becomes a note) and raise `exc`."""
    name = step or (exc.step if isinstance(exc, StepFailed) else "drive")
    try:
        log(name, "failed", f"{type(exc).__name__}: {exc}")
    except Exception as log_exc:  # noqa: BLE001 - the original failure wins
        exc.add_note(f"журнал: не записано failed: {log_exc}")
    raise exc


def _journal_sync_notes(log: Callable[..., None]) -> None:
    devices = sorted(fsutil.sync_notes())
    if devices:
        detail = "fullfsync-unsupported: " + ", ".join(f"dev={d}" for d in devices)
        log("drive", "sync-note", detail)
