"""Tests for the Task 7 protocol leaves (part 7-D2) and every primitive of
`PROTOCOLS` against its rows.

The leaves are real; only the lower operations of `fake_layout` are replaced
(launch chain, `fchown`, `pkill`/`ps`, fsutil recorders). The expected events
of a primitive come from the data (`leaf_variants`) through the same
concretisation as the routes test (`expected_events`).
"""

from __future__ import annotations

import dataclasses
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest import mock

import pytest

from df import admin, fsutil, leaves, receive, release, routes
from df import install_table as table
from df import platform as plat
from df.accept import Facts, classify, load_mark, read_facts
from df.config import Account, Pending, config_digest, dump_accounts, dump_pending
from df.digest import tree_digest
from df.journal import StepFailed
from df.receive import CopyLimits
from tests.df.conftest import BUILD_UID, FakeLayout, fd_path
from tests.df.route_world import (
    BOOT,
    ID,
    INSTALL,
    PY,
    StandIns,
    devtools_ref,
    expected_events,
    fake_uv,
    make_calls,
    normalize,
    obj,
    release_world,
    setup_layout,
    write_accounts,
    write_state_mark,
)

PLATFORM = plat.detect_platform()
RELEASE = "releases/<id>"


class Crash(Exception):
    """A simulated crash at a fault point."""


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    layout = setup_layout(monkeypatch, fake_layout)
    write_accounts(layout)
    return layout


# ---------- preparation ----------


def staged_tree(fl: FakeLayout) -> Path:
    """`staging/<id>/` (root `0711`) holding a builder tree with content."""
    lay = fl.layout
    lay.staging_dir(ID).mkdir(mode=0o711)
    tree = fl.builder_dir(lay.staging_tree(ID))
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "app.py").write_text("print(1)\n")
    (tree / "bin").mkdir()
    (tree / "bin" / "run").write_text("#!/bin/sh\n")
    (tree / "bin" / "run").chmod(0o755)
    (tree / "link").symlink_to("src/pkg/app.py")
    return tree


def root_release(fl: FakeLayout) -> Path:
    """A root copy at `releases/<id>`, root `0700` (as `accept_copy` leaves it)."""
    tree = fl.layout.release_dir(ID)
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "a.py").write_text("x\n")
    tree.chmod(0o700)
    return tree


def write_pending(fl: FakeLayout) -> None:
    pending = Pending("run-1", ID, 'schema = "df/install/v1"\n', DIGEST, None)
    fl.layout.pending.write_text(dump_pending(pending))


DIGEST = config_digest(INSTALL)


def facts(fl: FakeLayout) -> Facts:
    """The facts `drive` would read (no launcher, admin never ready)."""

    def admin_ready(*_a: object, **_kw: object) -> bool:
        return False

    return read_facts(
        fl.layout,
        ID,
        install_digest=DIGEST,
        build_uid=fl.build_uid,
        launcher_ready=lambda: False,
        admin_ready=admin_ready,
    )


def copy(fl: FakeLayout, **kw: object) -> str:
    return leaves.accept_copy(
        fl.layout,
        ID,
        install=INSTALL,
        platform=PLATFORM,
        **kw,  # type: ignore[arg-type]
    )


# ---------- accept_copy ----------


def test_accept_copy_events_exact(fl: FakeLayout) -> None:
    """S1-R10-N1: real accept_copy -> receive_tree -> SpawnedProducer.end:
    events == leaf_effects("accept_copy"), `sync_dir(releases)` exactly once;
    the copy equals the builder's tree and the digest is returned."""
    lay = fl.layout
    tree = staged_tree(fl)
    fl.clear()
    digest = copy(fl)
    assert normalize(fl.events) == expected_events(lay, "accept_copy", None, RELEASE)
    assert fl.events.count(("sync_dir", lay.releases)) == 1
    release = lay.release_dir(ID)
    assert digest == tree_digest(release) == tree_digest(tree)
    assert stat.S_IMODE(os.stat(release).st_mode) == 0o700
    assert fl.owner_of(os.stat(release)) == os.getuid()  # root's copy


