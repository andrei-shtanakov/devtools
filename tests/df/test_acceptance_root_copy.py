"""Root acceptance of copy, receive and cleanup on the real host (Task 13-D, R73).

§3.8 lines of the copy group at leaf level (R89): `prepare_dir`, `seal`,
`accept_copy`, `cleanup_tree`, `cleanup_build_home` and `retry_quarantine`
over trees a real `df-build` writes through `run_as`, the approved `tar`
streaming as `df-build` through the real `spawn_as`. The forked and the
silent producer and the quarantine left for the next install go through
`df.cli.main` (bootstrap `init`): their claims are about the command (the
writer lock after it exits, the next run's `copy-partial` route). Runs only
in CI df-stage1.

The OS observer (`tests/df/observe.py`) watches every phase whose source
stays in place: the build (`releases/<id>`, watched from `prepare_dir`'s
`fchown`, with the classification), the copy (`staging/<id>/tree`) and the
cleanup (the root parent that holds the tree: `staging/<id>`, `releases/`,
the quarantine, or `df-build`'s home). `seal` is not inside one: it moves
the source, which the observer refuses at exit, and its own operations on
the tree (`fstatat`, `renameat`) are outside the observed set anyway. A
cleanup's parent is root's own directory, opened by the protocol as the held
parent (`OWN_OPS`); every other access of root to a source is a violation.
An `unobserved` run fails the test (13-D brief), it is never a pass. Digests
of a source are taken before its observation (R94).
"""

from __future__ import annotations

import fcntl
import functools
import os
import re
import stat
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from df import accept, cli, fsutil, install, journal
from df import platform as plat
from df.digest import release_digest
from df.journal import StepFailed, read_journal
from df.leaves import accept_copy, prepare_dir, seal
from df.paths import Layout
from df.receive import CopyLimits, remove_own_tree
from tests.df.conftest import stage1_out
from tests.df.observe import Observation, observe_root_access
from tests.df.root_world import (
    ROLE_ENV,
    Controls,
    RootWorld,
    assert_committed,
    infra,
)

pytestmark = pytest.mark.root

PLATFORM = plat.detect_platform()
LINUX = PLATFORM == "linux-x86_64"
RID = "acc-1"
#: Limits of a leaf copy: small enough that a runaway writer ends it.
LIMITS = CopyLimits(
    max_bytes=256 << 20,
    max_stream_bytes=320 << 20,
    max_objects=50_000,
    max_seconds=120.0,
)
#: The deadline of a copy in the forked/silent producer runs (seconds).
DEADLINE = 45.0
#: The cleanup budget of the growing tree (R89) and its ε.
BUDGET = 5.0
EPSILON = 15.0
#: Root's own ops on the parent a protocol holds (cleanup), per parent kind.
OPEN_OPS = frozenset({"open", "openat"})
LIST_OPS = OPEN_OPS | {"readdir", "getdents", "getdents64"}
#: Seconds to wait for a writer's heartbeat.
BEAT_TIMEOUT = 20.0

