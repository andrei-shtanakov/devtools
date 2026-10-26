"""Root acceptance of the `df-build` account on the real host (Task 13-C, R73).

§3.8 lines of the account group over the root world of
`tests/df/root_world.py`: the real `create_build_user` (dscl on macOS,
groupadd/useradd on Linux), stopped at the OS primitives' boundaries and
resumed; foreign records and homes never adopted; decoy accounts named
`df-build` refused before any build process; and (Linux, R87) the account
made through `main` (`df.cli.main`, bootstrap `init`). Runs only in CI
df-stage1. A stop is `Killed`, a `BaseException`: no handler of df catches
it, as no handler survives a real kill.
"""

from __future__ import annotations

import grp
import os
import pwd
import stat
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from df import cli, fsutil, install, journal
from df import platform as plat
from df.config import Account, dump_accounts, load_accounts
from df.journal import StepFailed, read_journal, run_steps
from df.paths import Layout
from tests.df.root_world import (
    RootWorld,
    _flush_ds_cache,
    assert_committed,
    infra,
    remove_build_user,
    run,
)

pytestmark = pytest.mark.root

PLATFORM = plat.detect_platform()
MACOS = PLATFORM == "macos-arm64"
NAME = plat.BUILD_USER
OPS = plat.ACCOUNT_ATTRS[PLATFORM]
#: S1-R6-N2: a stop after each OS command of this platform's record.
ATTR_STOPS = [f"attr:{i}" for i in range(1, len(OPS) + 1)]
#: fault-point kind of `_make_home` per home stop.
HOME_STOPS = {"home-mkdir": "created", "home-chown": "chown", "home-rename": "renamed"}
STOPS = ["intent", *ATTR_STOPS, *HOME_STOPS]
_DSEDITGROUP = "/usr/sbin/dseditgroup"
_USERMOD = "/usr/sbin/usermod"
#: The administrative group a decoy joins (`plat.ADMIN_GROUPS`).
ADMIN_GROUP = "admin" if MACOS else "sudo"


class Killed(BaseException):
    """A process kill at a chosen point (never caught by df)."""


def _is_create(argv: Sequence[str]) -> bool:
    """An OS command that writes an account record (`attr_argv`)."""
    if list(argv[:1]) == [plat._DSCL]:
        return "-create" in argv
    return list(argv[:1]) in ([plat._GROUPADD], [plat._USERADD])


@dataclass
class CommandSpy:
    """Records the record-writing commands; kills after the n-th one."""

    real: Callable[..., plat.RunResult]
    kill_after: int | None = None
    commands: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], **kw: Any) -> plat.RunResult:
        result = self.real(argv, **kw)
        if not _is_create(argv):
            return result
        self.commands.append(list(argv))
        done = result.launched and result.returncode == 0
        if done and self.kill_after == len(self.commands):
            raise Killed(f"после команды {len(self.commands)}: {argv}")
        return result


def _spy(monkeypatch: pytest.MonkeyPatch) -> CommandSpy:
    spy = CommandSpy(plat.run_command)
    monkeypatch.setattr(plat, "run_command", spy)
    return spy


def _launches(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[str, ...]]]:
    """Every launch as a role: `run_as` and `spawn_as` both build `role_argv`."""
    seen: list[tuple[str, tuple[str, ...]]] = []
    real = plat.role_argv

    def role_argv(
        platform: plat.PlatformName, user: str, argv: Sequence[str], **kw: Any
    ) -> list[str]:
        seen.append((user, tuple(argv)))
        return real(platform, user, argv, **kw)

    monkeypatch.setattr(plat, "role_argv", role_argv)
    return seen


def _arm_intent(mp: pytest.MonkeyPatch, layout: Layout) -> None:
    real = fsutil.write_atomic

    def write(path: Path, text: str, mode: int = 0o644) -> None:
        real(path, text, mode)
        if path == layout.accounts and 'state = "creating"' in text:
            raise Killed("после записи намерения")

    mp.setattr(fsutil, "write_atomic", write)


