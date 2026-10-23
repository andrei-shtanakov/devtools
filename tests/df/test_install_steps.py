"""Tests for the install run and the command (Task 9, part 9-B).

`df.install` part 2 (`install_steps`, `read_pending`, `recover_pending`,
`rollback`, `run_install`) and `df.cli.main`, through the real path: bootstrap
(`main(mode="bootstrap")` takes the lock, settles and reads the snapshot) or
the entry's writer half (`as_writer`: lock, settle, `read_raw`, then
`main(mode="writer")`). Everything runs in `fake_layout` as the current user
(root faked): the manifest, the trusted checkouts, the approved-CLI and the
trusted-path policies are stand-ins; the routes, `drive` and the build with
the fake uv are real. No OS-crash model here (9-C).
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import (
    accept,
    admin,
    cli,
    fsutil,
    inputs,
    install,
    journal,
    lockset,
    receive,
    state,
)
from df import build as build_mod
from df import platform as plat
from df.config import (
    Account,
    InstallConfig,
    Pending,
    config_digest,
    dump_accounts,
    dump_install,
    dump_pending,
    load_releases,
)
from df.digest import TreeError
from df.journal import NeedsHuman, Stable, StepFailed, Transitional, read_journal
from df.manifest import Profile, RepoPin
from df.paths import Layout
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.route_world import (
    BOOT,
    FAKE_UV,
    PY,
    devtools_ref,
    fake_uv,
)

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


REAL_ACTIONS = install.install_actions


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def w(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
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
    world.lay.accounts.write_text(dump_accounts({plat.BUILD_USER: account}))
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


# ---------- the clean pass ----------


def test_bootstrap_init_installs_from_nothing(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh host: bootstrap `init` runs steps 1-3, then every row to
    `committed`; the generation ends even, the command is printed."""
    assert w.bootstrap(w.init_argv()) == 0, capsys.readouterr().err
    lay = w.lay
    assert w.done() == CLEAN_PASS
    assert os.readlink(lay.current) == f"releases/{FIRST}"
    assert (lay.etc / "install.toml").read_text() == dump_install(w.cfg())
    assert not os.path.lexists(lay.pending)
    assert journal.read_generation(lay) % 2 == 0
    assert install.command_ready(lay, FIRST)
    out = capsys.readouterr().out
    assert f"установлен релиз {FIRST}" in out
    assert str(lay.local_bin / "darkfactory") in out
    for path, mode in install.prep_dirs(lay):
        assert os.stat(path).st_mode & 0o7777 == mode, path


@pytest.mark.parametrize("step", ["trusted-paths", "build-user"])
def test_interrupt_prep_step_then_rerun_completes(w: World, step: str) -> None:
    """The real `run_steps` with `fail_after`: interrupted after `do()` of a
    preparation step, the rerun finds the fact achieved (`skip`) and the
    install completes. Step 1 has no `do()` (a pure check): nothing to
    interrupt after it."""
    lay = w.lay
    if step == "build-user":
        install._make_prep_dirs(lay)  # noqa: SLF001 - step 2 already done
    real = install.run_install

    def interrupted(*a: object, **kw: object) -> str:
        return real(*a, **kw, fail_after=step)  # type: ignore[arg-type]

    w.mp.setattr(install, "run_install", interrupted)
    # the test-only interruption is no refusal: a program error to `main`
    assert w.bootstrap(w.init_argv()) == 1
    w.mp.setattr(install, "run_install", real)
    assert w.bootstrap(w.init_argv()) == 0
    events = [(r["event"]) for r in read_journal(lay.journal) if r["step"] == step]
    assert events == ["start", "skip"]
    assert w.done() == CLEAN_PASS
    manifest = [
        r["event"] for r in read_journal(lay.journal) if r["step"] == "manifest"
    ]
    assert set(manifest) == {"skip"}


# ---------- refusals of the command ----------


