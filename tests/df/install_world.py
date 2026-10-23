"""The install world of Tasks 9-B/9-C: a fresh host and its stand-ins.

`World` is a host with nothing of `df` but the build account's home: the
manifest, the trusted checkouts, the approved-CLI and trusted-path policies
and the account are stand-ins (recorded in `calls`); the routes, `drive`
and the build with the fake uv are real. `make_world` installs it over
`fake_layout`; `fake_world` (conftest) adds the OS-crash model (`model`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import accept, admin, cli, fsutil, install, journal, launcher_entry, state
from df import build as build_mod
from df import platform as plat
from df.config import (
    Account,
    InstallConfig,
    ReleaseEntry,
    dump_accounts,
    dump_install,
    load_accounts,
    load_pending,
    load_releases,
)
from df.journal import Stable, read_journal
from df.manifest import Profile
from df.paths import Layout
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.crash_model import Crash, CrashModel
from tests.df.route_world import FAKE_UV, PY, devtools_ref, fake_uv

SHA = "0" * 40
OTHER_SHA = "1" * 40
PLATFORM = plat.detect_platform()
FIRST = f"{SHA[:12]}-{PLATFORM}-1"
SECOND = f"{OTHER_SHA[:12]}-{PLATFORM}-1"
PROFILE = Profile("p", PY, ())
CLEAN_PASS = [
    "build-dir",
    "build",
    "seal",
    "copy",
    "verify",
    "admin",
    "register",
    "activate",
    "launcher",
    "commit-config",
]


@dataclass
class World:
    """A fresh host: nothing of `df` but the build account's home."""

    fl: FakeLayout
    mp: pytest.MonkeyPatch
    calls: list[tuple[object, ...]] = field(default_factory=list)
    untrusted: set[Path] = field(default_factory=set)
    model: CrashModel | None = None

    @property
    def lay(self) -> Layout:
        return self.fl.layout

    def cfg(self, sha: str = SHA) -> InstallConfig:
        return InstallConfig(
            manifest_repo="owner/manifest",
            manifest_sha=sha,
            profile="p",
            approved_cli={
                "git": Path("/usr/bin/git"),
                "uv": fake_uv(self.fl),
                "tar": Path("/usr/bin/tar"),
            },
        )

    def init_argv(self, sha: str = SHA) -> list[str]:
        cli_args = [f"{k}={v}" for k, v in sorted(self.cfg(sha).approved_cli.items())]
        return [
            "init",
            "--manifest-repo",
            "owner/manifest",
            "--manifest-sha",
            sha,
            "--profile",
            "p",
            "--cli",
            *cli_args,
        ]

    def bootstrap(self, argv: list[str]) -> int:
        return cli.main(
            argv, mode="bootstrap", read=None, lock_fd=None, layout=self.lay
        )

    def writer(self, argv: list[str]) -> int:
        """The entry's writer half: lock, settle, one read, `main(writer)`."""
        with journal.writer_lock(self.lay) as fd:
            accept.settle_on_acquire(self.lay)
            raw = state.read_raw(self.lay)
            return cli.main(
                argv,
                mode="writer",
                read=Stable(raw, raw.generation),
                lock_fd=fd,
                layout=self.lay,
            )

    def reader(self, argv: list[str], read: state.ReadResult) -> int:
        return cli.main(argv, mode="reader", read=read, lock_fd=None, layout=self.lay)

    def done(self, run_id: str | None = None) -> list[str]:
        rows = read_journal(self.lay.journal)
        return [
            str(r["step"])
            for r in rows
            if r["event"] == "done"
            and (run_id is None or r["run_id"] == run_id)
            and str(r["step"]) in CLEAN_PASS
        ]

    def crash_at(self, action: str) -> None:
        """The next run's `action` raises before it runs (a killed process)."""
        real = install.install_actions

        def actions(run: install.InstallRun) -> dict[str, Callable[..., None]]:
            out = dict(real(run))

            def crash(_facts: object) -> None:
                raise KeyboardInterrupt(f"crash before {action}")

            out[action] = crash
            return out

        self.mp.setattr(install, "install_actions", actions)

    def no_crash(self) -> None:
        self.mp.setattr(install, "install_actions", REAL_ACTIONS)

    # ---- the OS-crash model (`fake_world` only) ----

    @property
    def m(self) -> CrashModel:
        """The OS-crash model of `fake_world`."""
        assert self.model is not None, "fake_world only"
        return self.model

    def entry(self, argv: list[str]) -> int:
        """The shipped entry's writer half (`df/launcher_entry.py`, stdlib):
        `acquire` (root, its own lock opening, its own os.fsync settle),
        `take_snapshot`, `typed_read`, then `main(mode="writer")` -- what
        `darkfactory install` runs, minus the import of the admin code."""
        paths = launcher_entry.EntryPaths(str(self.lay.root))
        self.mp.setattr(launcher_entry, "ROOT_UID", os.geteuid())
        self.mp.setattr(launcher_entry, "owner_uid", self.fl.owner_of)
        fd = launcher_entry.acquire(paths)
        try:
            snap = launcher_entry.take_snapshot(paths, "writer")
            read = launcher_entry.typed_read(state, snap)
            return cli.main(argv, mode="writer", read=read, lock_fd=fd, layout=self.lay)
        finally:
            os.close(fd)

    def retry(self, argv: list[str] | None = None) -> int:
        """A retry by the real path (`install` by default): the shipped
        entry while `current` exists (it refuses without it), else bootstrap
        -- lock, settle, `main`, `recover_pending`, `drive`."""
        run = self.entry if os.path.lexists(self.lay.current) else self.bootstrap
        return run(["install"] if argv is None else argv)

    def killed(self, run: Callable[[], object]) -> None:
        """`run()` dies by a process crash (a trap or `kill_now`), then is
        reaped: the files as the dying process left them (model R7)."""
        with pytest.raises(Crash):
            run()
        self.m.reap()

    def kill_at(self, action: str, *, after: bool = False) -> None:
        """The next run of `action` dies just before it, or just after it
        (its effects done, its journal `done` not written). One-shot."""
        real = install.install_actions
        model = self.m
        armed = [True]

        def actions(run: install.InstallRun) -> dict[str, Callable[..., None]]:
            out = dict(real(run))
            inner = out[action]

            def act(facts: object) -> None:
                if armed[0] and not after:
                    armed[0] = False
                    model.kill_now(f"before {action}")
                inner(facts)
                if armed[0] and after:
                    armed[0] = False
                    model.kill_now(f"after {action}")

            out[action] = act
            return out

        self.mp.setattr(install, "install_actions", actions)

    def kill_after_rollback(self) -> None:
        """The next `install.rollback` dies right after it returns."""
        real = install.rollback
        model = self.m

        def rollback(layout: Layout, pending: object) -> None:
            real(layout, pending)  # type: ignore[arg-type]
            self.mp.setattr(install, "rollback", real)
            model.kill_now("after rollback")

        self.mp.setattr(install, "rollback", rollback)