#: A tree with the root record, long and non-ASCII names, both link kinds.
LONG_NAME = "long-" + "n" * 150
DEEP = "deep/" + "/".join(["x" * 60] * 4)
NAMES_TREE = (
    "mkdir -p d/sub && printf 'hello\\n' > d/f && chmod 755 d/f"
    f" && printf long > {LONG_NAME}"
    " && printf utf8 > d/été.txt && printf ru > d/файл"
    f" && mkdir -p {DEEP} && printf deep > {DEEP}/f"
    " && ln d/f hard && ln -s d/f sym"
)
#: 16 MiB of padding: the copy is mid-stream when a race acts on the tree.
PAD = (
    "mkdir -p pad && i=0; while [ $i -lt 16 ]; do"
    " /usr/bin/head -c 1048576 /dev/urandom > pad/p$i; i=$((i+1)); done"
)
LIVE_TREE = (
    "mkdir -p d/sub grow && printf 'hello\\n' > d/f && ln -s d/f link"
    " && : > pad-grow && " + PAD
)
#: A non-empty tree whose root and nested directories end `000`.
ZERO_TREE = (
    "mkdir -p a/b/c && printf x > a/b/c/f && printf y > a/g && printf z > h"
    " && chmod 000 a/b/c && chmod 000 a/b && chmod 000 a && chmod 000 ."
)
#: The wrapper of the forked producer: the stream comes from a process in
#: its own session that holds stdout after the archive; the wrapper exits.
FORKED = (
    "/usr/bin/perl -e 'use POSIX (); exit 0 if fork; POSIX::setsid();"
    ' exec @ARGV or die\' /bin/sh -c \'"$@"; exec /bin/sleep 600\' sh "$@"'
)
#: The silent producer: 1 KiB of the real stream, then sleep.
SILENT = '"$@" | /usr/bin/head -c 1024; exec /bin/sleep 600'
#: GNU tar after the pipe is shrunk (`spawn_as` returns after its 2 s check).
DELAYED = 'sleep 5; exec "$@"'
F_SETPIPE_SZ = 1031


class Killed(BaseException):
    """A process kill at a chosen point (never caught by df)."""


# ---------- the copy world ----------


@dataclass
class CopyWorld:
    """A root world with a ready `df-build` and the helpers of this group."""

    world: RootWorld
    label: str
    uid: int
    observations: list[Observation] = field(default_factory=list)

    @property
    def layout(self) -> Layout:
        return self.world.layout

    @contextmanager
    def observing(self, sources: Sequence[Path], phase: str) -> Iterator[Observation]:
        """`observe_root_access` with this world's control and scope; the
        observation is kept as CI evidence."""
        out = stage1_out()
        record = None if out is None else out / "observe" / f"{self.label}-{phase}.json"
        with observe_root_access(
            sources,
            control=self.world.control_dir(),
            workdir=self.world.workdir(),
            scope=self.layout.root,
            record=record,
        ) as obs:
            yield obs
        self.observations.append(obs)

    def as_build(self, script: str, *, check: bool = True) -> plat.RunResult:
        """`script` as `df-build`, from its home (the production `cwd`)."""
        result = plat.run_as(
            plat.BUILD_USER,
            ["/bin/sh", "-c", script],
            env=ROLE_ENV,
            cwd=self.layout.build_home,
            network=False,
            timeout=120,
        )
        if check and (not result.launched or result.returncode != 0):
            infra(f"{plat.BUILD_USER}: {script[:80]}: {result}")
        return result

    def spawn_build(self, script: str) -> plat.SpawnedProducer:
        """A background `df-build` process; `script` must print first."""
        return plat.spawn_as(
            plat.BUILD_USER,
            ["/bin/sh", "-c", script],
            env=ROLE_ENV,
            cwd=self.layout.build_home,
            network=False,
        )

    def built(self, script: str, rid: str = RID) -> Path:
        """`releases/<rid>` made by `prepare_dir`, filled by `df-build`
        and classified, under observation from the `fchown` on (R93)."""
        lay = self.layout
        tree = lay.release_dir(rid)
        with self.observing([], f"build-{rid}") as obs:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(fsutil, "fault_point", _watch_at_chown(obs))
                prepare_dir(lay, rid)
            self.as_build(f"cd {_q(tree)} && {script}")
            facts = accept.read_facts(
                lay,
                rid,
                install_digest="sha256:" + "0" * 64,
                build_uid=self.uid,
                launcher_ready=lambda: False,
                admin_ready=_never_admin,
            )
        require_clean(obs)
        assert facts.r == "b-full", facts
        return tree

    def sealed(self, script: str, rid: str = RID) -> Path:
        """`built`, then `seal`: the tree at `staging/<rid>/tree`."""
        self.built(script, rid)
        seal(self.layout, rid)
        return self.layout.staging_tree(rid)

    def copy(
        self,
        *,
        limits: CopyLimits = LIMITS,
        hook: Callable[[str, str], None] | None = None,
    ) -> str:
        """The leaf `accept_copy` of `RID` with the approved CLI."""
        kwargs: dict[str, Any] = {} if hook is None else {"_hook": hook}
        return accept_copy(
            self.layout,
            RID,
            install=self.world.cfg(),
            platform=PLATFORM,
            limits=limits,
            **kwargs,
        )

    def copy_observed(
        self, phase: str, hook: Callable[[str, str], None] | None = None
    ) -> tuple[str | None, StepFailed | None]:
        """`copy` observed over the staging tree: (digest, refusal)."""
        with self.observing([self.layout.staging_tree(RID)], phase) as obs:
            try:
                digest, refusal = self.copy(hook=hook), None
            except StepFailed as exc:
                digest, refusal = None, exc
        require_clean(obs)
        return digest, refusal

    def bootstrap_init(self) -> int:
        return self.world.bootstrap(self.world.init_argv())


