"""Named crash windows of the install (Task 9, part 9-C).

Every test runs in `fake_world`: the install world of 9-B with the OS-crash
model of `tests/df/crash_model.py` (only synced effects survive
`os_crash()`). A process crash is a model trap or `kill_now` (the dying
process runs no handler, R7). Every retry goes the real path -- the entry's
writer half or bootstrap: lock, `settle_on_acquire`, `main`,
`recover_pending`, `drive` -- never `drive` alone. The crash matrix and its
twins are 9-D's.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from df import (
    accept,
    admin,
    cli,
    fsutil,
    install,
    journal,
    launcher_entry,
    receive,
    routes,
)
from df import build as build_mod
from df import platform as plat
from df.journal import StepFailed, read_journal
from df.platform import RunResult
from tests.df.conftest import BUILD_UID
from tests.df.crash_model import Point
from tests.df.install_world import (
    FIRST,
    OTHER_SHA,
    PLATFORM,
    SECOND,
    SHA,
    World,
    at_rest_problems,
    committed_problems,
    current_id,
    installed,
    leftover_ids,
    unsynced_besides_checkout,
)
from tests.df.route_world import KEY

UPGRADE = f"{SHA[:12]}-{install.plat.detect_platform()}-2"


def fault(op: str, path: Path) -> Callable[[Point], bool]:
    return lambda p: p.kind == "fault" and p.op == op and p.path == path


def any_sync(p: Point) -> bool:
    return p.kind == "sync"


def complete(w: World, release_id: str, sha: str = SHA) -> None:
    """One more retry commits `release_id`; an OS crash then changes nothing."""
    assert w.retry() == 0
    assert committed_problems(w, release_id, w.cfg(sha)) == []
    w.m.os_crash()
    assert committed_problems(w, release_id, w.cfg(sha)) == []


def done_steps(w: World) -> list[str]:
    return [str(r["step"]) for r in read_journal(w.lay.journal) if r["event"] == "done"]


# ---------- the model on a real install ----------


@pytest.mark.parametrize("mark", ["verified", "admin"])
@pytest.mark.parametrize("crash", ["process", "os"])
def test_os_crash_model_keeps_only_synced_effects(
    fake_world: World, mark: str, crash: str
) -> None:
    """The mark's `rename` is visible, not durable. A process crash leaves it
    (the retry settles it and goes on from the stage); an OS crash takes it
    back, so the retry repeats `transfer_check` (for `verified`) and the
    admin code (for `admin`)."""
    w = fake_world
    calls: list[str] = []
    real_transfer, real_admin = routes.transfer_check, admin.install_admin

    def transfer(*a: object, **kw: object) -> object:
        calls.append("transfer_check")
        return real_transfer(*a, **kw)  # type: ignore[arg-type]

    def install_admin(*a: object, **kw: object) -> object:
        calls.append("install_admin")
        return real_admin(*a, **kw)  # type: ignore[arg-type]

    w.mp.setattr(routes, "transfer_check", transfer)
    w.mp.setattr(admin, "install_admin", install_admin)
    marks = w.lay.marks_dir / f"{FIRST}.toml"
    stage = f'stage = "{mark}"'

    def at_mark(p: Point) -> bool:
        return fault("renamed", marks)(p) and stage in marks.read_text()

    w.m.trap(at_mark)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert str(marks.relative_to(w.lay.root)) in unsynced_besides_checkout(w)
    if crash == "os":
        w.m.os_crash()
    calls.clear()
    assert w.retry() == 0
    redo = {
        ("verified", "process"): ["install_admin"],
        ("admin", "process"): [],
        ("verified", "os"): ["transfer_check", "install_admin"],
        ("admin", "os"): ["install_admin"],
    }
    assert calls == redo[(mark, crash)]
    assert committed_problems(w, FIRST, w.cfg()) == []


# ---------- visible, not durable, then a retry (S1-R5-N1) ----------


def new_entry_code(w: World) -> None:
    """The next release brings another entry text: its command files differ
    from the active release's, so `L` is false and `launcher` runs (with the
    same text `L` already holds after `activate`, 9-A C3)."""
    real = install.fetch_references

    def references(*a: object, **kw: object) -> dict[str, Path]:
        refs = real(*a, **kw)  # type: ignore[arg-type]
        entry = refs["devtools"] / "df" / "launcher_entry.py"
        entry.write_text(entry.read_text() + "# the next release\n")
        return refs

    w.mp.setattr(install, "fetch_references", references)


WINDOWS: dict[str, Callable[[World], Path]] = {
    "releases.toml": lambda w: w.lay.etc / "releases.toml",
    "bin/darkfactory": lambda w: w.lay.bin_dir / "darkfactory",
    "usr-local-link": lambda w: w.lay.local_bin / "darkfactory",
}


@pytest.mark.parametrize("window", sorted(WINDOWS))
@pytest.mark.parametrize("start", ["first-install", "upgrade"])
def test_visible_unsynced_then_retry_then_os_crash(
    fake_world: World, start: str, window: str
) -> None:
    """S1-R5-N1: a process crash after the `rename`, before the parent's
    sync; a retry; an OS crash -- at the retry's first sync (R49, depth 2)
    and after it completes, both from one branch point. `current` always
    names a registered intact release, the command is there, and
    `pending.toml` goes only after it."""
    w = fake_world
    if start == "upgrade":
        installed(w)
        new_entry_code(w)
        run, target = (lambda: w.writer(["install"])), UPGRADE
    else:
        run, target = (lambda: w.bootstrap(w.init_argv())), FIRST
    path = WINDOWS[window](w)
    w.m.trap(fault("renamed", path))
    w.killed(run)
    if (start, window) == ("upgrade", "usr-local-link"):
        # the link is replaced by one with the same target: closed by value
        assert w.m.is_durable(path)
    else:
        assert not w.m.is_durable(path), "the window is open"
    branch = w.m.snapshot()

    w.m.trap(any_sync)
    w.killed(w.retry)
    w.m.os_crash()
    assert at_rest_problems(w) == []
    complete(w, target)

    w.m.restore(branch)
    assert w.retry() == 0
    assert committed_problems(w, target, w.cfg()) == []  # nothing unsynced
    w.m.os_crash()
    assert committed_problems(w, target, w.cfg()) == []
    points = w.m.points
    gone = [p.index for p in points if p.op == "unlinked" and p.path == w.lay.pending]
    command = (w.lay.bin_dir / "darkfactory", w.lay.local_bin / "darkfactory")
    last_command = max(p.index for p in points if p.path in command)
    assert gone and gone[-1] > last_command, "pending.toml goes after the command"


# ---------- rollback over a visible, not durable fact (S1-R6-N1) ----------


@pytest.mark.parametrize("window", ["releases.toml", "tree-removal"])
@pytest.mark.parametrize("mode", ["writer", "bootstrap"])
def test_rollback_with_unsynced_fact_then_os_crash(
    fake_world: World, mode: str, window: str
) -> None:
    """S1-R6-N1 through `main` -> `recover_pending` (not `drive`). `writer`
    is `main(mode="writer")` directly, an isolated check of
    `recover_pending` (I8: through the entry another configuration is
    unreachable). A retry with another configuration rolls back, an OS crash
    follows at once: no unregistered tree without `pending.toml`."""
    w = fake_world
    run = w.writer if mode == "writer" else w.bootstrap
    other = w.init_argv(OTHER_SHA)
    if window == "releases.toml":
        w.m.trap(fault("renamed", w.lay.etc / "releases.toml"))
        w.killed(lambda: w.bootstrap(w.init_argv()))
        assert not w.m.is_durable(w.lay.etc / "releases.toml"), "window open"
    else:
        w.kill_at("verify")
        w.killed(lambda: w.bootstrap(w.init_argv()))
        w.m.trap(fault("unlinked", w.lay.release_dir(FIRST)))
        w.killed(lambda: run(other))
        assert f"opt/df/releases/{FIRST}" in w.m.unsynced()
    w.kill_after_rollback()
    w.killed(lambda: run(other))
    w.m.os_crash()
    assert at_rest_problems(w) == []
    assert not os.path.lexists(w.lay.pending)
    if window == "tree-removal":
        assert FIRST not in leftover_ids(w)
    assert run(other) == 0
    assert committed_problems(w, SECOND, w.cfg(OTHER_SHA)) == []


# ---------- the rollback is durable before pending.toml goes (S1-R7-N1) ----------

TREES = ["release", "admin", "release+admin"]


def first_with_trees(w: World, trees: str) -> None:
    """A first install of `FIRST` interrupted with exactly `trees` durable."""
    w.kill_at("admin" if trees == "release" else "register")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    if trees == "admin":  # a rollback that durably removed the tree only
        w.m.trap(fault("unlinked", w.lay.admin_dir(FIRST)))
        w.killed(lambda: w.bootstrap(w.init_argv(OTHER_SHA)))
        w.m.os_crash()
    want = {"release": ["releases"], "admin": ["admin"]}.get(trees)
    found = [d for d in leftover_ids(w)[FIRST] if d in ("releases", "admin")]
    assert found == (want or ["releases", "admin"])
    if trees != "admin":  # the root as `verify` left it
        assert os.stat(w.lay.release_dir(FIRST)).st_mode & 0o777 == 0o755
    assert not [p for p in unsynced_besides_checkout(w) if FIRST in p]


def rollback_then_os_crash(w: World) -> None:
    w.kill_after_rollback()
    w.killed(lambda: w.bootstrap(w.init_argv(OTHER_SHA)))
    w.m.os_crash()


@pytest.mark.parametrize("trees", TREES)
def test_rollback_durable_before_pending_removed(fake_world: World, trees: str) -> None:
    """S1-R7-N1: bootstrap -> settle -> `recover_pending` -> `cleanup` ->
    `remove_durably(pending)`; an OS crash right after it, before the next
    `drive`: no root tree of the id without `pending.toml`."""
    w = fake_world
    first_with_trees(w, trees)
    rollback_then_os_crash(w)
    assert not os.path.lexists(w.lay.pending)
    assert FIRST not in leftover_ids(w)
    assert at_rest_problems(w) == []
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert committed_problems(w, SECOND, w.cfg(OTHER_SHA)) == []


@pytest.mark.parametrize("parent", ["releases", "admin"])
def test_rollback_durable_twin_without_parent_sync(
    fake_world: World, parent: str
) -> None:
    """The twin: `remove_own_tree` without `sync_dir` of its parent
    (`releases/`, then `admin/`) -- the counterexample must appear."""
    w = fake_world
    first_with_trees(w, "release+admin")
    gone = w.lay.release_dir(FIRST) if parent == "releases" else w.lay.admin_dir(FIRST)
    points = w.m.points

    def after_removal(p: Point) -> bool:
        before = points[p.index - 1]
        return p.path == gone.parent and before.op == "unlinked" and before.path == gone

    w.m.drop_syncs(after_removal)
    rollback_then_os_crash(w)
    assert not os.path.lexists(w.lay.pending)
    assert f"orphan {FIRST}: {parent}" in at_rest_problems(w)


# ---------- verify after the build home's cleanup (S1-R9-N1) ----------


def build_home_spy(w: World, *, kill_after: int | None = None) -> list[str]:
    """`cleanup_build_home` outcomes, in order; a process crash right after
    the `kill_after`-th call (once)."""
    real = plat.cleanup_build_home
    out: list[str] = []
    armed = [kill_after]

    def cleanup(layout: object, rel: str, **kw: object) -> str:
        result = real(layout, rel, **kw)  # type: ignore[arg-type]
        out.append(f"{rel}:{result}")
        if armed[0] == len(out):
            armed[0] = None
            w.m.kill_now(f"after cleanup_build_home {rel}")
        return result

    w.mp.setattr(plat, "cleanup_build_home", cleanup)
    return out


def root_never_inside(w: World, home: Path) -> list[str]:
    """In-process (root) file calls from `df` code inside `home`."""
    seen: list[str] = []
    inside = str(home) + "/"

    def from_df() -> bool:
        """Any `df` frame on the stack (calls via pathlib, shutil, os.walk)."""
        frame = sys._getframe(2)  # noqa: SLF001
        while frame is not None:
            if str(frame.f_globals.get("__name__")).startswith("df."):
                return True
            frame = frame.f_back
        return False

    def watch(name: str) -> None:
        real = getattr(os, name)

        def call(path: object, *a: object, **kw: object) -> object:
            dir_fd = kw.get("dir_fd")
            full = str(path)
            if isinstance(dir_fd, int):
                from tests.df.crash_model import fd_path

                full = f"{fd_path(dir_fd)}/{path}"
            if full.startswith(inside) and from_df():
                seen.append(f"{name} {full}")
            return real(path, *a, **kw)

        w.mp.setattr(os, name, call)

    for name in ("open", "stat", "lstat", "listdir", "scandir", "unlink", "rmdir"):
        watch(name)
    return seen


@pytest.mark.parametrize(
    "case", ["after-tmp", "after-cache", "both-absent", "root-0700"]
)
def test_verify_retry_after_build_home_cleanup(fake_world: World, case: str) -> None:
    """S1-R9-N1: `drive` -> `verify` -> `cleanup_build_home` -> `transfer_check`
    interrupted after `tmp` (after `cache`; both absent from the start; the
    root of `releases/<id>` back at `0700` by an OS crash after `chmod`); the
    retry commits, `cleanup_build_home` answers "absent" for what is gone,
    and root never touches anything inside the build home. The build leaves
    `tmp/<id>`; `cache` is the uv cache a profile with repos would leave."""
    w = fake_world
    home = w.lay.build_home
    w.kill_at("verify")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert os.path.isdir(home / "tmp")
    if case == "both-absent":
        shutil.rmtree(home / "tmp")
    else:
        w.fl.builder_dir(home / "cache")
        (home / "cache" / "f").write_bytes(b"x")
    w.m.baseline()  # a prepared state: what the build left is durable
    stop = {"after-tmp": 1, "after-cache": 2, "both-absent": 2}.get(case)
    outcomes = build_home_spy(w, kill_after=stop)
    if case == "root-0700":
        w.m.trap(fault("chmod", w.lay.release_dir(FIRST)))
    w.killed(w.retry)
    if case == "root-0700":
        w.m.os_crash()
        assert os.stat(w.lay.release_dir(FIRST)).st_mode & 0o777 == 0o700
    outcomes.clear()
    seen = root_never_inside(w, home)
    assert w.retry() == 0
    assert committed_problems(w, FIRST, w.cfg()) == []
    expected = {
        "after-tmp": ["tmp:absent", "cache:removed"],
        "after-cache": ["tmp:absent", "cache:absent"],
        "both-absent": ["tmp:absent", "cache:absent"],
        "root-0700": ["tmp:removed", "cache:removed"],
    }
    assert outcomes == expected[case]
    assert seen == []
    assert not os.path.lexists(home / "tmp")
    assert not os.path.lexists(home / "cache")


def test_cleanup_build_home_absent_differs_from_error(fake_world: World) -> None:
    """`find` exit 0 and empty -> "absent"; exit != 0 (no access to the build
    home) -> `StepFailed`; `launched=False` -> `StepFailed`."""
    w = fake_world
    results = {
        "absent": RunResult(0, b"", b"", launched=True),
        "error": RunResult(1, b"", b"find: Permission denied", launched=True),
        "not-launched": RunResult(127, b"", b"", launched=False),
    }
    got: dict[str, str] = {}
    for name, result in results.items():
        w.mp.setattr(plat, "run_as", lambda *_a, _r=result, **_kw: _r)
        try:
            got[name] = plat.cleanup_build_home(w.lay, "tmp")
        except StepFailed as exc:
            got[name] = f"refused: {exc.step}"
    assert got == {
        "absent": "absent",
        "error": "refused: verify",
        "not-launched": "refused: verify",
    }


# ---------- the admin Python (S1-R10-N2) ----------


@pytest.mark.parametrize("crash", ["process", "process+os"])
def test_admin_python_rename_then_os_crash(fake_world: World, crash: str) -> None:
    """S1-R10-N2: interrupted after the Python's `rename`, before
    `sync_dir(admin-python)`. A process crash leaves `.staging` and the
    Python visible: the retry's settle keeps them and the route removes
    `.staging`. An OS crash also takes both back -- the name `.staging` was
    never synced either -- and the route installs the Python again. `A` is
    never true in an iteration without the Python; the install commits."""
    w = fake_world
    python = w.lay.admin_python / KEY
    staging = w.lay.admin_python / ".staging"
    w.m.trap(fault("renamed", python))
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert os.path.lexists(python) and os.path.lexists(staging)
    if crash == "process+os":
        w.m.os_crash()
        assert not os.path.lexists(python)
        assert not os.path.lexists(staging)
    real_read_for = install.read_for
    a_without_python: list[str] = []

    def read_for(run: install.InstallRun) -> Callable[[], accept.Facts]:
        read = real_read_for(run)

        def checked() -> accept.Facts:
            facts = read()
            if facts.A and not os.path.isdir(python):
                a_without_python.append(accept.classify(facts))
            return facts

        return checked

    w.mp.setattr(install, "read_for", read_for)
    installs: list[str] = []
    real_install = admin.install_admin_python

    def install_python(*a: object, **kw: object) -> object:
        installs.append("install_admin_python")
        return real_install(*a, **kw)  # type: ignore[arg-type]

    w.mp.setattr(admin, "install_admin_python", install_python)
    start = len(w.m.points)
    assert w.retry() == 0
    removed = [p.path for p in w.m.points[start:] if p.op == "unlinked"]
    assert a_without_python == []
    assert staging in removed  # the leftover `.staging`, before or after
    assert installs == ([] if crash == "process" else ["install_admin_python"])
    assert committed_problems(w, FIRST, w.cfg()) == []


# ---------- staging (S1-R8-N1) ----------


def test_verify_retry_after_staging_removed(fake_world: World) -> None:
    """S1-R8-N1: `verify` removed `staging/<id>` and set `0755`, the process
    died before the mark; the retry (bootstrap: no `current`) goes `copied` ->
    `verify` with both `cleanup_staging` steps skipped by their guards."""
    w = fake_world
    real = plat.cleanup_build_home

    def cleanup(layout: object, rel: str, **kw: object) -> str:
        w.mp.setattr(plat, "cleanup_build_home", real)
        w.m.kill_now("before the mark")
        return real(layout, rel, **kw)  # type: ignore[arg-type]

    w.mp.setattr(plat, "cleanup_build_home", cleanup)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert not os.path.lexists(w.lay.staging_dir(FIRST))
    assert os.stat(w.lay.release_dir(FIRST)).st_mode & 0o777 == 0o755
    start = len(w.m.points)
    assert w.retry() == 0
    staging = str(w.lay.staging_dir(FIRST))
    assert not [p for p in w.m.points[start:] if str(p.path).startswith(staging)]
    assert committed_problems(w, FIRST, w.cfg()) == []


@pytest.mark.parametrize("container", ["with-tree", "empty"])
def test_rollback_with_staging_container_then_os_crash(
    fake_world: World, container: str
) -> None:
    """S1-R8-N1: a durable `staging/<id>` (with `tree`, or empty after a
    crash inside `seal`); a retry with another configuration rolls back; an
    OS crash right after `pending.toml` goes -- nothing of the id is left."""
    w = fake_world
    if container == "with-tree":
        w.kill_at("copy")
    else:
        w.m.trap(fault("created", w.lay.staging_dir(FIRST)))
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert os.path.isdir(w.lay.staging_dir(FIRST))
    has_tree = os.path.lexists(w.lay.staging_tree(FIRST))
    assert has_tree is (container == "with-tree")
    rollback_then_os_crash(w)
    assert FIRST not in leftover_ids(w)
    assert at_rest_problems(w) == []
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0


# ---------- copy, activation ----------


@pytest.mark.parametrize("point", ["member", "mid-copy"])
def test_crash_inside_copy_then_rerun(fake_world: World, point: str) -> None:
    """A crash inside `accept_copy` (after the first member; after the copy
    is whole, before `sync_tree`), then an OS crash: the retry removes the
    partial copy (`copy-partial`) or copies anew, and commits."""
    w = fake_world
    target = w.lay.release_dir(FIRST)
    if point == "member":
        real_member = receive._Receiver._member  # noqa: SLF001

        def member(self: object, *a: object, **kw: object) -> None:
            real_member(self, *a, **kw)  # type: ignore[arg-type]
            w.mp.setattr(receive._Receiver, "_member", real_member)  # noqa: SLF001
            w.m.kill_now("after the first member")

        w.mp.setattr(receive._Receiver, "_member", member)  # noqa: SLF001
    else:
        seen: list[int] = []

        def whole(p: Point) -> bool:
            if fault("created", target)(p):
                seen.append(p.index)
            return len(seen) == 2

        w.m.trap(whole)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert os.path.isdir(target)
    branch = w.m.snapshot()
    assert w.retry() == 0
    assert "copy-partial" in done_steps(w)
    assert committed_problems(w, FIRST, w.cfg()) == []
    w.m.restore(branch)
    w.m.os_crash()
    complete(w, FIRST)


AFTER = ["activate", "launcher", "commit-config:install.toml"]


@pytest.mark.parametrize("after", AFTER)
def test_crash_after_activation_completes_same_id(
    fake_world: World, after: str
) -> None:
    """The retry keeps the id: the command is installed, `install.toml` is the
    pending configuration, and `pending.toml` is removed last."""
    w = fake_world
    if after == "commit-config:install.toml":
        w.m.trap(fault("renamed", w.lay.etc / "install.toml"))
    else:
        w.kill_at(after, after=True)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert current_id(w) == FIRST
    start = len(w.m.points)
    assert w.retry() == 0
    tail = [
        (p.op, p.path)
        for p in w.m.points[start:]
        if p.kind == "fault" and p.path != w.lay.generation
    ]
    assert tail[-1] == ("unlinked", w.lay.pending)
    assert committed_problems(w, FIRST, w.cfg()) == []
    w.m.os_crash()
    assert committed_problems(w, FIRST, w.cfg()) == []


@pytest.mark.parametrize("action", ["activate", "commit-config"])
def test_crash_after_activate_before_journal_done(
    fake_world: World, action: str
) -> None:
    """M19, the facts half (the `doctor` half is Task 12): the action's
    effects are durable, its journal `done` is not written. By the facts the
    release is active (`activate`) or committed (`commit-config`)."""
    w = fake_world
    w.kill_at(action, after=True)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    w.m.os_crash()
    rows = [r for r in read_journal(w.lay.journal) if r["step"] == action]
    assert [r["event"] for r in rows] == ["start"]
    assert current_id(w) == FIRST
    assert at_rest_problems(w) == []
    committed = not os.path.lexists(w.lay.pending)
    assert committed is (action == "commit-config")
    if committed:  # the generation stays odd: the next writer closes it (M9)
        assert committed_problems(w, FIRST, w.cfg()) == ["generation odd"]


# ---------- rollback windows (r4) ----------


def test_rollback_after_unsynced_admin_removal_then_os_crash(fake_world: World) -> None:
    """r4: the rollback dies after removing `admin/<id>`, before
    `sync_dir(admin)`; a retry with another configuration rolls back again;
    an OS crash right after -- `admin/<id>` does not come back (settle)."""
    w = fake_world
    w.kill_at("register")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    w.m.trap(fault("unlinked", w.lay.admin_dir(FIRST)))
    w.killed(lambda: w.bootstrap(w.init_argv(OTHER_SHA)))
    assert f"opt/df/admin/{FIRST}" in w.m.unsynced()
    rollback_then_os_crash(w)
    assert not os.path.lexists(w.lay.admin_dir(FIRST))
    assert at_rest_problems(w) == []


def test_rollback_after_verify_chmod(fake_world: World) -> None:
    """Interrupted after `chmod 0755` in `verify`; a retry with another
    configuration removes the tree (the root's mode does not matter)."""
    w = fake_world
    w.m.trap(fault("chmod", w.lay.release_dir(FIRST)))
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert os.stat(w.lay.release_dir(FIRST)).st_mode & 0o777 == 0o755
    rollback_then_os_crash(w)
    assert FIRST not in leftover_ids(w)
    assert at_rest_problems(w) == []
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert committed_problems(w, SECOND, w.cfg(OTHER_SHA)) == []


# ---------- readers, current ----------


def test_reader_interleaved_with_install_commit(fake_world: World) -> None:
    """A reader between reading `current` and the files; the install moves
    entirely to B meanwhile without waiting. The reader gets one state
    whole (B, by its retry), never a mix; `main(mode="reader")` accepts it."""
    w = fake_world
    installed(w)
    paths = launcher_entry.EntryPaths(str(w.lay.root))
    before = launcher_entry.read_parts(paths)
    real_read = launcher_entry.read_optional
    codes: list[int] = []

    def read_optional(path: Path) -> bytes | None:
        if not codes:
            codes.append(w.writer(["install"]))
        return real_read(path)

    w.mp.setattr(launcher_entry, "read_optional", read_optional)
    snap = launcher_entry.take_snapshot(paths, "reader")
    w.mp.setattr(launcher_entry, "read_optional", real_read)
    after = launcher_entry.read_parts(paths)
    assert codes == [0], "the install did not wait for the reader"
    assert before["current_id"] == FIRST and after["current_id"] == UPGRADE
    assert snap.stable and snap.parts == after
    read = launcher_entry.typed_read(__import__("df.state").state, snap)
    code = cli.main(
        ["models", "list"], mode="reader", read=read, lock_fd=None, layout=w.lay
    )
    assert code == 0  # parsed and listed from the snapshot


def test_process_crash_after_current_rename_then_os_crash(fake_world: World) -> None:
    """r3: a process crash between `rename` of `current` and `sync_dir(opt)`;
    a retry; an OS crash after `commit-config` -- `current` and
    `install.toml` agree (the counterexample of r3-local-check)."""
    w = fake_world
    installed(w)
    w.m.trap(fault("renamed", w.lay.current))
    w.killed(lambda: w.writer(["install"]))
    assert current_id(w) == UPGRADE
    assert "opt/df/current" in w.m.unsynced()
    assert w.retry() == 0
    assert committed_problems(w, UPGRADE, w.cfg()) == []  # nothing unsynced
    w.m.os_crash()
    assert committed_problems(w, UPGRADE, w.cfg()) == []


# ---------- the build account's home (R21) ----------


@pytest.mark.parametrize("settle", [True, False])
def test_home_found_by_retry_is_settled_before_ready(
    fake_world: World, settle: bool
) -> None:
    """R21 (Task 5), S1-R6-N1: `rename_noreplace` of the home -> a process
    crash -> a retry (lock, `settle_on_acquire`, the real
    `create_build_user`) -> `ready` -> an OS crash: the home is there. The
    twin without `settle_on_acquire` loses it while the record says ready."""
    from tests.df.account_world import account_world  # noqa: PLC0415

    w = fake_world
    lay = w.lay
    w.mp.setattr(plat, "create_build_user", REAL_CREATE)
    account_world(w.mp)
    os.rmdir(lay.build_home)
    w.m.baseline()

    def attempt() -> None:
        with journal.writer_lock(lay):
            if settle:
                accept.settle_on_acquire(lay)
            plat.create_build_user(lay)

    w.m.trap(fault("renamed", lay.build_home))
    w.killed(attempt)
    assert os.path.isdir(lay.build_home)
    attempt()
    assert REAL_LOAD(lay.accounts)[plat.BUILD_USER].state == "ready"
    w.m.os_crash()
    assert REAL_LOAD(lay.accounts)[plat.BUILD_USER].state == "ready"
    assert os.path.isdir(lay.build_home) is settle


def test_created_home_is_durable_before_ready(fake_world: World) -> None:
    """The home `create_build_user` makes is durable when it returns
    (`sync_dir` of its parent after `rename_noreplace`): an OS crash right
    after `ready` keeps it, with no retry to settle it."""
    from tests.df.account_world import account_world  # noqa: PLC0415

    w = fake_world
    lay = w.lay
    w.mp.setattr(plat, "create_build_user", REAL_CREATE)
    account_world(w.mp)
    os.rmdir(lay.build_home)
    w.m.baseline()
    with journal.writer_lock(lay):
        accept.settle_on_acquire(lay)
        plat.create_build_user(lay)
    w.m.os_crash()
    assert REAL_LOAD(lay.accounts)[plat.BUILD_USER].state == "ready"
    assert os.path.isdir(lay.build_home)


REAL_CREATE = plat.create_build_user
REAL_LOAD = __import__("df.config").config.load_accounts


def test_fsutil_is_the_models(fake_world: World) -> None:
    """The fixture's model owns the sync seams and the boot id."""
    assert fsutil.boot_id() == fake_world.m.boot_id()


# ---------- created directories keep their modes (finding of 9-C) ----------


def test_prep_dirs_keep_their_modes_across_os_crash(fake_world: World) -> None:
    """Step 2 creates its directories with exact modes (`fchmod` after
    `mkdir 0700`, M3). The mode is the directory's own metadata: durable by
    a sync of the directory, not of its parent (model R2; spec: protocol
    `verify_chmod`). An OS crash right after step 2 must not leave
    `staging`/`.quarantine` at `0700` -- the retry's step 2 would refuse
    them forever («права 0700, нужны 0711») -- nor `/opt`, `/var/lib/df` at
    `0700`, closing the command to every non-root user."""
    w = fake_world
    real = install._make_prep_dirs  # noqa: SLF001
    made = {str(p.relative_to(w.lay.root)) for p, _ in install.prep_dirs(w.lay)}
    unsynced: list[str] = []

    def prep(layout: object) -> None:
        real(layout)  # type: ignore[arg-type]
        w.mp.setattr(install, "_make_prep_dirs", real)
        unsynced.extend(p for p in w.m.unsynced() if p in made | {"opt"})
        w.m.kill_now("after step 2")

    w.mp.setattr(install, "_make_prep_dirs", prep)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert unsynced == []
    w.m.os_crash()
    for path, mode in install.prep_dirs(w.lay):
        assert os.stat(path).st_mode & 0o7777 == mode, path
    assert w.bootstrap(w.init_argv()) == 0
    assert committed_problems(w, FIRST, w.cfg()) == []


@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_lock_dirs_keep_their_modes_across_os_crash(
    fake_world: World, umask: int
) -> None:
    """`writer_lock` creates `/var/lib/df` and `install/` (`0755`, R18) with
    `fchmod` after `mkdir`: under a root umask `077` the mode is set by the
    `fchmod` alone and must be durable before the lock is used."""
    w = fake_world
    old = os.umask(umask)
    try:
        with journal.writer_lock(w.lay):
            dirs = {"var", "var/lib", "var/lib/df", "var/lib/df/install"}
            assert [p for p in w.m.unsynced() if p in dirs] == []
    finally:
        os.umask(old)
    w.m.os_crash()
    for path in (w.lay.var, w.lay.install_dir):
        assert os.stat(path).st_mode & 0o7777 == 0o755, path


@pytest.mark.parametrize("crash", ["process", "os"])
def test_account_created_through_main(fake_world: World, crash: str) -> None:
    """9-C Minor 8 (the D4 carry): the real `create_build_user` and
    `verify_build_user` through `main` -- bootstrap `init`, step 3's `do()`
    after the lock and `settle_on_acquire`. Killed right after the home's
    `rename_noreplace` (then, for `os`, an OS crash): the retry through
    `main` settles what it finds and commits; an OS crash after the commit
    keeps the home and the `ready` record."""
    from tests.df.account_world import account_world  # noqa: PLC0415

    w = fake_world
    lay = w.lay
    outer = plat.run_command  # fake_layout's: ps/pkill stand-ins, else real
    accounts = account_world(w.mp, PLATFORM)
    # the record's uid is the build uid fake_layout gives df-build's trees
    w.mp.setattr(plat, "ID_RANGE", {PLATFORM: range(BUILD_UID, BUILD_UID + 1)})
    fake_os = plat.run_command

    def run_command(argv: list[str], **kw: object) -> RunResult:
        """Account commands go to the fake OS records, the rest as before."""
        if argv[0].startswith(("/usr/bin/dscl", "/usr/sbin/", "/usr/bin/sudo")):
            return fake_os(argv, **kw)
        if argv[0] in ("/usr/bin/getent", "/usr/bin/dseditgroup"):
            return fake_os(argv, **kw)
        return outer(argv, **kw)  # type: ignore[arg-type]

    w.mp.setattr(plat, "run_command", run_command)
    w.mp.setattr(plat, "create_build_user", REAL_CREATE)
    w.mp.setattr(build_mod, "verify_build_user", plat.verify_build_user)

    def whoami(_user: str) -> tuple[int, int]:
        account = REAL_LOAD(lay.accounts)[plat.BUILD_USER]
        return account.uid, account.gid

    w.mp.setattr(build_mod, "whoami_as", whoami)
    os.rmdir(lay.build_home)
    w.m.baseline()
    w.m.trap(fault("renamed", lay.build_home))
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert REAL_LOAD(lay.accounts)[plat.BUILD_USER].state == "creating"
    if crash == "os":
        w.m.os_crash()
    assert w.retry(w.init_argv()) == 0
    assert accounts.commands, "the account was made through the fake OS"
    assert committed_problems(w, FIRST, w.cfg()) == []
    w.m.os_crash()
    assert committed_problems(w, FIRST, w.cfg()) == []
    assert os.path.isdir(lay.build_home)