REAL_ACTIONS = install.install_actions


def make_world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
    """See `World`; the stand-ins are recorded in `calls`."""
    world = World(fake_layout, monkeypatch)
    lay = fake_layout.layout
    lay.build_home.mkdir(parents=True)
    lay.local_bin.mkdir(parents=True)
    uv = fake_uv(fake_layout)
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(admin, "UV_ADMIN_CONFIRMED", frozenset({"0.9.2"}))

    def approved(cli_map: object, platform: object) -> list[str]:
        world.calls.append(("approved_cli_problems",))
        return []

    def trusted(path: Path) -> list[str]:
        return [f"{path}: untrusted"] if path in world.untrusted else []

    def fetch_manifest(repo: str, sha: str) -> bytes:
        world.calls.append(("fetch_manifest", repo, sha))
        return b"manifest"

    def parse_profile(_data: bytes, _profile: str) -> Profile:
        return PROFILE

    def references(layout: object, profile: Profile, *, git: Path) -> dict[str, Path]:
        world.calls.append(("fetch_references", git))
        return {"devtools": devtools_ref(fake_layout)}

    def create(layout: object) -> Account:
        world.calls.append(("create_build_user",))
        return write_ready_account(world)

    def verify(layout: object) -> list[str]:
        return [] if os.path.lexists(lay.accounts) else ["нет записи df-build"]

    monkeypatch.setattr(plat, "approved_cli_problems", approved)
    monkeypatch.setattr(plat, "trusted_path_problems", trusted)
    monkeypatch.setattr(install.manifest_mod, "fetch_manifest", fetch_manifest)
    monkeypatch.setattr(install.manifest_mod, "parse_profile", parse_profile)
    monkeypatch.setattr(install, "fetch_references", references)
    monkeypatch.setattr(plat, "create_build_user", create)
    monkeypatch.setattr(build_mod, "verify_build_user", verify)
    monkeypatch.setattr(build_mod, "whoami_as", lambda _u: (BUILD_UID, BUILD_UID))
    return world