def test_init_refuses_existing_install(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    installed(w)
    before = files(w)
    for run in (w.bootstrap, w.writer):
        assert run(w.init_argv(OTHER_SHA)) == 2
        assert "установка уже есть: darkfactory install" in capsys.readouterr().err
    assert files(w) == before


def test_init_with_pending_and_install_toml_refused(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupted upgrade (`pending.toml` and `install.toml`): `init`
    refuses; nothing changes."""
    installed(w)
    interrupt(w, w.writer, ["install"], "build")
    assert os.path.lexists(w.lay.pending)
    before = files(w)
    assert w.writer(w.init_argv()) == 2
    assert "установка уже есть" in capsys.readouterr().err
    assert files(w) == before


def test_init_requires_root(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """M14 (security order: euid first): not root — exit 2 before the lock,
    so nothing is created."""
    w.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    locks: list[object] = []
    w.mp.setattr(journal, "writer_lock", lambda lay: locks.append(lay))
    for argv in (w.init_argv(), ["install"]):
        assert w.bootstrap(argv) == 2
        assert "требует root" in capsys.readouterr().err
    assert locks == []
    assert not os.path.lexists(w.lay.var)


def test_init_lock_loser_changes_nothing(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    installed(w)
    before = files(w)
    with journal.writer_lock(w.lay):
        assert w.bootstrap(w.init_argv(OTHER_SHA)) == 2
        assert w.bootstrap(["install"]) == 2
    assert "идёт другая административная операция" in capsys.readouterr().err
    assert files(w) == before


def test_commands_refuse_while_pending(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """M19: the common filter refuses a writer subcommand other than
    `init`/`install` while `pending.toml` exists — before dispatch (`models
    set` is Task 10's); without `pending.toml` the same call reaches the
    dispatch stub instead."""
    installed(w)
    assert w.writer(["models", "set", "x"]) == 2
    assert "ещё не реализовано (Task 10)" in capsys.readouterr().err
    interrupt(w, w.writer, ["install"], "build")
    assert w.writer(["models", "set", "x"]) == 2
    assert "завершите darkfactory install" in capsys.readouterr().err


def test_reader_mode_refuses_writer_subcommand(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """(c): `mode_of` decides: a writer subcommand in reader mode (the entry
    took no lock) is exit 2 and reaches nothing."""
    installed(w)
    raw = state.read_raw(w.lay)
    runs: list[object] = []
    w.mp.setattr(install, "run_install", lambda *a, **kw: runs.append(a))
    for argv in (["install"], w.init_argv(), ["models", "set", "x"]):
        assert w.reader(argv, Stable(raw, raw.generation)) == 2
        assert "без блокировки" in capsys.readouterr().err
    assert runs == []


def test_reader_transitional(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """A reader in a transitional state: `doctor` reaches its (Task 12) stub,
    the rest refuse naming the interrupted operation."""
    assert w.reader(["models"], Transitional(3)) == 2
    assert "идёт или прервана административная операция" in capsys.readouterr().err
    assert w.reader(["doctor"], Transitional(3)) == 2
    assert "Task 12" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode,read,lock_fd",
    [
        ("writer", None, 5),
        ("writer", Transitional(1), 5),
        ("writer", "stable", None),
        ("reader", "stable", 5),
        ("reader", None, None),
        ("bootstrap", "stable", None),
        ("bootstrap", None, 5),
        ("other", None, None),
    ],
)
def test_main_refuses_a_wrong_call(
    w: World,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    read: object,
    lock_fd: int | None,
) -> None:
    """The entry's contract: a combination it never makes is exit 2."""
    if read == "stable":
        read = Stable(state.RawSnapshot(0, None, None, None, None, False), 0)
    code = cli.main(
        ["install"],
        mode=mode,
        read=read,
        lock_fd=lock_fd,
        layout=w.lay,  # type: ignore[arg-type]
    )
    assert code == 2
    assert "ошибка программы" in capsys.readouterr().err
    assert not os.path.lexists(w.lay.var)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope"],
        ["init"],
        ["init", "--manifest-repo", "r"],
        ["install", "-x"],
        ["--help"],
        ["install", "--help"],
    ],
)
def test_usage_errors_are_exit_2(w: World, argv: list[str]) -> None:
    """N1: argparse never exits on its own; a bad command line is exit 2."""
    assert w.bootstrap(argv) == 2
    assert not os.path.lexists(w.lay.var)


@pytest.mark.parametrize(
    "cli_args",
    [["git=/usr/bin/git", "git=/bin/git"], ["git"], ["=x"], ["uv=relative"]],
)
def test_init_cli_pairs_refused(w: World, cli_args: list[str]) -> None:
    argv = [*w.init_argv()[:7], "--cli", *cli_args]
    assert w.bootstrap(argv) == 2


def test_exit_codes(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """M7: `NeedsHuman` is 3 with the instruction; a program error is 1 with
    its traceback; a bare `TreeError`/`OSError` is a named exit 2."""
    cases: list[tuple[BaseException, int, str]] = [
        (NeedsHuman("создайте аккаунт"), 3, "создайте аккаунт"),
        (AssertionError("bug"), 1, "Traceback"),
        (TreeError("tree"), 2, "darkfactory: tree"),
        (OSError(5, "io"), 2, "darkfactory: [Errno 5] io"),
    ]
    for exc, code, text in cases:

        def boom(*_a: object, exc: BaseException = exc, **_k: object) -> str:
            raise exc

        w.mp.setattr(install, "run_install", boom)
        assert w.bootstrap(w.init_argv()) == code
        assert text in capsys.readouterr().err


# ---------- recovery of an interrupted install ----------


def pending_of(world: World) -> Pending:
    pending = install.read_pending(world.lay)
    assert pending is not None
    return pending


def leftovers(world: World, release_id: str) -> list[Path]:
    """What of `release_id` is still on disk."""
    lay = world.lay
    paths = [
        lay.release_dir(release_id),
        lay.staging_dir(release_id),
        lay.admin_dir(release_id),
        lay.mark_file(release_id),
    ]
    return [p for p in paths if os.path.lexists(p)]


@pytest.mark.parametrize("config", ["same", "other"])
@pytest.mark.parametrize("entry", ["bootstrap", "darkfactory"])
def test_init_with_pending_first_install(w: World, entry: str, config: str) -> None:
    """S1-R7-N3, I8. Bootstrap (killed before `register`, `current` absent):
    the same configuration continues the same id; another rolls back and
    installs a new id. Through `darkfactory` (killed after `activate`, so
    `current` is the pending id): both continue the same id, the request is
    ignored with a `warn`."""
    sha = SHA if config == "same" else OTHER_SHA
    if entry == "bootstrap":
        interrupt(w, w.bootstrap, w.init_argv(), "register")
        assert w.bootstrap(w.init_argv(sha)) == 0
    else:
        interrupt(w, w.bootstrap, w.init_argv(), "launcher")
        assert os.readlink(w.lay.current) == f"releases/{FIRST}"
        assert w.writer(w.init_argv(sha)) == 0
    want = FIRST if config == "same" or entry == "darkfactory" else SECOND
    assert os.readlink(w.lay.current) == f"releases/{want}"
    assert not os.path.lexists(w.lay.pending)
    rows = read_journal(w.lay.journal)
    warned = [r for r in rows if r["event"] == "warn" and r["step"] == "pending"]
    assert bool(warned) == (entry == "darkfactory" and config == "other")
    if want == SECOND:
        assert leftovers(w, FIRST) == []
        assert (w.lay.etc / "install.toml").read_text() == dump_install(w.cfg(sha))
    else:
        assert (w.lay.etc / "install.toml").read_text() == dump_install(w.cfg())


def test_resume_with_other_config_rolls_back(w: World) -> None:
    """Killed after `build`; `init` with another `manifest_sha` -> the tree
    is cleaned (the route `rollback`), a new id is installed."""
    interrupt(w, w.bootstrap, w.init_argv(), "seal")
    assert os.path.lexists(w.lay.release_dir(FIRST))
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert leftovers(w, FIRST) == []
    assert os.readlink(w.lay.current) == f"releases/{SECOND}"
    rows = read_journal(w.lay.journal)
    assert [r["event"] for r in rows if r["step"] == "rollback"] == ["start", "done"]


def test_registered_unactivated_survives_rollback(w: World) -> None:
    """Killed after `register`, before `activate`: a rollback leaves the
    registered release (inactive), removes only `pending.toml`, and the new
    install proceeds."""
    interrupt(w, w.bootstrap, w.init_argv(), "activate")
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert os.path.isdir(w.lay.release_dir(FIRST))
    ids = {e.id for e in load_releases(w.lay.etc / "releases.toml").releases}
    assert ids == {FIRST, SECOND}
    assert os.readlink(w.lay.current) == f"releases/{SECOND}"


def test_generation_even_after_success_and_rollback(w: World) -> None:
    """M6: odd while a pending install lives; even after the success, after
    a rollback alone (then odd again for the new pending) and at the end."""
    gens: list[int] = []
    real = install.rollback

    def spy(layout: object, pending: Pending) -> None:
        real(layout, pending)  # type: ignore[arg-type]
        gens.append(journal.read_generation(w.lay))

    w.mp.setattr(install, "rollback", spy)
    installed(w)
    assert journal.read_generation(w.lay) % 2 == 0
    interrupt(w, w.writer, ["install"], "build")
    assert journal.read_generation(w.lay) % 2 == 1
    (w.lay.etc / "install.toml").unlink()  # back to an interrupted first install
    os.unlink(w.lay.current)
    pend = pending_of(w)
    w.lay.pending.write_text(
        dump_pending(dataclasses.replace(pend, previous_current=None))
    )
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert len(gens) == 1 and gens[0] % 2 == 0
    assert journal.read_generation(w.lay) % 2 == 0


def test_odd_generation_without_pending_is_closed(w: World) -> None:
    """M9: a crash between removing `pending.toml` and `generation_end`; the
    next writer makes it even with a `warn`, then works as usual."""
    installed(w)
    journal.generation_begin(w.lay)
    g = journal.read_generation(w.lay)
    assert w.writer(["install"]) == 0
    rows = read_journal(w.lay.journal)
    assert any(r["step"] == "generation" and r["event"] == "warn" for r in rows)
    assert (
        journal.read_generation(w.lay) % 2 == 0 and journal.read_generation(w.lay) > g
    )


def test_third_current_is_refused(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """M10: `current` neither the pending id nor `previous_current` — a human
    decides; nothing is removed."""
    first = installed(w)
    interrupt(w, w.writer, ["install"], "build")
    pend = pending_of(w)
    w.lay.pending.write_text(
        dump_pending(dataclasses.replace(pend, previous_current="elsewhere-1"))
    )
    before = files(w)
    assert w.writer(["install"]) == 2
    assert "pending: current = " + first in capsys.readouterr().err
    assert files(w) == before
    assert leftovers(w, pend.release_id) != []


def test_damaged_under_resume_is_refused_with_a_way_out(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """R47 (I9, option (a)): the activated pending release changed on disk —
    `damaged` under resume is exit 2 naming the id, the state, the manual
    way out and `doctor`; nothing is removed."""
    interrupt(w, w.bootstrap, w.init_argv(), "launcher")
    (w.lay.release_dir(FIRST) / "injected").write_text("x\n")
    assert w.writer(["install"]) == 2
    err = capsys.readouterr().err
    for text in (FIRST, "damaged", str(w.lay.pending), "darkfactory doctor"):
        assert text in err
    assert os.path.lexists(w.lay.pending)
    assert os.path.isdir(w.lay.release_dir(FIRST))


def test_pending_text_and_digest_are_one_value(w: World) -> None:
    """M8: `read_pending` refuses a `pending.toml` whose digest or text is not
    one configuration rendered."""
    pend = install.new_pending(w.cfg(), FIRST, None)
    w.lay.install_dir.mkdir(parents=True)
    for bad in (
        {"install_digest": "f" * 64},
        {"install_text": dump_install(w.cfg()) + "# x\n"},
        {"release_id": "../x"},
    ):
        w.lay.pending.write_text(dump_pending(dataclasses.replace(pend, **bad)))
        with pytest.raises(StepFailed) as exc:
            install.read_pending(w.lay)
        assert exc.value.step == "pending"
    w.lay.pending.write_text(dump_pending(pend))
    assert install.read_pending(w.lay) == pend
    assert pend.install_digest == config_digest(w.cfg())


# ---------- security order (task-9-rulings: I6, I7, M4, M14) ----------

PYPROJECT = b'[project]\nname = "app"\nversion = "0.1.0"\n'
LOCK = b"""version = 1
revision = 3

[[package]]
name = "app"
version = "0.1.0"
source = { virtual = "." }
"""


def pin(name: str) -> RepoPin:
    return RepoPin(name, f"https://github.com/o/{name}.git", "2" * 40, ())


def real_checkouts(
    world: World, names: list[str], files_of: Callable[[str], dict[str, bytes]]
) -> None:
    """The real `fetch_references` over a profile of `names`; the root git
    fetch itself (`inputs.fetch_reference`) is a recorded stand-in that
    writes `files_of(repo)`."""
    world.mp.setattr(install, "fetch_references", REAL_REFERENCES)
    profile = Profile("p", PY, tuple(pin(n) for n in names))
    world.mp.setattr(install.manifest_mod, "parse_profile", lambda _d, _p: profile)

    def fetch(p: RepoPin, dest: Path, *, git: Path, layout: object) -> Path:
        world.calls.append(("fetch_reference", p.name))
        dest.mkdir()
        for name, data in files_of(p.name).items():
            (dest / name).write_bytes(data)
        return dest

    world.mp.setattr(inputs, "fetch_reference", fetch)


REAL_REFERENCES = install.fetch_references


def record(world: World, module: object, name: str) -> None:
    real = getattr(module, name)

    def spy(*a: object, **kw: object) -> object:
        world.calls.append((name,))
        return real(*a, **kw)

    world.mp.setattr(module, name, spy)


def test_trust_order_before_any_execution(w: World) -> None:
    """I6 (spy): manifest -> approved CLI and paths (step 2) -> account
    (step 3) -> trusted checkouts -> uv settings -> quarantine -> `drive`;
    no process runs before step 2 succeeds."""
    real_checkouts(
        w, ["devtools"], lambda _r: {"pyproject.toml": PYPROJECT, "uv.lock": LOCK}
    )
    record(w, build_mod, "verify_build_user")
    record(w, lockset, "uv_config_problems")
    record(w, plat, "retry_quarantine")
    record(w, plat, "run_as")
    record(w, plat, "run_command")

    def drive(*_a: object, **_k: object) -> None:
        w.calls.append(("drive",))
        raise StepFailed("drive", "stop here")

    w.mp.setattr(accept, "drive", drive)
    assert w.bootstrap(w.init_argv()) == 2
    names = [c[0] for c in w.calls]
    order = [
        "fetch_manifest",
        "approved_cli_problems",
        "verify_build_user",
        "fetch_reference",
        "uv_config_problems",
        "retry_quarantine",
        "drive",
    ]
    assert list(dict.fromkeys(n for n in names if n in order)) == order
    last_check = max(i for i, n in enumerate(names) if n == "approved_cli_problems")
    assert last_check < names.index("fetch_reference")
    first_exec = min(
        (i for i, n in enumerate(names) if n in ("run_as", "run_command")),
        default=len(names),
    )
    assert first_exec > names.index("approved_cli_problems")


@pytest.mark.parametrize("bad", ["cli", "opt", "staging-mode"])
def test_step2_refusal_stops_before_any_execution(
    w: World, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    """The negative twin of I6 and M3: an approved CLI or a directory that
    fails the policy is `trusted-paths` naming it; no checkout, no process,
    nothing repaired."""
    real_checkouts(
        w, ["devtools"], lambda _r: {"pyproject.toml": PYPROJECT, "uv.lock": LOCK}
    )
    record(w, plat, "run_as")
    record(w, plat, "run_command")
    if bad == "cli":
        w.mp.setattr(plat, "approved_cli_problems", lambda _c, _p: ["git: не root"])
    else:
        install._make_prep_dirs(w.lay)  # noqa: SLF001
        if bad == "opt":
            w.untrusted.add(w.lay.opt)
        else:
            w.lay.staging.chmod(0o755)
    assert w.bootstrap(w.init_argv()) == 2
    err = capsys.readouterr().err
    assert "trusted-paths" in err
    want = {
        "cli": "git: не root",
        "opt": f"{w.lay.opt}: untrusted",
        "staging-mode": f"{w.lay.staging}: права 0755, нужны 0711",
    }[bad]
    assert want in err
    assert [
        c for c in w.calls if c[0] in ("fetch_reference", "run_as", "run_command")
    ] == []
    if bad == "staging-mode":
        assert os.stat(w.lay.staging).st_mode & 0o777 == 0o755


def test_build_user_step_runs_nothing_as_df_build_before_ready(w: World) -> None:
    """M4 (spy): while `verify_build_user` has problems, `whoami_as` (a
    process of `df-build`) is never called; the step refuses after
    `create_build_user` leaves the account not ready. Twin: ready — it is."""
    lay = w.lay
    asked: list[str] = []
    w.mp.setattr(build_mod, "verify_build_user", lambda _l: ["state = creating"])
    w.mp.setattr(
        build_mod, "whoami_as", lambda u: asked.append(u) or (BUILD_UID, BUILD_UID)
    )
    w.mp.setattr(plat, "create_build_user", lambda _l: w.calls.append(("create",)))
    lay.install_dir.mkdir(parents=True)
    step = install.install_steps(lay, w.cfg(), install.Prepared())[2]
    with pytest.raises(StepFailed) as exc:
        journal.run_steps([step], lay.journal, run_id="r")
    assert exc.value.step == "build-user"
    assert asked == [] and ("create",) in w.calls
    write_ready_account(w)
    w.mp.setattr(build_mod, "verify_build_user", lambda _l: [])
    journal.run_steps([step], lay.journal, run_id="r")
    assert asked == [plat.BUILD_USER]


def test_uv_config_refused_before_build_lists_all_repos(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Task 7a / §3.2, M5: two repos — `[tool.uv] exclude-newer` in one,
    `uv.toml` in the other — one `StepFailed("build")` naming both files and
    keys; `drive` never starts, nothing runs as `df-build`."""
    bad_pyproject = PYPROJECT + b'[tool.uv]\nexclude-newer = "2024-01-01"\n'
    files_of = {
        "r1": {"pyproject.toml": bad_pyproject, "uv.lock": LOCK},
        "r2": {"pyproject.toml": PYPROJECT, "uv.toml": b"x = 1\n", "uv.lock": LOCK},
    }
    real_checkouts(w, ["r1", "r2"], files_of.__getitem__)
    drives: list[object] = []
    w.mp.setattr(accept, "drive", lambda *a, **k: drives.append(a))
    assert w.bootstrap(w.init_argv()) == 2
    err = capsys.readouterr().err
    assert "build: r1: [tool.uv] exclude-newer" in err or "r1:" in err
    assert "exclude-newer" in err and "r2: uv.toml" in err
    assert drives == []
    assert not [e for e in w.fl.events if e[0] == "run_as"]


def test_unreadable_checkout_file_is_a_problem(w: World) -> None:
    files_of = {"r1": {"uv.lock": LOCK}, "r2": {"pyproject.toml": PYPROJECT}}
    profile = Profile("p", PY, (pin("r1"), pin("r2")))
    refs = {}
    for name, content in files_of.items():
        ref = w.lay.root / name
        ref.mkdir()
        for f, data in content.items():
            (ref / f).write_bytes(data)
        refs[name] = ref
    os.symlink(w.lay.root / "r1" / "uv.lock", w.lay.root / "r1" / "pyproject.toml")
    with pytest.raises(StepFailed) as exc:
        install.uv_config_refusal(profile, refs)
    assert exc.value.step == "build"
    assert "r1:" in exc.value.detail and "r2: uv.lock: нет файла" in exc.value.detail


# ---------- the rollback ----------


def test_rollback_needs_trusted_parents(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """I7 (negative twin): an untrusted `/opt/df` — `trusted-paths` before any
    removal (the tree and `pending.toml` stay); trusted again — the rollback
    removes the tree."""
    interrupt(w, w.bootstrap, w.init_argv(), "verify")
    w.untrusted.add(w.lay.opt)
    removed: list[object] = []
    real = receive.remove_own_tree
    w.mp.setattr(receive, "remove_own_tree", lambda *a: removed.append(a) or real(*a))
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 2
    assert "trusted-paths" in capsys.readouterr().err
    assert removed == [] and os.path.isdir(w.lay.release_dir(FIRST))
    assert pending_of(w).release_id == FIRST
    w.untrusted.clear()
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert removed and leftovers(w, FIRST) == []


def test_rollback_never_runs_as_build_user_before_ready(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Carry (a), I-1 through `recover_pending`: a mixed world — a root
    `releases/<id>` and a builder `staging/<id>/tree` — with the account
    not ready. The root tree is removed first (the route's own order), then
    the cleaner refuses before any `run_as`; a retry repeats that
    idempotently; with the account ready the rollback completes."""
    lay = w.lay
    lay.install_dir.mkdir(parents=True)
    install._make_prep_dirs(lay)  # noqa: SLF001
    (lay.release_dir(FIRST) / "src").mkdir(parents=True)
    lay.staging_dir(FIRST).mkdir(mode=0o711)
    tree = w.fl.builder_dir(lay.staging_tree(FIRST))
    (tree / "f").write_text("x\n")
    lay.pending.write_text(dump_pending(install.new_pending(w.cfg(), FIRST, None)))
    journal.generation_begin(lay)
    w.mp.setattr(build_mod, "verify_build_user", lambda _l: ["state = creating"])
    for _ in range(2):
        w.fl.clear()
        assert w.bootstrap(w.init_argv(OTHER_SHA)) == 2
        assert "rollback:" in capsys.readouterr().err
        assert not [e for e in w.fl.events if e[0] == "run_as"]
        assert not os.path.lexists(lay.release_dir(FIRST))
        assert tree.is_dir() and os.path.lexists(lay.pending)
    write_ready_account(w)
    w.mp.setattr(build_mod, "verify_build_user", lambda _l: [])
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert leftovers(w, FIRST) == []


@pytest.mark.parametrize("where", ["tree", "pending"])
def test_rollback_failure_names_its_step(
    w: World, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """7-D2 / M7: a bare `TreeError` (`remove_own_tree`) or `OSError`
    (`remove_durably`) of a rollback call is `StepFailed("rollback")` naming
    the route step and the object; exit 2, journaled."""
    interrupt(w, w.bootstrap, w.init_argv(), "verify")
    if where == "tree":

        def broken(*_a: object) -> None:
            raise TreeError("not ours")

        w.mp.setattr(receive, "remove_own_tree", broken)
        want = f"rollback: cleanup[0] remove_own_tree {w.lay.release_dir(FIRST)}"
    else:

        def broken_rm(_p: Path) -> None:
            raise PermissionError(13, "denied")

        w.mp.setattr(fsutil, "remove_durably", broken_rm)
        mark = w.lay.mark_file(FIRST)
        want = f"rollback: cleanup[4] drop_mark {mark}: [Errno 13] denied"
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 2
    assert want in capsys.readouterr().err
    rows = read_journal(w.lay.journal)
    assert [r["event"] for r in rows if r["step"] == "rollback"][-1] == "failed"


# ---------- the command ----------


def test_install_committed_without_local_link(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed half of `test_usr_local_link_only_when_trusted` (Task 8a):
    `/usr/local/bin` untrusted — `link_command` is not called, `L` holds
    without the link, the install commits and prints the full path."""
    w.untrusted.add(w.lay.local_bin)
    linked: list[object] = []
    w.mp.setattr(admin, "link_command", lambda lay: linked.append(lay))
    assert w.bootstrap(w.init_argv()) == 0
    assert linked == []
    assert not os.path.lexists(w.lay.local_bin / "darkfactory")
    assert install.command_ready(w.lay, FIRST)
    assert f"команда: {w.lay.launcher}\n" in capsys.readouterr().out


def test_bootstrap_refuses_healthy_command(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """M16: no `pending.toml`, `current`, `allowed`, `intact`, `L` — exit 2
    «используйте darkfactory», nothing changes. A broken command (`L`
    false) or a `pending.toml` — bootstrap proceeds."""
    installed(w)
    before = files(w)
    assert w.bootstrap(["install"]) == 2
    assert "используйте darkfactory" in capsys.readouterr().err
    assert files(w) == before
    w.lay.launcher.write_text("#!/bin/sh\nexit 1\n")
    assert w.bootstrap(["install"]) == 0
    interrupt(w, w.writer, ["install"], "build")
    assert w.bootstrap(["install"]) == 0
    assert not os.path.lexists(w.lay.pending)


@pytest.mark.parametrize("variant", ["resume", "bootstrap"])
def test_rollback_to_other_admin_python_rewrites_launcher(
    w: World, variant: str
) -> None:
    """F16 in the M17 form (a prepared state): the command names another
    admin Python, so `L` is false for the active release. Resume (`pending`
    of the active id): the table reaches `launcher` and `L` holds. Bootstrap
    (no `pending`): the command is not healthy, bootstrap installs anew."""
    first = installed(w)
    other = admin.launcher_text(w.lay, "3.12.9", PLATFORM)
    w.lay.launcher.write_text(other)
    assert not install.command_ready(w.lay, first)
    if variant == "resume":
        pend = install.new_pending(w.cfg(), first, None)
        w.lay.pending.write_text(dump_pending(pend))
        journal.generation_begin(w.lay)
        assert w.writer(["install"]) == 0
        assert w.done(pend.run_id) == ["launcher", "commit-config"]
        assert os.readlink(w.lay.current) == f"releases/{first}"
    else:
        assert w.bootstrap(["install"]) == 0
        assert os.readlink(w.lay.current) != f"releases/{first}"
    active = os.readlink(w.lay.current).removeprefix("releases/")
    assert w.lay.launcher.read_text() != other
    assert install.command_ready(w.lay, active)


# ---------- the snapshot is not re-read (I2) ----------

#: Functions allowed to open or readlink a snapshot part from inside `main`
#: (I2): the automaton's facts and settles, the route guards and the F13
#: entry check, the release's own registry and link writers, the next id,
#: the command's registered Python, and the writer's one `read_pending`.
#: Bootstrap reads its snapshot itself (`read_raw`, after `settle_on_acquire`).
REREAD_ALLOWED = frozenset(
    {
        ("accept", "read_facts"),
        ("accept", "settle"),
        ("accept", "settle_on_acquire"),
        ("routes", "route_guard"),
        ("routes", "_refuse_registered"),
        ("release", "register"),
        ("release", "activate"),
        ("release", "next_release_id"),
        ("install", "_registered_python"),
        ("install", "read_pending"),
    }
)
BOOTSTRAP_READ = ("state", "read_raw")


@dataclass
class Reads:
    """Opens and readlinks of the snapshot parts, with the `df` frames."""

    watched: frozenset[Path]
    hits: list[tuple[Path, list[tuple[str, str]]]] = field(default_factory=list)

    def see(self, path: object, dir_fd: object) -> None:
        if dir_fd is not None or not isinstance(path, (str, os.PathLike)):
            return
        target = Path(os.fspath(path))
        if target not in self.watched:
            return
        frames = [
            (Path(f.filename).stem, f.name)
            for f in traceback.extract_stack()
            if f"{os.sep}df{os.sep}" in f.filename and "tests" not in f.filename
        ]
        self.hits.append((target, frames))


@contextlib.contextmanager
def watching(world: World) -> Iterator[Reads]:
    lay = world.lay
    reads = Reads(
        frozenset(
            {
                lay.etc / "install.toml",
                lay.etc / "releases.toml",
                lay.etc / "models.toml",
                lay.pending,
                lay.current,
            }
        )
    )
    real_open, real_readlink = os.open, os.readlink

    def open_(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: object = None
    ) -> int:
        reads.see(path, dir_fd)
        return real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]

    def readlink(path: object, *, dir_fd: object = None) -> object:
        reads.see(path, dir_fd)
        return real_readlink(path, dir_fd=dir_fd)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as m:
        m.setattr(os, "open", open_)
        m.setattr(os, "readlink", readlink)
        yield reads


def assert_no_reread(reads: Reads, *, bootstrap: bool) -> None:
    allowed = REREAD_ALLOWED | ({BOOTSTRAP_READ} if bootstrap else set())
    for path, frames in reads.hits:
        assert allowed & set(frames), (path, frames)
    pendings = [f for _p, f in reads.hits if ("install", "read_pending") in f]
    assert len(pendings) == 1


@pytest.mark.parametrize("run", ["bootstrap-init", "writer-upgrade", "writer-resume"])
def test_main_rereads_nothing(w: World, run: str) -> None:
    """I2: inside `main`, a snapshot part (`install.toml`, `releases.toml`,
    `models.toml`, `pending.toml`, `current`) is opened or read as a link
    only by the automaton reading its own changes under the lock (the
    whitelist), and `pending.toml` by `read_pending` exactly once; a reader
    reads none."""
    if run == "bootstrap-init":
        with watching(w) as reads:
            assert w.bootstrap(w.init_argv()) == 0
        assert_no_reread(reads, bootstrap=True)
        assert any(BOOTSTRAP_READ in f for _p, f in reads.hits)
        return
    installed(w)
    if run == "writer-resume":
        interrupt(w, w.writer, ["install"], "activate")
    with journal.writer_lock(w.lay) as fd:
        accept.settle_on_acquire(w.lay)
        raw = state.read_raw(w.lay)
        with watching(w) as reads:
            code = cli.main(
                ["install"],
                mode="writer",
                read=Stable(raw, raw.generation),
                lock_fd=fd,
                layout=w.lay,
            )
    assert code == 0
    assert_no_reread(reads, bootstrap=False)
    raw = state.read_raw(w.lay)
    with watching(w) as reads:
        assert w.reader(["models", "list"], Stable(raw, raw.generation)) == 2
    assert reads.hits == []


@pytest.mark.parametrize("seen", [True, False])
def test_pending_must_match_the_snapshot(
    w: World, capsys: pytest.CaptureFixture[str], seen: bool
) -> None:
    """I2: the writer's one `read_pending` must agree with the snapshot's
    `pending_present`, else `StepFailed("snapshot")` before any change."""
    installed(w)
    if not seen:
        w.lay.pending.write_text(
            dump_pending(install.new_pending(w.cfg(), "x-1", FIRST))
        )
    before = files(w)
    with journal.writer_lock(w.lay) as fd:
        raw = dataclasses.replace(state.read_raw(w.lay), pending_present=seen)
        code = cli.main(
            ["install"], mode="writer", read=Stable(raw, 2), lock_fd=fd, layout=w.lay
        )
    assert code == 2
    assert "snapshot: pending.toml не совпадает со снимком" in capsys.readouterr().err
    assert files(w) == before


@pytest.mark.parametrize("argv", [["doctor"], ["models", "set", "x"], ["models"]])
def test_bootstrap_rejects_other_subcommands(
    w: World, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """`main(mode="bootstrap")` runs only `init`/`install`, as `bootstrap.py`
    does; nothing is created."""
    assert w.bootstrap(argv) == 2
    assert "только init или install" in capsys.readouterr().err
    assert not os.path.lexists(w.lay.var)