def test_accept_copy_refused_stream_keeps_partial(fl: FakeLayout) -> None:
    """A refused stream: created, process(df-build), then `StepFailed("copy")`;
    the partial copy stays for the route, no sync of the parent."""
    lay = fl.layout
    staged_tree(fl)
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        copy(fl, limits=CopyLimits(max_objects=1))
    assert exc.value.step == "copy" and "; stderr:" in exc.value.detail
    assert normalize(fl.events) == [
        ("created", lay.release_dir(ID)),
        ("process", "df-build"),
    ]
    assert lay.release_dir(ID).is_dir()


@pytest.mark.skipif(os.geteuid() == 0, reason="F35: root reads 000 files (Task 13)")
def test_tar_failure_refuses_and_keeps_partial_for_route(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """tar fails on an unreadable file -> `StepFailed("copy")` with tar's
    stderr; the partial copy root `0700` stays and the producer is ended
    (S1-R10-N3). Retry: the facts are `partial-copy`, whose route
    `copy-partial` removes the rest and goes on to `copied`."""
    lay = fl.layout
    tree = staged_tree(fl)
    secret = tree / "src" / "secret"
    secret.write_text("s\n")
    secret.chmod(0)
    write_pending(fl)
    write_state_mark(fl, "built")
    fl.clear()
    try:
        with pytest.raises(StepFailed) as exc:
            copy(fl)
    finally:
        secret.chmod(0o644)
    assert exc.value.step == "copy"
    # bsdtar stops the stream at the file ("tar: ..."), GNU tar called by
    # full path exits non-zero ("/usr/bin/tar: ..."): either way the refusal
    # carries tar's stderr.
    _, _, stderr = exc.value.detail.partition("; stderr: ")
    assert "tar" in stderr
    release = lay.release_dir(ID)
    assert stat.S_IMODE(os.stat(release).st_mode) == 0o700
    assert ("process", "df-build") in fl.events
    assert ("sync_dir", lay.releases) not in fl.events
    phase = classify(facts(fl))
    assert phase == "partial-copy"
    assert table.row_of(phase).action == "copy-partial"
    routes.run_route(
        lay,
        ID,
        "copy-partial",
        calls=StandIns(fl).calls(),
        build_uid=BUILD_UID,
    )
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None and mark.stage == "copied"
    assert mark.tree_digest == tree_digest(release) == tree_digest(tree)
    assert classify(facts(fl)) == "copied"


def test_copy_refusal_reports_the_stderr_tail(fl: FakeLayout) -> None:
    """F21: `TreeError` -> `StepFailed("copy", "<exc>; stderr: <last 4096>")`.

    Real `spawn_as` and `receive_tree` under `fake_layout`; the approved
    `tar` is a local script: 5003 bytes of stderr, then a block that is no
    tar header."""
    lay = fl.layout
    staged_tree(fl)
    tar = lay.root.parent / "bad-tar"
    tar.write_text(
        "#!/bin/sh\n"
        "/usr/bin/head -c 5000 /dev/zero | /usr/bin/tr '\\0' x >&2\n"
        "printf END >&2\n"
        "/usr/bin/head -c 512 /dev/zero | /usr/bin/tr '\\0' y\n"
    )
    tar.chmod(0o755)
    install = dataclasses.replace(
        INSTALL, approved_cli={**INSTALL.approved_cli, "tar": tar}
    )
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        leaves.accept_copy(lay, ID, install=install, platform=PLATFORM)
    assert exc.value.step == "copy"
    head, tail = exc.value.detail.split("; stderr: ")
    assert head
    assert tail == ("x" * 5000 + "END")[-leaves.STDERR_REPORT_BYTES :]
    assert len(tail) == 4096
    assert normalize(fl.events) == [
        ("created", lay.release_dir(ID)),
        ("process", "df-build"),
    ]
    assert lay.release_dir(ID).is_dir()


def test_copied_digest_and_its_check_are_one_call(fl: FakeLayout) -> None:
    """M6: the `copied` mark and the route's check exclude the same names —
    a later `release.json` does not break the check; anything else does."""
    staged_tree(fl)
    write_state_mark(fl, "built")
    calls = make_calls(fl)
    calls["accept_copy"](RELEASE)
    calls["write_mark"]("accept/<id>.toml: copied")
    release = fl.layout.release_dir(ID)
    (release / "release.json").write_text("{}\n")
    calls["tree_digest"]("releases/<id> = отметке")
    (release / "extra").write_text("x\n")
    with pytest.raises(StepFailed):
        calls["tree_digest"]("releases/<id> = отметке")


def test_call_sites_match_signatures(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """spawn_as, receive_tree and cleanup_tree replaced by `create_autospec`
    of the real functions: a wrong call fails; argv[0] is the approved tar,
    env and cwd are set, the limits reach both."""
    lay = fl.layout
    spawn = mock.create_autospec(plat.spawn_as)
    take = mock.create_autospec(receive.receive_tree)
    clean = mock.create_autospec(plat.cleanup_tree, return_value="removed")
    monkeypatch.setattr(plat, "spawn_as", spawn)
    monkeypatch.setattr(receive, "receive_tree", take)
    monkeypatch.setattr(plat, "cleanup_tree", clean)
    root_release(fl)
    limits = CopyLimits(reap_seconds=3.5)
    digest = copy(fl, limits=limits)
    assert digest == tree_digest(lay.release_dir(ID))
    (user, argv), kw = spawn.call_args
    assert user == "df-build"
    assert argv == [
        str(INSTALL.approved_cli["tar"]),
        *plat.TAR_FLAGS[PLATFORM],
        "-cf",
        "-",
        "-C",
        str(lay.staging_tree(ID)),
        ".",
    ]
    assert kw == {
        "env": {"PATH": "/usr/bin:/bin", "LANG": "C"},
        "cwd": lay.build_home,
        "network": False,
        "reap_seconds": 3.5,
    }
    args, kw = take.call_args
    assert args == (spawn.return_value, lay.releases, ID)
    assert kw["limits"] is limits
    make_calls(fl)["cleanup_tree"]("staging/<id>/tree")
    clean.assert_called_once_with(lay, lay.staging_dir(ID), "tree", quarantine_label=ID)


# ---------- prepare_dir ----------


@pytest.mark.parametrize("stop", ["created", "chown"])
def test_prepare_dir_transfers_by_fd(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, stop: str
) -> None:
    """S1-R8-N2: open before fchown; fchown, fsync and close by descriptor;
    after fchown no open of `releases/<id>`. A crash at `stop`, then the
    facts: root's empty dir is `unknown` (cleanup removes it by
    `remove_own_tree`); the transferred one is `prepared`."""
    lay = fl.layout
    release = lay.release_dir(ID)
    write_pending(fl)
    calls: list[tuple[str, object]] = []
    fake_open, fake_fchown, fake_fault = os.open, os.fchown, fsutil.fault_point
    fake_sync = fsutil.sync_dir

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        dir_fd = kw.get("dir_fd")
        base = fd_path(dir_fd) if isinstance(dir_fd, int) else Path()
        calls.append(("open", base / str(path)))
        return fake_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    def fchown(fd: int, uid: int, gid: int) -> None:
        calls.append(("fchown", fd))
        fake_fchown(fd, uid, gid)

    def sync_dir(fd_or_path: int | Path) -> fsutil.SyncNote:
        calls.append(("sync", fd_or_path))
        return fake_sync(fd_or_path)

    def fault(kind: fsutil.FaultKind, path: Path) -> None:
        fake_fault(kind, path)
        if kind == stop:
            raise Crash(kind)

    with monkeypatch.context() as m, pytest.raises(Crash):
        m.setattr(os, "open", open_)
        m.setattr(os, "fchown", fchown)
        m.setattr(fsutil, "sync_dir", sync_dir)
        m.setattr(fsutil, "fault_point", fault)
        leaves.prepare_dir(lay, ID)
    opens = [i for i, c in enumerate(calls) if c == ("open", release)]
    assert len(opens) == 1
    chowns = [i for i, c in enumerate(calls) if c[0] == "fchown"]
    if stop == "chown":
        assert chowns and opens[0] < chowns[0]
        assert all(c != ("open", release) for c in calls[chowns[0] :])
    else:
        assert chowns == []
    assert not any(isinstance(c[1], Path) and c[0] == "sync" for c in calls)
    assert classify(facts(fl)) == ("unknown" if stop == "created" else "prepared")
    if stop == "created":
        assert fl.owner_of(os.stat(release)) == os.getuid()
        fl.clear()
        routes.run_route(
            lay, ID, "cleanup", calls=StandIns(fl).calls(), build_uid=BUILD_UID
        )
        assert not os.path.lexists(release)
        assert ("call", "remove_own_tree", RELEASE) in fl.events


def test_prepare_dir_full_run_never_reopens(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """The whole protocol: one open of the path, before `fchown`; the two
    syncs are by descriptor; the directory ends `0700`, owner `df-build`."""
    lay = fl.layout
    release = lay.release_dir(ID)
    seen: list[tuple[str, object]] = []
    fake_open, fake_fchown = os.open, os.fchown

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        dir_fd = kw.get("dir_fd")
        base = fd_path(dir_fd) if isinstance(dir_fd, int) else Path()
        seen.append(("open", base / str(path)))
        return fake_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    def fchown(fd: int, uid: int, gid: int) -> None:
        seen.append(("fchown", (uid, gid)))
        fake_fchown(fd, uid, gid)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "fchown", fchown)
    fl.clear()
    leaves.prepare_dir(lay, ID)
    monkeypatch.setattr(os, "open", fake_open)
    assert normalize(fl.events) == expected_events(lay, "prepare_dir", None, RELEASE)
    at = seen.index(("fchown", (BUILD_UID, BUILD_UID)))
    assert seen.index(("open", release)) < at
    assert ("open", release) not in seen[at:]
    assert stat.S_IMODE(os.stat(release).st_mode) == 0o700
    assert fl.owner_of(os.stat(release)) == BUILD_UID


def test_prepare_dir_takes_ids_from_accounts(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """Concern 5: uid and gid of the `accounts.toml` record, not `pwd`."""
    account = Account(plat.BUILD_USER, 4242, 4343, "ready", "2026-10-22")
    fl.layout.accounts.write_text(dump_accounts({plat.BUILD_USER: account}))
    got: list[tuple[int, int]] = []
    fake = os.fchown
    monkeypatch.setattr(
        os, "fchown", lambda fd, uid, gid: (got.append((uid, gid)), fake(fd, uid, gid))
    )
    leaves.prepare_dir(fl.layout, ID)
    assert got == [(4242, 4343)]


@pytest.mark.parametrize("record", ["absent", "creating", "broken"])
def test_prepare_dir_refuses_without_a_ready_record(
    fl: FakeLayout, record: str
) -> None:
    path = fl.layout.accounts
    if record == "absent":
        path.unlink()
    elif record == "creating":
        account = Account(plat.BUILD_USER, 4242, 4242, "creating", "2026-10-22")
        path.write_text(dump_accounts({plat.BUILD_USER: account}))
    else:
        path.write_text("not toml [")
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        leaves.prepare_dir(fl.layout, ID)
    assert exc.value.step == "build-dir"
    assert fl.events == []
    assert not os.path.lexists(fl.layout.release_dir(ID))


def test_prepare_dir_refuses_an_existing_name(fl: FakeLayout) -> None:
    fl.layout.release_dir(ID).mkdir()
    with pytest.raises(StepFailed) as exc:
        leaves.prepare_dir(fl.layout, ID)
    assert exc.value.step == "build-dir"


# ---------- seal ----------


def builder_release(fl: FakeLayout) -> Path:
    tree = fl.builder_dir(fl.layout.release_dir(ID))
    (tree / "f").write_text("x\n")
    return tree


def test_seal_syncs_three_dirs(fl: FakeLayout) -> None:
    """`releases/`, `staging/` and `staging/<id>/`, in that order; the tree
    moved whole, the container root `0711`."""
    lay = fl.layout
    builder_release(fl)
    fl.clear()
    leaves.seal(lay, ID)
    syncs = [e[1] for e in fl.events if e[0] == "sync_dir"]
    assert syncs == [lay.releases, lay.staging, lay.staging_dir(ID)]
    assert not os.path.lexists(lay.release_dir(ID))
    assert (lay.staging_tree(ID) / "f").read_text() == "x\n"
    assert fl.owner_of(os.stat(lay.staging_tree(ID))) == BUILD_UID
    assert stat.S_IMODE(os.stat(lay.staging_dir(ID)).st_mode) == 0o711


@pytest.mark.parametrize("variant", ["new", "reuse"])
def test_seal_variant_events(fl: FakeLayout, variant: str) -> None:
    """S1-R12-N1: reuse — an empty `staging/<id>` (a crash right after the
    `mkdir`): events == leaf_variants("seal")[variant], no `created`."""
    lay = fl.layout
    builder_release(fl)
    if variant == "reuse":
        lay.staging_dir(ID).mkdir(mode=0o711)
    fl.clear()
    leaves.seal(lay, ID)
    assert normalize(fl.events) == expected_events(lay, "seal", variant, RELEASE)


def test_seal_reuse_sets_the_container_mode(fl: FakeLayout) -> None:
    """M1: a crash between `mkdir` and `fchmod` under umask 077 leaves an
    empty root `0700` container; `reuse` ends it `0711`, events unchanged."""
    lay = fl.layout
    builder_release(fl)
    lay.staging_dir(ID).mkdir(mode=0o700)
    lay.staging_dir(ID).chmod(0o700)
    fl.clear()
    leaves.seal(lay, ID)
    assert normalize(fl.events) == expected_events(lay, "seal", "reuse", RELEASE)
    assert stat.S_IMODE(os.stat(lay.staging_dir(ID)).st_mode) == 0o711


@pytest.mark.parametrize("kind", ["link", "builder", "file"])
def test_seal_refuses_a_container_that_is_not_a_root_dir(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, kind: str
) -> None:
    """M2: `staging/<id>` is checked by `fstatat` before it is opened; a link,
    a foreign directory or a file is refused and never opened."""
    lay = fl.layout
    builder_release(fl)
    box = lay.staging_dir(ID)
    if kind == "link":
        (lay.staging / "other").mkdir(mode=0o711)
        box.symlink_to("other")
    elif kind == "builder":
        fl.builder_dir(box)
    else:
        box.write_text("x\n")
    opened: list[object] = []
    fake_open = os.open

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        opened.append(path)
        return fake_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_)
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        leaves.seal(lay, ID)
    assert exc.value.step == "seal"
    assert ID not in opened and fl.events == []
    assert lay.release_dir(ID).is_dir()


def test_open_root_dir_refuses_a_swapped_object(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """M2: the opened descriptor must be the object `fstatat` checked."""
    lay = fl.layout
    builder_release(fl)
    box = lay.staging_dir(ID)
    box.mkdir(mode=0o711)
    (lay.staging / "swap").mkdir(mode=0o711)
    fake_open = os.open

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        if path == ID and kw.get("dir_fd") is not None:
            box.rmdir()
            (lay.staging / "swap").rename(box)
        return fake_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_)
    with pytest.raises(StepFailed) as exc:
        leaves.seal(lay, ID)
    assert exc.value.step == "seal" and "подменён" in exc.value.detail
    assert lay.release_dir(ID).is_dir()


@pytest.mark.parametrize("content", ["tree", "file"])
def test_seal_reuse_refuses_a_non_empty_container(fl: FakeLayout, content: str) -> None:
    lay = fl.layout
    builder_release(fl)
    lay.staging_dir(ID).mkdir(mode=0o711)
    (lay.staging_dir(ID) / content).mkdir()
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        leaves.seal(lay, ID)
    assert exc.value.step == "seal"
    assert fl.events == [] and lay.release_dir(ID).is_dir()


@pytest.mark.parametrize("missing", ["staging", "releases"])
def test_leaves_refuse_a_missing_installer_dir(fl: FakeLayout, missing: str) -> None:
    """F31: a missing directory is a named refusal, never created."""
    lay = fl.layout
    path = lay.staging if missing == "staging" else lay.releases
    for p in sorted(path.rglob("*"), reverse=True):
        p.rmdir()
    path.rmdir()
    with pytest.raises(StepFailed) as exc:
        leaves.seal(lay, ID)
    assert exc.value.step == "seal" and str(path) in exc.value.detail
    assert not os.path.lexists(path)


# ---------- verify_chmod ----------


def test_verify_chmod_events_and_mode(fl: FakeLayout) -> None:
    lay = fl.layout
    release = root_release(fl)
    fl.clear()
    leaves.verify_chmod(lay, ID)
    assert normalize(fl.events) == expected_events(lay, "verify_chmod", None, RELEASE)
    assert stat.S_IMODE(os.stat(release).st_mode) == 0o755


@pytest.mark.parametrize("kind", ["builder", "link", "absent"])
def test_verify_chmod_refuses_what_is_not_a_root_dir(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, kind: str
) -> None:
    """Only a root directory is opened; a builder's tree or a link is
    refused by `fstatat` before any open (spec 3.8)."""
    lay = fl.layout
    release = lay.release_dir(ID)
    if kind == "builder":
        builder_release(fl)
    elif kind == "link":
        root_release(fl).rename(lay.releases / "other")
        release.symlink_to("other")
    opened: list[object] = []
    fake_open = os.open

    def open_(path: object, flags: int, *a: object, **kw: object) -> int:
        opened.append(path)
        return fake_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_)
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        leaves.verify_chmod(lay, ID)
    assert exc.value.step == "verify"
    assert ID not in opened and fl.events == []


# ---------- every primitive against its protocol rows ----------


def _cleaner_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment's outcome «очистка не удалась»: the cleaner's command
    exits non-zero (the launch chain stays the fake's)."""
    monkeypatch.setattr(plat, "CLEANER_ARGV", lambda _root: [["/usr/bin/false"]])


def _run_cleanup_tree(
    fl: FakeLayout, monkeypatch: pytest.MonkeyPatch, variant: str | None
) -> str:
    lay = fl.layout
    lay.staging_dir(ID).mkdir(mode=0o711)
    tree = lay.staging_tree(ID)
    if variant == "link":
        tree.symlink_to("elsewhere")
    elif variant != "absent":
        (fl.builder_dir(tree) / "f").write_text("x\n")
    if variant in ("quarantine", "failed"):
        _cleaner_fails(monkeypatch)
    label = None if variant == "failed" else ID
    fl.clear()
    outcome = plat.cleanup_tree(
        lay, lay.staging_dir(ID), "tree", quarantine_label=label
    )
    assert outcome == table.CLEANUP_OUTCOME[str(variant)]
    return "staging/<id>/tree"


def _run_cleanup_build_home(
    fl: FakeLayout, _m: pytest.MonkeyPatch, variant: str | None
) -> str:
    if variant == "tree":
        (fl.layout.build_home / "tmp" / "x").mkdir(parents=True)
    fl.clear()
    want = "removed" if variant == "tree" else "absent"
    assert plat.cleanup_build_home(fl.layout, "tmp") == want
    return "build_home/tmp"


def _run_seal(fl: FakeLayout, _m: pytest.MonkeyPatch, variant: str | None) -> str:
    builder_release(fl)
    if variant == "reuse":
        fl.layout.staging_dir(ID).mkdir(mode=0o711)
    fl.clear()
    leaves.seal(fl.layout, ID)
    return RELEASE


def _simple(
    setup: Callable[[FakeLayout], object], run: Callable[[FakeLayout], object], t: str
) -> Callable[[FakeLayout, pytest.MonkeyPatch, str | None], str]:
    def go(fl: FakeLayout, _m: pytest.MonkeyPatch, _v: str | None) -> str:
        setup(fl)
        fl.clear()
        run(fl)
        return t

    return go


def _nothing(_fl: FakeLayout) -> None:
    pass


def _run_install_admin_python(
    fl: FakeLayout, monkeypatch: pytest.MonkeyPatch, _v: str | None
) -> str:
    """The real primitive with the fixture uv (`FAKE_UV`); trusted paths."""
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])
    fl.clear()
    admin.install_admin_python(fl.layout, PY, uv=fake_uv(fl), platform=PLATFORM)
    return "admin-python/cpython-…"