def write_ready_account(world: World) -> Account:
    account = Account(plat.BUILD_USER, BUILD_UID, BUILD_UID, "ready", "2026-10-23")
    # durable, as the real `create_build_user` writes it (`write_atomic`)
    fsutil.write_atomic(world.lay.accounts, dump_accounts({plat.BUILD_USER: account}))
    return account


def files(world: World) -> dict[str, bytes | str]:
    """Every file and link under `/etc/df` and `/var/lib/df` (bytes, target)."""
    out: dict[str, bytes | str] = {}
    for top in (world.lay.etc, world.lay.var):
        for dirpath, dirnames, filenames in os.walk(top):
            for name in [*dirnames, *filenames]:
                path = Path(dirpath) / name
                key = str(path.relative_to(world.lay.root))
                if path.is_symlink():
                    out[key] = os.readlink(path)
                elif path.is_file():
                    out[key] = path.read_bytes()
    return out


def installed(world: World, sha: str = SHA) -> str:
    """A committed first install through bootstrap; returns its id."""
    assert world.bootstrap(world.init_argv(sha)) == 0
    return os.readlink(world.lay.current).removeprefix("releases/")


def interrupt(
    world: World, run: Callable[[list[str]], int], argv: list[str], action: str
) -> None:
    """`run(argv)` killed just before `action`; later runs do not crash."""
    world.crash_at(action)
    with pytest.raises(KeyboardInterrupt):
        run(argv)
    world.no_crash()
    assert os.path.lexists(world.lay.pending)


def registry(world: World) -> dict[str, ReleaseEntry]:
    """`releases.toml` by id (empty when absent)."""
    if not os.path.lexists(world.lay.etc / "releases.toml"):
        return {}
    return {e.id: e for e in load_releases(world.lay.etc / "releases.toml").releases}


def current_id(world: World) -> str | None:
    """The id `current` names, or `None`."""
    if not os.path.lexists(world.lay.current):
        return None
    return os.readlink(world.lay.current).removeprefix("releases/")


def leftover_ids(world: World) -> dict[str, list[str]]:
    """Ids found as root trees, staging containers, admin code and marks."""
    lay = world.lay
    found: dict[str, list[str]] = {}
    for where, top in (
        ("releases", lay.releases),
        ("staging", lay.staging),
        ("admin", lay.admin),
        ("accept", lay.marks_dir),
    ):
        if not os.path.isdir(top):
            continue
        for name in sorted(os.listdir(top)):
            if where == "staging" and name == ".quarantine":
                continue
            rid = name.removesuffix(".toml") if where == "accept" else name
            found.setdefault(rid, []).append(where)
    return found