def _arm_home(mp: pytest.MonkeyPatch, layout: Layout, kind: str) -> None:
    home = layout.build_home
    places = {home, home.parent / plat.HOME_TMP_NAME}
    real = fsutil.fault_point

    def fault(at: fsutil.FaultKind, path: Path) -> None:
        real(at, path)
        if at == kind and path in places:
            raise Killed(f"{kind} {path}")

    mp.setattr(fsutil, "fault_point", fault)


def _arm(mp: pytest.MonkeyPatch, layout: Layout, spy: CommandSpy, stop: str) -> int:
    """Arm `stop`; return how many record commands run before it."""
    if stop == "intent":
        _arm_intent(mp, layout)
        return 0
    if stop in HOME_STOPS:
        _arm_home(mp, layout, HOME_STOPS[stop])
        return len(OPS)
    spy.kill_after = int(stop.split(":")[1])
    return spy.kill_after


def _account(layout: Layout) -> Account | None:
    return load_accounts(layout.accounts).get(NAME)


def _expected(account: Account, layout: Layout) -> list[list[str]]:
    return [plat.attr_argv(PLATFORM, op, account, layout) for op in OPS]


# ---------- partial creation, resumed ----------


@pytest.mark.parametrize("stop_after", STOPS)
def test_partial_account_creation_resumed(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch, stop_after: str
) -> None:
    """A real stop after the intent, after each OS command of the record
    (macOS: `RecordName` and every dscl attribute) and at each step of the
    home: the partial record is visible, the retry writes only what is
    missing, reaches `ready`, and `whoami_as` gives the recorded UID/GID
    (spec 3.3, S1-R5-N3, S1-R6-N2)."""
    lay = root_world.layout
    spy = _spy(monkeypatch)
    with monkeypatch.context() as mp:
        done = _arm(mp, lay, spy, stop_after)
        with pytest.raises(Killed):
            root_world.prepare_build_user()
    first = _account(lay)
    assert first is not None and first.state == "creating", first
    expected = _expected(first, lay)
    assert spy.commands == expected[:done]
    _assert_visible(first, lay, done)
    _assert_home_at_stop(first, lay, stop_after)
    spy.commands.clear()
    spy.kill_after = None
    account = root_world.prepare_build_user()
    assert (account.state, account.uid, account.gid) == ("ready", first.uid, first.gid)
    assert spy.commands == expected[done:]
    assert plat.verify_build_user(lay) == []
    assert plat.whoami_as(NAME) == (first.uid, first.gid)
    assert not os.path.lexists(lay.build_home.parent / plat.HOME_TMP_NAME)


def _assert_visible(account: Account, layout: Layout, done: int) -> None:
    """Each element written before the stop is in the OS record."""
    for op in OPS[:done]:
        record = plat.read_record(op.kind, NAME)
        assert record is not None, op
        fields = plat._op_fields(PLATFORM, op, account, layout)
        assert fields.items() <= record.items(), (op, record)


def _assert_home_at_stop(account: Account, layout: Layout, stop: str) -> None:
    """The home is exactly where the stop left it."""
    home = layout.build_home
    reserved = home.parent / plat.HOME_TMP_NAME
    if stop not in HOME_STOPS:
        assert not os.path.lexists(home) and not os.path.lexists(reserved)
        return
    if stop == "home-rename":
        assert not os.path.lexists(reserved)
        assert os.lstat(home).st_uid == account.uid
        return
    assert not os.path.lexists(home)
    owner = 0 if stop == "home-mkdir" else account.uid
    assert os.lstat(reserved).st_uid == owner


# ---------- foreign records and homes ----------


#: `_write_intent`'s refusal of records it did not make (spec 3.2: «отказ с
#: инструкцией (удалить или переименовать)»).
FOREIGN = (
    f"пользователь {NAME} существует, но создан не darkfactory: "
    "удалите или переименуйте"
)
#: The attribute of the group's GID in `_refuse_conflict`'s text.
GID_ATTR = "PrimaryGroupID" if MACOS else "gid"