@pytest.fixture()
def cw(
    root_world: RootWorld, controls: Controls, request: pytest.FixtureRequest
) -> CopyWorld:
    """The copy world over `root_world` after the positive `controls`."""
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)
    return CopyWorld(root_world, label, controls.account.uid)


def _q(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _never_admin(
    layout: Layout,
    release_id: str,
    *,
    expected_admin_digest: str,
    expected_python: str,
) -> bool:
    return False


def _watch_at_chown(obs: Observation) -> Callable[[str, Path], None]:
    def hook(kind: str, path: Path) -> None:
        if kind == "chown":
            obs.watch(path)

    return hook


def require_clean(
    obs: Observation, *, own: Mapping[Path, frozenset[str]] | None = None
) -> None:
    """`observed`, and no access of root to a source except `own`'s: the
    root parent a protocol holds, with the ops it performs on it."""
    if obs.status != "observed":
        pytest.fail(f"наблюдатель: unobserved — {obs.reason}")
    allowed = dict(own or {})
    for parent in allowed:
        assert os.lstat(parent).st_uid == 0, parent  # root's own, not df-build's
    bad = [
        a
        for a in obs.violations
        if not (a.path in {str(p) for p in allowed} and a.op in allowed[Path(a.path)])
    ]
    assert bad == [], bad


# ---------- facts after a copy ----------


def builder_pids(uid: int) -> list[int] | None:
    """Live (non-zombie) processes of `df-build`; `None` if `ps` failed."""
    return plat._user_pids(uid)  # noqa: SLF001 - the production listing


def assert_no_builder_processes(uid: int) -> None:
    assert builder_pids(uid) == [], "процессы df-build остались"


def assert_lock_free(layout: Layout) -> None:
    """The writer lock can be taken now (non-blocking `flock`)."""
    with journal.writer_lock(layout):
        pass


def assert_root_700(path: Path) -> None:
    st = os.lstat(path)
    assert stat.S_ISDIR(st.st_mode) and st.st_uid == 0, path
    assert stat.S_IMODE(st.st_mode) == 0o700, oct(st.st_mode)


def assert_destination_closed(cw: CopyWorld) -> None:
    """`df-build` cannot create in, nor open for writing anything of,
    `releases/<id>`: `EACCES`."""
    release = cw.layout.release_dir(RID)
    targets = [release / "df-build-probe"]
    targets += [p for p in _files(release)[:3] if not p.is_symlink()]
    for target in targets:
        done = cw.as_build(f": >> {_q(target)}", check=False)
        assert done.launched and done.returncode != 0, (target, done)
        assert b"Permission denied" in done.stderr, (target, done.stderr)
    assert not os.path.lexists(release / "df-build-probe")


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, _dirs, files in os.walk(root):
        found += [Path(dirpath, f) for f in files]
    return found


def assert_within(release: Path, limits: CopyLimits) -> None:
    """An accepted copy keeps the limits it was received under."""
    objects = sum(len(d) + len(f) for _p, d, f in os.walk(release))
    size = sum(os.lstat(p).st_size for p in _files(release) if not p.is_symlink())
    assert objects <= limits.max_objects and size <= limits.max_bytes


@dataclass(frozen=True)
class Sentinel:
    """A root-only file (`0600` in a `0755` directory of root)."""

    path: Path
    content: bytes


def make_sentinel(layout: Layout) -> Sentinel:
    directory = layout.root / "secret"
    directory.mkdir(mode=0o755)
    path = directory / "sentinel"
    content = f"root-only-sentinel-{uuid.uuid4().hex}".encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    return Sentinel(path, content)


def assert_no_sentinel(release: Path, sentinel: Sentinel) -> None:
    """No regular file of the copy holds the root-only content."""
    if not release.exists():
        return
    for path in _files(release):
        if path.is_symlink() or not path.is_file():
            continue
        assert sentinel.content not in path.read_bytes(), path


def wait_beat(beat: Path, count: int = 2) -> None:
    """Until the writer's heartbeat file grew `count` times (`lstat` only)."""
    deadline = time.monotonic() + BEAT_TIMEOUT
    last, seen = -1, 0
    while seen < count:
        if time.monotonic() > deadline:
            infra(f"писатель df-build не пишет: {beat}")
        try:
            size = os.lstat(beat).st_size
        except FileNotFoundError:
            size = -1
        if size > last >= 0:
            seen += 1
        last = max(last, size)
        time.sleep(0.05)


@dataclass
class OnceHook:
    """The `_hook` of `accept_copy`: `act` at the first `point`."""

    point: str
    act: Callable[[], None]
    fired: list[str] = field(default_factory=list)

    def __call__(self, point: str, rel: str) -> None:
        if point == self.point and not self.fired:
            self.fired.append(rel)
            self.act()


# ---------- real tar ----------


def test_real_tar_from_build_user_accepted(cw: CopyWorld) -> None:
    """tar of `df-build` through `spawn_as` into `receive_tree`: the root
    record, long and non-ASCII names, a hard and a symbolic link; the
    copy's digest equals the source's (taken before the observation, R94)."""
    tree = cw.sealed(NAMES_TREE)
    expected = release_digest(tree)
    digest, refusal = cw.copy_observed("copy")
    assert refusal is None, refusal
    release = cw.layout.release_dir(RID)
    assert digest == expected == release_digest(release)
    assert_root_700(release)
    assert os.readlink(release / "sym") == "d/f"
    assert (release / "hard").read_bytes() == b"hello\n"
    assert os.lstat(release / "hard").st_ino != os.lstat(release / "d/f").st_ino
    assert (release / "d" / "été.txt").read_bytes() == b"utf8"
    assert (release / DEEP / "f").read_bytes() == b"deep"
    assert_no_builder_processes(cw.uid)


# ---------- live writer during the copy ----------

#: One-shot races: `df-build` changes the sealed tree at the first member.
ONE_SHOT: Mapping[str, str] = {
    "dir-to-link": "rm -rf {t}/d && ln -s {secret_dir} {t}/d",
    "link-retarget": "rm -f {t}/link && ln -s {secret} {t}/link",
    "file-to-fifo": "rm -f {t}/d/f && mkfifo {t}/d/f",
    "hardlink-root-only-file": "ln {secret} {t}/d/hard",
}
#: Continuous writers: started before the copy, print first, heartbeat.
WRITERS: Mapping[str, str] = {
    "saved-fd-writer": (
        "exec 3>> {r}/pad-grow; echo go; while :; do"
        " /usr/bin/head -c 65536 /dev/zero >&3; printf . >> {beat};"
        " /bin/sleep 0.01; done"
    ),
    "growing-file": (
        "echo go; while :; do /usr/bin/head -c 65536 /dev/zero >> {t}/pad-grow;"
        " printf . >> {beat}; /bin/sleep 0.01; done"
    ),
    "growing-tree": (
        "cd {t}/grow && echo go; i=0; while :; do : > f$i; i=$((i+1));"
        " printf . >> {beat}; /bin/sleep 0.002; done"
    ),
}
RACES = [*ONE_SHOT, *WRITERS]


@pytest.mark.parametrize("race", RACES)
def test_live_writer_during_copy(
    cw: CopyWorld, race: str, record_property: Callable[[str, object], None]
) -> None:
    """A real `df-build` process changes the tree during the copy (synced
    through `_hook`): `TreeError` (`StepFailed("copy")`) or a copy that is an
    ordinary tree within the limits; the root-only sentinel is not in it
    (tar as `df-build` cannot read it); every phase — the build with the
    classification, the copy, the cleanup — observed with no root access to
    a source; the producer and the writer ended; `df-build` cannot open the
    destination for writing (`EACCES`)."""
    lay = cw.layout
    secret = make_sentinel(lay)
    beat = lay.build_home / f"beat-{race}"
    fill = {
        "t": _q(lay.staging_tree(RID)),
        "r": _q(lay.release_dir(RID)),
        "secret": _q(secret.path),
        "secret_dir": _q(secret.path.parent),
        "beat": _q(beat),
    }
    writer: plat.SpawnedProducer | None = None
    cw.built(LIVE_TREE)
    if race == "saved-fd-writer":  # the descriptor is taken before `seal`
        writer = cw.spawn_build(WRITERS[race].format(**fill))
    seal(lay, RID)
    if race in WRITERS and writer is None:
        writer = cw.spawn_build(WRITERS[race].format(**fill))
    acts: list[plat.RunResult] = []

    def act() -> None:
        if race in WRITERS:
            wait_beat(beat)
            return
        acts.append(cw.as_build(ONE_SHOT[race].format(**fill), check=False))

    hook = OnceHook("member", act)
    try:
        digest, refusal = cw.copy_observed("copy", hook)
    finally:
        if writer is not None:
            writer.end()
    assert hook.fired, "гонка не состоялась: ни одного члена потока"
    for done in acts:
        record_property("race_act", f"rc={done.returncode} {done.stderr[-300:]!r}")
        assert done.launched, done
        if race == "hardlink-root-only-file" and LINUX:
            # protected_hardlinks: a link to root's 0600 file is refused.
            assert done.returncode != 0, done
        else:
            assert done.returncode == 0, done
    record_property("outcome", "accepted" if refusal is None else str(refusal))
    release = lay.release_dir(RID)
    assert_no_builder_processes(cw.uid)
    if refusal is None:
        assert digest == release_digest(release)
        assert_within(release, LIMITS)
    else:
        assert refusal.step == "copy", refusal
    assert_root_700(release)
    assert_no_sentinel(release, secret)
    assert_destination_closed(cw)
    box = lay.staging_dir(RID)
    with cw.observing([box], "cleanup") as obs:
        outcome = plat.cleanup_tree(lay, box, "tree", quarantine_label=RID)
    require_clean(obs, own={box: OPEN_OPS})
    assert outcome == "removed"
    assert not os.path.lexists(lay.staging_tree(RID))


# ---------- tar failure ----------


def test_tar_failure_as_root(
    cw: CopyWorld, record_property: Callable[[str, object], None]
) -> None:
    """R33.F35 as root: a file `df-build` cannot read stops its tar —
    `StepFailed("copy")` carrying tar's stderr (bsdtar cuts the stream,
    GNU tar exits 2; both recorded, R81); the partial copy stays root `0700`
    for the route, the producer is ended."""
    cw.sealed(
        "mkdir -p src && printf a > src/a && printf s > src/secret"
        " && chmod 000 src/secret"
    )
    with pytest.raises(StepFailed) as exc:
        cw.copy()
    detail = str(exc.value.detail)
    _, _, stderr = detail.partition("; stderr: ")
    record_property("tar_refusal", detail[-2000:])
    record_property("tar_stderr", stderr[-2000:])
    assert exc.value.step == "copy"
    assert "tar" in stderr, detail
    if LINUX:
        assert "producer exited with code 2" in detail, detail
    assert_root_700(cw.layout.release_dir(RID))
    assert_no_builder_processes(cw.uid)


# ---------- cleanup of 000 trees ----------


def test_cleanup_of_000_tree(cw: CopyWorld) -> None:
    """S1-R4-N4: `df-build` makes a non-empty tree, sets `000` on its root
    and nested directories and stops. The first cleanup in `releases/`,
    the quarantine retry and the cleanup in `build_home` each remove such a
    tree; every one observed with no root access to the tree (GNU find on
    Ubuntu, BSD find on macOS)."""
    lay = cw.layout
    cw.built(ZERO_TREE)
    with cw.observing([lay.releases], "releases") as obs:
        outcome = plat.cleanup_tree(lay, lay.releases, RID, quarantine_label=RID)
    require_clean(obs, own={lay.releases: OPEN_OPS})
    assert outcome == "removed"
    assert not os.path.lexists(lay.release_dir(RID))

    cw.built(ZERO_TREE, "q-1")
    os.rename(lay.release_dir("q-1"), lay.quarantine / "q-1.1")  # setup only
    with cw.observing([lay.quarantine], "quarantine") as obs:
        left = plat.retry_quarantine(lay)
    require_clean(obs, own={lay.quarantine: LIST_OPS})
    assert left == []
    assert os.listdir(lay.quarantine) == []

    cw.as_build(f"mkdir {_q(lay.build_home / 't000')} && cd t000 && {ZERO_TREE}")
    with cw.observing([lay.build_home], "build-home") as obs:
        removed = plat.cleanup_build_home(lay, "t000")
    require_clean(obs)
    assert removed == "removed"
    assert not os.path.lexists(lay.build_home / "t000")


# ---------- growing tree and quarantine ----------


def quarantined_growing_tree(cw: CopyWorld) -> tuple[str, float]:
    """A sealed tree a `df-build` writer keeps filling, cleaned with
    `budget=BUDGET` under observation: (outcome, seconds)."""
    lay = cw.layout
    beat = lay.build_home / "beat-cleanup"
    tree = cw.sealed(LIVE_TREE)
    writer = cw.spawn_build(WRITERS["growing-tree"].format(t=_q(tree), beat=_q(beat)))
    try:
        wait_beat(beat)
        box = lay.staging_dir(RID)
        with cw.observing([box], "cleanup") as obs:
            start = time.monotonic()
            outcome = plat.cleanup_tree(
                lay, box, "tree", quarantine_label=RID, budget=BUDGET
            )
            elapsed = time.monotonic() - start
    finally:
        writer.end()
    require_clean(obs, own={box: OPEN_OPS})
    return outcome, elapsed


def test_cleanup_of_growing_tree_quarantined_within_budget(
    cw: CopyWorld, record_property: Callable[[str, object], None]
) -> None:
    """A `df-build` writer creates files while the cleaner deletes: within
    budget+ε the rest is in the quarantine and the install goes on (the leaf
    returns `quarantined`, no refusal); the writer is ended afterwards."""
    outcome, elapsed = quarantined_growing_tree(cw)
    record_property("cleanup_seconds", f"{elapsed:.2f}")
    assert outcome == "quarantined"
    assert elapsed <= BUDGET + EPSILON, elapsed
    lay = cw.layout
    assert not os.path.lexists(lay.staging_tree(RID))
    assert os.path.lexists(lay.quarantine / f"{RID}.1")
    assert_no_builder_processes(cw.uid)


def test_quarantine_removed_by_next_install(cw: CopyWorld) -> None:
    """The writer stopped, the next install (`bootstrap init`, its
    `retry_quarantine`) removes the rest and commits; no `quarantine` warn."""
    outcome, _ = quarantined_growing_tree(cw)
    assert outcome == "quarantined"
    lay = cw.layout
    assert os.listdir(lay.quarantine) == [f"{RID}.1"]
    assert cw.bootstrap_init() == cli.EXIT_OK
    assert os.listdir(lay.quarantine) == []
    rows = read_journal(lay.journal)
    assert [r for r in rows if r["step"] == "quarantine"] == []
    assert_committed(lay)


# ---------- build user after seal ----------


def test_build_user_after_seal_cannot_touch_destination(
    cw: CopyWorld, record_property: Callable[[str, object], None]
) -> None:
    """A `df-build` process started after `seal` reads and changes the
    staging tree (by design) and keeps trying to create in and append to
    `releases/<id>` during the copy: every try is refused; the copy is an
    ordinary tree or a refusal."""
    lay = cw.layout
    tree = cw.sealed(LIVE_TREE)
    release = lay.release_dir(RID)
    log = lay.build_home / "after-seal.log"
    beat = lay.build_home / "beat-after-seal"
    script = (
        f"echo go; while :; do cat {_q(tree)}/d/f > /dev/null;"
        f" printf x >> {_q(tree)}/pad-grow;"
        f" if ( : > {_q(release)}/probe ) 2>/dev/null; then echo OPENED >> {_q(log)};"
        f" else echo denied >> {_q(log)}; fi;"
        f" if ( : >> {_q(release)}/d/f ) 2>/dev/null; then echo OPENED >> {_q(log)};"
        f" else echo denied >> {_q(log)}; fi;"
        f" printf . >> {_q(beat)}; /bin/sleep 0.01; done"
    )
    writer = cw.spawn_build(script)
    try:
        hook = OnceHook("member", lambda: wait_beat(beat))
        digest, refusal = cw.copy_observed("copy", hook)
    finally:
        writer.end()
    record_property("outcome", "accepted" if refusal is None else str(refusal))
    tries = log.read_text().split()
    assert tries and set(tries) == {"denied"}, tries[:10]
    if refusal is None:
        assert digest == release_digest(release)
    assert not os.path.lexists(release / "probe")
    assert_root_700(release)
    assert_destination_closed(cw)


# ---------- crash inside accept ----------


@pytest.mark.parametrize("point", ["member", "mid-copy"])
def test_crash_inside_accept_real_fs(cw: CopyWorld, point: str) -> None:
    """A kill inside `accept_copy` at `point`: the partial copy is root
    `0700`, no `df-build` process is left; the route `copy-partial`'s leaves
    (`remove_own_tree`, `accept_copy`) give the source's digest."""
    tree = cw.sealed(LIVE_TREE)
    expected = release_digest(tree)

    def kill() -> None:
        raise Killed(point)

    with pytest.raises(Killed):
        cw.copy(hook=OnceHook(point, kill))
    lay = cw.layout
    assert_root_700(lay.release_dir(RID))
    assert_no_builder_processes(cw.uid)
    remove_own_tree(lay.releases, RID)
    assert cw.copy() == expected


# ---------- producers through the command ----------


def _substitute_producer(
    mp: pytest.MonkeyPatch,
    script: str,
    after: Callable[[plat.SpawnedProducer], None] | None = None,
    argv_edit: Callable[[list[str]], list[str]] | None = None,
) -> None:
    """`spawn_as` of the approved tar runs `/bin/sh -c script sh <argv>`
    as `df-build` through the real chain; `after` sees the producer."""
    real = plat.spawn_as

    def spawn_as(user: str, argv: Sequence[str], **kw: Any) -> plat.SpawnedProducer:
        tar_argv = list(argv)
        if argv_edit is not None:
            tar_argv = argv_edit(tar_argv)
        producer = real(user, ["/bin/sh", "-c", script, "sh", *tar_argv], **kw)
        if after is not None:
            after(producer)
        return producer

    mp.setattr(plat, "spawn_as", spawn_as)


def _release_ids(layout: Layout) -> list[str]:
    return sorted(os.listdir(layout.releases))


def _refused_then_resumed(cw: CopyWorld, script: str, reason: str) -> None:
    """`init` with the producer substituted is refused at `copy` for
    `reason`; the partial copy is root `0700`, no `df-build` process, the
    writer lock free; `init` again resumes through `copy-partial` and
    commits."""
    lay = cw.layout
    with pytest.MonkeyPatch.context() as mp:
        _substitute_producer(mp, script)
        mp.setattr(
            install, "CopyLimits", functools.partial(CopyLimits, max_seconds=DEADLINE)
        )
        assert cw.bootstrap_init() == cli.EXIT_REFUSED
    rows = read_journal(lay.journal)
    failed = [r for r in rows if r["event"] == "failed"]
    assert failed and failed[-1]["step"] == "copy", failed[-1:]
    assert reason in str(failed[-1]["detail"]), failed[-1]
    (rid,) = _release_ids(lay)
    assert_root_700(lay.release_dir(rid))
    assert_no_builder_processes(cw.uid)
    assert_lock_free(lay)
    assert cw.bootstrap_init() == cli.EXIT_OK
    rows = read_journal(lay.journal)
    assert ("copy-partial", "done") in {(r["step"], r["event"]) for r in rows}
    assert_committed(lay)


def test_forked_producer_reaped_via_spawn_as(cw: CopyWorld) -> None:
    """S1-R4-N3, S1-R10-N3: the approved tar under a wrapper that starts the
    producer in its own session and exits without waiting; the stream never
    ends, so `receive_tree` refuses at its deadline, and afterwards no
    process of `df-build` is left (the session escaped the group kill, not
    the reap by user), the partial copy is root `0700`, the lock free; the
    next install removes it through `copy-partial`."""
    _refused_then_resumed(cw, FORKED, "deadline")


def test_silent_producer_released(cw: CopyWorld) -> None:
    """The producer writes 1 KiB of the stream and sleeps: refused within
    the deadline, the `df-build` process killed, the partial copy root
    `0700` until the next run, the writer lock free after the command;
    the retry with the real tar removes it (`copy-partial`) and commits."""
    _refused_then_resumed(cw, SILENT, "deadline")


# ---------- GNU padding ----------


@pytest.mark.skipif(not LINUX, reason="только Linux (GNU tar, F_SETPIPE_SZ)")
def test_gnu_padding_with_small_pipe(
    cw: CopyWorld, record_property: Callable[[str, object], None]
) -> None:
    """Ubuntu: the producer pipe shrunk to 4096 bytes, GNU tar `-b 64`
    (32 KiB records): the stream is accepted, not refused by the deadline;
    the record padding counts toward `max_stream_bytes` (one byte less is
    refused)."""
    lay = cw.layout
    tree = cw.sealed(LIVE_TREE)
    expected = release_digest(tree)
    flags = " ".join(plat.TAR_FLAGS[PLATFORM])
    size_run = cw.as_build(
        f"/usr/bin/tar {flags} -b 64 -cf - -C {_q(tree)} . | /usr/bin/wc -c"
    )
    size = int(size_run.stdout.decode().strip())
    record_property("stream_bytes", size)
    assert size % (64 * 512) == 0, size
    pipe_sizes: list[int] = []

    def shrink(producer: plat.SpawnedProducer) -> None:
        pipe_sizes.append(fcntl.fcntl(producer.fd, F_SETPIPE_SZ, 4096))

    def blocked(argv: list[str]) -> list[str]:
        at = argv.index("-cf")
        return [*argv[:at], "-b", "64", *argv[at:]]

    limits = CopyLimits(max_stream_bytes=size, max_seconds=60.0)
    with pytest.MonkeyPatch.context() as mp:
        _substitute_producer(mp, DELAYED, shrink, blocked)
        start = time.monotonic()
        assert cw.copy(limits=limits) == expected
        record_property("copy_seconds", f"{time.monotonic() - start:.2f}")
        remove_own_tree(lay.releases, RID)
        with pytest.raises(StepFailed) as exc:
            cw.copy(limits=CopyLimits(max_stream_bytes=size - 1, max_seconds=60.0))
    assert pipe_sizes == [4096, 4096], pipe_sizes
    assert "max_stream_bytes" in str(exc.value.detail), exc.value.detail
    assert_no_builder_processes(cw.uid)