def at_rest_problems(world: World) -> list[str]:
    """Invariants of any state at rest (after a crash, before a retry):

    - no tree, container, admin code or mark of an id that is neither
      registered nor `pending.toml`'s (an orphan nothing will clean);
    - `current` names a registered, intact release, and its install's state
      under `/var/lib/df` is there: a `ready` `df-build` record, the journal,
      its mark;
    - once an install began (`pending.toml` or `install.toml`), the
      generation file exists -- a missing one is a problem, never 0;
    - without `pending.toml`: `install.toml` exists iff `current` does, and
      then the command of `current` is ready.
    """
    lay = world.lay
    problems: list[str] = []
    reg = registry(world)
    pending = load_pending(lay.pending)
    known = set(reg) | ({pending.release_id} if pending is not None else set())
    for rid, where in leftover_ids(world).items():
        if rid not in known:
            problems.append(f"orphan {rid}: {', '.join(where)}")
    cur = current_id(world)
    has_install = os.path.lexists(lay.etc / "install.toml")
    if (pending is not None or has_install) and not os.path.lexists(lay.generation):
        problems.append("generation file missing")
    if cur is not None:
        problems += _active_state_problems(world, cur, reg)
    if pending is None:
        if has_install != (cur is not None):
            problems.append(f"install.toml {has_install}, current {cur}")
        if cur is not None and not install.command_ready(lay, cur):
            problems.append(f"command of {cur} not ready")
    return problems


def _active_state_problems(
    world: World, cur: str, reg: dict[str, ReleaseEntry]
) -> list[str]:
    lay = world.lay
    problems: list[str] = []
    entry = reg.get(cur)
    if entry is None:
        problems.append(f"current {cur}: not registered")
    elif not accept.entry_intact(lay, cur, entry, admin_ready=admin.admin_ready):
        problems.append(f"current {cur}: not intact")
    accounts = load_accounts(lay.accounts) if os.path.lexists(lay.accounts) else {}
    account = accounts.get(plat.BUILD_USER)
    if account is None or account.state != "ready":
        problems.append("accounts.toml: no ready df-build record")
    if not os.path.lexists(lay.journal):
        problems.append("journal missing")
    if not os.path.lexists(lay.mark_file(cur)):
        problems.append(f"mark of {cur} missing")
    return problems


def unsynced_besides_checkout(world: World) -> list[str]:
    """`unsynced()` without the two scratch trees nothing syncs by design:
    root's checkout (fetched again on every run) and what is inside the
    build home (`cleanup_build_home`: «nothing is synced», spec 3.3)."""
    lay = world.lay
    scratch = tuple(
        str(p.relative_to(lay.root)) + "/" for p in (lay.verify_dir, lay.build_home)
    )
    return [p for p in world.m.unsynced() if not p.startswith(scratch)]


def committed_problems(world: World, release_id: str, cfg: InstallConfig) -> list[str]:
    """`at_rest_problems` plus: committed on `release_id` with `cfg`, the
    generation file present and even, and -- the universal invariant of a
    successful run -- nothing the model holds unsynced (`fake_world`)."""
    lay = world.lay
    problems = at_rest_problems(world)
    if os.path.lexists(lay.pending):
        problems.append("pending.toml still there")
    if current_id(world) != release_id:
        problems.append(f"current {current_id(world)}, expected {release_id}")
    install_toml = lay.etc / "install.toml"
    if not os.path.lexists(install_toml) or install_toml.read_text() != dump_install(
        cfg
    ):
        problems.append("install.toml is not the installed configuration")
    if not os.path.lexists(lay.generation):
        problems.append("generation file missing")
    elif journal.read_generation(lay) % 2:
        problems.append("generation odd")
    if world.model is not None:
        problems += [f"unsynced {p}" for p in unsynced_besides_checkout(world)]
    return problems