@pytest.mark.parametrize(
    "case",
    ["record-before-intent", "user-before-intent", "partial-with-conflicting-gid"],
)
def test_conflicting_record_not_adopted(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A foreign group `df-build`, or a foreign user record alone, before
    the intent; or a group with another GID under our intent: `StepFailed`
    with its exact text, the records untouched, no record command run."""
    lay = root_world.layout
    spy = _spy(monkeypatch)
    uid, gid = _free_ids(2)
    if case == "partial-with-conflicting-gid":
        with monkeypatch.context() as mp:
            _arm_intent(mp, lay)
            with pytest.raises(Killed):
                root_world.prepare_build_user()
        intent = _account(lay)
        assert intent is not None
        gid = next(i for i in _free_ids(2) if i != intent.gid)
        text = f"group {NAME}: {GID_ATTR} = {gid}, в намерении {intent.gid}"
    else:
        text = FOREIGN
    if case == "user-before-intent":
        _make_lone_user(uid)
    else:
        _make_group(gid)
    records = _records()
    assert records["user" if case == "user-before-intent" else "group"] is not None
    with pytest.raises(StepFailed) as raised:
        root_world.prepare_build_user()
    assert raised.value.detail == text
    assert _records() == records
    assert spy.commands == []
    account = _account(lay)
    if case == "partial-with-conflicting-gid":
        assert account is not None and account.state == "creating"
    else:
        assert account is None


def test_foreign_home_dir_not_adopted(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory of root at the home path: `StepFailed`, the directory and
    its content untouched, no record written."""
    lay = root_world.layout
    home = lay.build_home
    os.mkdir(home, 0o755)
    (home / "keep").write_text("operator\n")
    before = os.lstat(home)
    spy = _spy(monkeypatch)
    with pytest.raises(StepFailed, match="создан не darkfactory"):
        root_world.prepare_build_user()
    after = os.lstat(home)
    assert (after.st_ino, after.st_mode, after.st_uid) == (
        before.st_ino,
        before.st_mode,
        before.st_uid,
    )
    assert (home / "keep").read_text() == "operator\n"
    assert spy.commands == []
    assert _account(lay) is None
    assert plat.read_record("user", NAME) is None
    assert plat.read_record("group", NAME) is None


# ---------- decoy accounts ----------

#: Case -> what `verify_build_user` names.
DECOYS = {
    "uid0": "uid 0",
    "admin-group": "административные группы",
    "foreign-home": "владелец uid 0",
    "not-in-accounts": "нет записи",
}


@pytest.mark.parametrize("case", list(DECOYS))
def test_preexisting_build_user_refused_before_build(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A decoy `df-build` made by the test (UID 0, in an administrative
    group, a home of root, or without a record in `accounts.toml` -- and
    then without a home, so only the record can refuse it): `bootstrap
    init` refuses at `build-user`; no process runs as a role, none is reaped
    (`pkill -u` of a UID-0 decoy would kill every root process: recorded,
    never run), nothing is staged, the decoy's records are untouched."""
    lay = root_world.layout
    with _decoy(root_world, case):
        records = _records()
        launches = _launches(monkeypatch)
        with monkeypatch.context() as mp:
            reaps = _record_reaps(mp)
            rc = root_world.bootstrap(root_world.init_argv())
        assert rc == cli.EXIT_REFUSED
        failed = [r for r in read_journal(lay.journal) if r["event"] == "failed"]
        assert [r["step"] for r in failed] == ["build-user"], failed
        if case == "not-in-accounts":
            assert failed[0]["detail"] == f"StepFailed: build-user: {FOREIGN}"
        problems = plat.verify_build_user(lay)
        assert any(DECOYS[case] in p for p in problems), problems
        assert launches == []
        assert reaps == []
        assert _records() == records
        assert sorted(os.listdir(lay.releases)) == []
        assert sorted(os.listdir(lay.staging)) == [lay.quarantine.name]
    if case == "admin-group":
        assert NAME not in _admin_members()


def _record_reaps(mp: pytest.MonkeyPatch) -> list[str]:
    """`reap_user_processes` calls, recorded and never run."""
    calls: list[str] = []

    def reap(name: str, **kw: Any) -> list[int]:
        calls.append(name)
        return []

    mp.setattr(plat, "reap_user_processes", reap)
    return calls


def _admin_members() -> list[str]:
    """Members of `ADMIN_GROUP` as the OS records them (not a libc cache)."""
    if MACOS:
        path = f"/Groups/{ADMIN_GROUP}"
        out = run([plat._DSCL, ".", "-read", path, "GroupMembership"])
        return out.split()[1:]
    return run([plat._GETENT, "group", ADMIN_GROUP]).strip().split(":")[3].split(",")


@contextmanager
def _decoy(world: RootWorld, case: str) -> Iterator[None]:
    """The decoy of `case` while the block runs; removed afterwards."""
    try:
        _make_decoy(world, case)
        yield
    finally:
        _remove_decoy(case)


def _make_decoy(world: RootWorld, case: str) -> None:
    lay = world.layout
    _prep_dirs(world)
    uid, gid = _free_ids(2)
    if case == "uid0":
        uid = 0
    else:
        gid = uid
    _make_group(gid)
    _make_user(uid, gid, lay.build_home, non_unique=uid == 0)
    owner = 0 if case == "foreign-home" else uid
    if case != "not-in-accounts":
        os.mkdir(lay.build_home, 0o700)
        os.chown(lay.build_home, owner, gid)
    if case == "admin-group":
        _join_admin()
    if case != "not-in-accounts":
        ready = Account(NAME, uid, gid, "ready", "2026-10-01T00:00:00+00:00")
        fsutil.write_atomic(lay.accounts, dump_accounts({NAME: ready}))
    _wait_visible(uid, admin=case == "admin-group")


def _prep_dirs(world: RootWorld) -> None:
    """Steps 1-2 only: the directories, no account."""
    steps = install.install_steps(world.layout, world.cfg(), install.Prepared())
    with journal.writer_lock(world.layout):
        run_steps(steps[:2], world.layout.journal, run_id="decoy")


def _remove_decoy(case: str) -> None:
    if case == "admin-group" and MACOS:
        run([_DSEDITGROUP, "-o", "edit", "-d", NAME, "-t", "user", ADMIN_GROUP])
    if case == "uid0":
        remove_build_user(force=True)  # plain userdel refuses: root runs processes
        return
    remove_build_user()


def _records() -> dict[str, dict[str, str] | None]:
    return {kind: plat.read_record(kind, NAME) for kind in ("user", "group")}


# ---------- OS records made by the test ----------


def _free_ids(n: int) -> list[int]:
    """The `n` lowest ids of the range free both as a UID and as a GID."""
    used = {p.pw_uid for p in pwd.getpwall()} | {g.gr_gid for g in grp.getgrall()}
    free = [i for i in plat.ID_RANGE[PLATFORM] if i not in used][:n]
    if len(free) < n:
        infra(f"нет {n} свободных id в {plat.ID_RANGE[PLATFORM]}")
    return free


def _make_group(gid: int) -> None:
    if MACOS:
        run([plat._DSCL, ".", "-create", f"/Groups/{NAME}"])
        run([plat._DSCL, ".", "-create", f"/Groups/{NAME}", "PrimaryGroupID", str(gid)])
        return
    run([plat._GROUPADD, "--gid", str(gid), NAME])


def _make_user(uid: int, gid: int, home: Path, *, non_unique: bool) -> None:
    if not MACOS:
        argv = [plat._USERADD, "--uid", str(uid), *(["-o"] if non_unique else [])]
        argv += ["--gid", NAME, "--home-dir", str(home), "--no-create-home"]
        run([*argv, "--shell", "/usr/sbin/nologin", NAME])
        return
    path = f"/Users/{NAME}"
    run([plat._DSCL, ".", "-create", path])
    attrs = {
        "UniqueID": str(uid),
        "PrimaryGroupID": str(gid),
        "UserShell": "/usr/bin/false",
        "NFSHomeDirectory": str(home),
        "Password": "*",
    }
    for attr, value in attrs.items():
        run([plat._DSCL, ".", "-create", path, attr, value])


def _make_lone_user(uid: int) -> None:
    """A user record `df-build` without a group of its name."""
    if MACOS:
        path = f"/Users/{NAME}"
        run([plat._DSCL, ".", "-create", path])
        run([plat._DSCL, ".", "-create", path, "UniqueID", str(uid)])
        run([plat._DSCL, ".", "-create", path, "PrimaryGroupID", "-2"])
        return
    argv = [plat._USERADD, "--uid", str(uid), "--no-user-group", "--gid", "nogroup"]
    run([*argv, "--no-create-home", "--shell", "/usr/sbin/nologin", NAME])


def _join_admin() -> None:
    if MACOS:
        run([_DSEDITGROUP, "-o", "edit", "-a", NAME, "-t", "user", ADMIN_GROUP])
    else:
        run([_USERMOD, "-aG", ADMIN_GROUP, NAME])


def _wait_visible(uid: int, *, admin: bool) -> None:
    """Until libc sees the decoy as made (the macOS directory cache lags)."""
    admin_gid = grp.getgrnam(ADMIN_GROUP).gr_gid
    deadline = time.monotonic() + 30
    while True:
        try:
            entry = pwd.getpwnam(NAME)
            seen = entry.pw_uid == uid and (
                not admin or admin_gid in os.getgrouplist(NAME, entry.pw_gid)
            )
        except KeyError:
            seen = False
        if seen:
            return
        if time.monotonic() > deadline:
            infra(f"приманка {NAME} (uid {uid}) не видна libc")
        if MACOS:
            _flush_ds_cache()
        time.sleep(0.5)


# ---------- the account through main (Linux, R87) ----------


@pytest.mark.skipif(sys.platform != "linux", reason="Linux: useradd через main")
@pytest.mark.parametrize("crash", ["process", "os"])
def test_account_created_through_main_linux(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch, crash: str
) -> None:
    """9-C Minor 8, 9-D1 concern 5: the real `create_build_user` (groupadd,
    useradd) and `verify_build_user` through `main` (bootstrap `init`),
    killed right after the home's `rename_noreplace`; the retry through
    `main` commits. `os` adds what a real host shows after a reboot without
    power loss: a new boot id and dropped clean page caches. Losing
    unsynced writes is not producible on a real file system; that half is
    the emulated twin's (`test_crash_windows::test_account_created_through_main`).
    """
    lay = root_world.layout
    spy = _spy(monkeypatch)
    with monkeypatch.context() as mp:
        _arm_home(mp, lay, "renamed")
        with pytest.raises(Killed):
            root_world.bootstrap(root_world.init_argv())
    first = _account(lay)
    assert first is not None and first.state == "creating"
    assert spy.commands == _expected(first, lay)
    assert os.lstat(lay.build_home).st_uid == first.uid
    if crash == "os":
        _reboot_without_power_loss(monkeypatch)
    spy.commands.clear()
    assert root_world.bootstrap(root_world.init_argv()) == cli.EXIT_OK
    assert spy.commands == []
    account = _account(lay)
    assert account is not None and account.state == "ready"
    assert (account.uid, account.gid) == (first.uid, first.gid)
    assert plat.verify_build_user(lay) == []
    assert_committed(lay)
    home = os.lstat(lay.build_home)
    assert (home.st_uid, stat.S_IMODE(home.st_mode)) == (first.uid, 0o700)


def _reboot_without_power_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    plat.reap_user_processes(NAME)
    os.sync()
    Path("/proc/sys/vm/drop_caches").write_text("3\n")
    rebooted = f"{fsutil.boot_id()}-rebooted"
    monkeypatch.setattr(fsutil, "boot_id", lambda: rebooted)