def _run_activate(
    fl: FakeLayout, monkeypatch: pytest.MonkeyPatch, _v: str | None
) -> str:
    """The real `activate` (Task 9) over a registered intact release."""
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: [])
    release_world(fl)
    fl.clear()
    release.activate(fl.layout, ID)
    return "/opt/df/current"


#: name -> run of the real primitive in a variant; returns the target text.
PRIMITIVES: Mapping[
    str, Callable[[FakeLayout, pytest.MonkeyPatch, str | None], str]
] = {
    "write_atomic": _simple(
        _nothing,
        lambda fl: fsutil.write_atomic(obj(fl.layout, "/etc/df/install.toml"), "x\n"),
        "/etc/df/install.toml",
    ),
    "remove_durably": _simple(
        lambda fl: fl.layout.pending.write_text("x\n"),
        lambda fl: fsutil.remove_durably(fl.layout.pending),
        "pending.toml",
    ),
    "remove_own_tree": _simple(
        lambda fl: (fl.layout.admin_dir(ID) / "df").mkdir(parents=True),
        lambda fl: receive.remove_own_tree(fl.layout.admin, ID),
        "admin/<id>",
    ),
    "cleanup_tree": _run_cleanup_tree,
    "prepare_dir": _simple(
        _nothing, lambda fl: leaves.prepare_dir(fl.layout, ID), RELEASE
    ),
    "seal": _run_seal,
    "accept_copy": _simple(staged_tree, copy, RELEASE),
    "verify_chmod": _simple(
        root_release, lambda fl: leaves.verify_chmod(fl.layout, ID), RELEASE
    ),
    "remove_empty_dir": _simple(
        lambda fl: fl.layout.staging_dir(ID).mkdir(mode=0o711),
        lambda fl: receive.remove_empty_dir(fl.layout.staging, ID),
        "staging/<id>",
    ),
    "cleanup_build_home": _run_cleanup_build_home,
    "install_admin_python": _run_install_admin_python,
    "install_admin": _simple(
        devtools_ref,
        lambda fl: admin.install_admin(
            fl.layout, ID, devtools_ref=fl.layout.verify_dir / "devtools"
        ),
        "admin/<id>",
    ),
    "link_command": _simple(
        _nothing, lambda fl: admin.link_command(fl.layout), "/usr/local/bin/darkfactory"
    ),
    "activate": _run_activate,
}


def test_primitives_cover_protocols() -> None:
    """Every protocol has its real primitive here: no later owner task is
    left (Task 9, M18)."""
    assert set(PRIMITIVES) == set(table.PROTOCOLS)
    assert routes.LATER_LEAVES == {}


@pytest.mark.parametrize(
    "name,variant",
    [(n, v) for n in table.PROTOCOLS for v in table.leaf_variants(n)],
)
def test_primitive_protocols_match_table(
    monkeypatch: pytest.MonkeyPatch,
    fl: FakeLayout,
    name: str,
    variant: str | None,
) -> None:
    """S1-R7-N1, S1-R11-N4: the real primitive in `variant` (the preparation
    gives the outcome); its events == leaf_variants(name)[variant] — order,
    multiplicity and objects; event kinds are those of OPS."""
    target = PRIMITIVES[name](fl, monkeypatch, variant)
    assert normalize(fl.events) == expected_events(fl.layout, name, variant, target)
