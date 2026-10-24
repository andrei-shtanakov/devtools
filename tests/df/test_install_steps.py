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

import argparse
import contextlib
import dataclasses
import os
import shutil
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
    Pending,
    config_digest,
    dump_install,
    dump_pending,
    load_releases,
)
from df.digest import TreeError
from df.journal import NeedsHuman, Stable, StepFailed, Transitional, read_journal
from df.manifest import Profile, RepoPin
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.install_world import (
    CLEAN_PASS,
    FIRST,
    MODELS_SET,
    OTHER_SHA,
    PLATFORM,
    SECOND,
    SHA,
    World,
    files,
    installed,
    interrupt,
    make_world,
    write_ready_account,
)
from tests.df.route_world import (
    BOOT,
    PY,
)


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def w(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    """See `World`; the stand-ins are recorded in `calls`."""
    return make_world(fake_layout, monkeypatch)


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
    set`); without `pending.toml` the same call reaches the dispatch and
    succeeds instead."""
    installed(w)
    assert w.writer(MODELS_SET) == 0
    interrupt(w, w.writer, ["install"], "build")
    before = files(w)
    assert w.writer(MODELS_SET) == 2
    assert "завершите darkfactory install" in capsys.readouterr().err
    assert files(w) == before


def test_reader_mode_refuses_writer_subcommand(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """(c): `mode_of` decides: a writer subcommand in reader mode (the entry
    took no lock) is exit 2 and reaches nothing."""
    installed(w)
    raw = state.read_raw(w.lay)
    runs: list[object] = []
    w.mp.setattr(install, "run_install", lambda *a, **kw: runs.append(a))
    for argv in (["install"], w.init_argv(), MODELS_SET):
        assert w.reader(argv, Stable(raw, raw.generation)) == 2
        assert "без блокировки" in capsys.readouterr().err
    assert runs == []


def test_reader_transitional(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """A reader in a transitional state: `doctor` reaches its (Task 12) stub,
    the rest refuse naming the interrupted operation."""
    assert w.reader(["models", "list"], Transitional(3)) == 2
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


def test_parser_error_never_exits() -> None:
    """N1 on Python 3.12, where `exit_on_error=False` still routes required
    and unrecognized arguments to `error()`: it raises, never `sys.exit`."""
    with pytest.raises(cli.UsageError, match="boom"):
        cli._Parser(prog="x").error("boom")  # noqa: SLF001


@pytest.mark.parametrize(
    "cli_args,why",
    [
        (["git=/usr/bin/git", "git=/bin/git"], "нужно NAME=PATH, каждое имя один раз"),
        (["git"], "нужно NAME=PATH"),
        (["=x"], "нужно NAME=PATH"),
        (["git=/g", "tar=/t", "uv=relative"], "uv: approved_cli path must be absolute"),
        (["git=/g"], "approved_cli: missing required: tar, uv"),
    ],
)
def test_init_cli_pairs_refused(
    w: World, capsys: pytest.CaptureFixture[str], cli_args: list[str], why: str
) -> None:
    """A bad `--cli` is refused, naming why, before the lock creates anything."""
    argv = [*w.init_argv()[:7], "--cli", *cli_args]
    assert w.bootstrap(argv) == 2
    assert why in capsys.readouterr().err
    assert not os.path.lexists(w.lay.var)


def test_exit_codes(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """M7: `NeedsHuman` is 3 with the instruction; a program error is 1 with
    its traceback; a bare `TreeError`/`OSError` is exit 2 named by its type
    (m3: it has no step of its own)."""
    cases: list[tuple[BaseException, int, str]] = [
        (NeedsHuman("создайте аккаунт"), 3, "создайте аккаунт"),
        (AssertionError("bug"), 1, "Traceback"),
        (TreeError("tree"), 2, "darkfactory: TreeError: tree"),
        (OSError(5, "io"), 2, "darkfactory: OSError: [Errno 5] io"),
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
    err = capsys.readouterr().err
    assert "pending: current = " + first in err
    assert pend.release_id in err and "elsewhere-1" in err and "doctor" not in err
    assert files(w) == before
    assert leftovers(w, pend.release_id) != []


def test_damaged_under_resume_is_refused_with_a_way_out(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """R51/R52 (I9, option (a), G1): the activated pending release changed on
    disk — `damaged` under resume is exit 2 naming the id, the state and
    the manual way out (a first install before `commit-config`: `unlink`),
    never `doctor`; nothing is removed."""
    interrupt(w, w.bootstrap, w.init_argv(), "launcher")
    (w.lay.release_dir(FIRST) / "injected").write_text("x\n")
    assert w.writer(["install"]) == 2
    err = capsys.readouterr().err
    for text in (
        f"damaged: релиз {FIRST}: повреждён",
        "уже переключила на него current",
        f"удалите {w.lay.pending}",
        f"unlink {w.lay.current}",
    ):
        assert text in err
    assert "doctor" not in err
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
    (step 3) -> trusted checkouts -> uv settings -> quarantine -> `drive`.
    That no process runs before step 2 is the positive control
    `test_commands_run_as_df_build_only_after_step2` (m6)."""
    real_checkouts(
        w, ["devtools"], lambda _r: {"pyproject.toml": PYPROJECT, "uv.lock": LOCK}
    )
    record(w, build_mod, "verify_build_user")
    record(w, lockset, "uv_config_problems")
    record(w, plat, "retry_quarantine")

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
    assert "build: r1: pyproject.toml [tool.uv] exclude-newer" in err, err
    assert "r2: uv.toml: настройки uv в репо не поддерживаются" in err
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
#: the command's registered Python, the writer's one `read_pending`, and the
#: writer's own atomic writes and removals of those parts.
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
        # I9: G2 reads the pending release's registry entry as `drive` would;
        # G4 reads `current` and the registry after the run's own changes
        ("install", "_refuse_damaged_pending"),
        ("install", "_backstop"),
        # writes of the writer's own parts (they stat their target), not reads
        ("fsutil", "write_atomic"),
        ("fsutil", "remove_durably"),
    }
)
BOOTSTRAP_READ = ("state", "read_raw")


@dataclass
class Reads:
    """Opens and readlinks of the snapshot parts, with the `df` frames."""

    watched: frozenset[Path]
    hits: list[tuple[Path, list[tuple[str, str]]]] = field(default_factory=list)
    opens: list[list[tuple[str, str]]] = field(default_factory=list)

    def see(self, path: object, dir_fd: object, op: str = "stat") -> None:
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
        if op == "open":
            self.opens.append(frames)


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
    real_stat, real_lstat = os.stat, os.lstat

    def stat_(
        path: object, *, dir_fd: object = None, follow_symlinks: bool = True
    ) -> object:
        reads.see(path, dir_fd)
        stat_of: Callable[..., object] = real_stat
        return stat_of(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def lstat(path: object, *, dir_fd: object = None) -> object:
        reads.see(path, dir_fd)
        return real_lstat(path, dir_fd=dir_fd)  # type: ignore[arg-type]

    def open_(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: object = None
    ) -> int:
        reads.see(path, dir_fd, "open")
        return real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]

    def readlink(path: object, *, dir_fd: object = None) -> object:
        reads.see(path, dir_fd)
        return real_readlink(path, dir_fd=dir_fd)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as m:
        m.setattr(os, "open", open_)
        m.setattr(os, "readlink", readlink)
        m.setattr(os, "stat", stat_)
        m.setattr(os, "lstat", lstat)
        yield reads


def assert_no_reread(reads: Reads, *, bootstrap: bool) -> None:
    allowed = REREAD_ALLOWED | ({BOOTSTRAP_READ} if bootstrap else set())
    for path, frames in reads.hits:
        assert allowed & set(frames), (path, frames)
    pendings = [f for f in reads.opens if ("install", "read_pending") in f]
    assert len(pendings) == 1


@pytest.mark.parametrize("run", ["bootstrap-init", "writer-upgrade", "writer-resume"])
def test_main_rereads_nothing(w: World, run: str) -> None:
    """I2: inside `main`, a snapshot part (`install.toml`, `releases.toml`,
    `models.toml`, `pending.toml`, `current`) is opened, stat'ed or read as a link
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
        assert w.reader(["models", "list"], Stable(raw, raw.generation)) == 0
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


@pytest.mark.parametrize("argv", [["doctor"], MODELS_SET, ["models", "list"]])
def test_bootstrap_rejects_other_subcommands(
    w: World, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """`main(mode="bootstrap")` runs only `init`/`install`, as `bootstrap.py`
    does; nothing is created."""
    assert w.bootstrap(argv) == 2
    assert "только init или install" in capsys.readouterr().err
    assert not os.path.lexists(w.lay.var)


# ---------- fix round 1 ----------


def test_rollback_judges_an_absent_parent_by_its_ancestor(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """I-1 (negative twin of I7): `/opt/df` absent under an untrusted `<root>/opt`
    — the rollback refuses `trusted-paths` naming `<root>/opt` before any
    removal: no `rollback start`, `pending.toml` kept."""
    lay = w.lay
    lay.install_dir.mkdir(parents=True)
    lay.pending.write_text(dump_pending(install.new_pending(w.cfg(), FIRST, None)))
    journal.generation_begin(lay)
    lay.opt.parent.mkdir()
    w.untrusted.add(lay.opt.parent)
    removed: list[object] = []
    w.mp.setattr(receive, "remove_own_tree", lambda *a: removed.append(a))
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 2
    assert f"trusted-paths: {lay.opt.parent}: untrusted" in capsys.readouterr().err
    rows = read_journal(lay.journal)
    assert not [r for r in rows if r["step"] == "rollback"]
    assert removed == [] and pending_of(w).release_id == FIRST


@pytest.mark.parametrize("side", ["opt", "etc"])
def test_step2_judges_an_absent_dir_by_its_ancestor(
    w: World, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    """I-3: a fresh host with `<root>/opt` (or `<root>/etc`) untrusted and
    `/opt/df` (`/etc/df`) absent stops at `trusted-paths` naming it; nothing
    is created under it and no command runs."""
    real_checkouts(
        w, ["devtools"], lambda _r: {"pyproject.toml": PYPROJECT, "uv.lock": LOCK}
    )
    record(w, plat, "run_as")
    record(w, plat, "run_command")
    ancestor = w.lay.root / side
    ancestor.mkdir()
    w.untrusted.add(ancestor)
    assert w.bootstrap(w.init_argv()) == 2
    assert f"trusted-paths: {ancestor}: untrusted" in capsys.readouterr().err
    assert os.listdir(ancestor) == []
    assert [
        c for c in w.calls if c[0] in ("fetch_reference", "run_as", "run_command")
    ] == []


def test_step2_refuses_a_linked_dir(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """m1: an existing step-2 path that is a link (to a trusted root
    directory of the right mode) is refused «не каталог»."""
    install._make_prep_dirs(w.lay)  # noqa: SLF001
    elsewhere = w.lay.root / "elsewhere"
    elsewhere.mkdir(mode=0o711)
    elsewhere.chmod(0o711)
    shutil.rmtree(w.lay.staging)
    os.symlink(elsewhere, w.lay.staging)
    assert w.bootstrap(w.init_argv()) == 2
    assert f"trusted-paths: {w.lay.staging}: не каталог" in capsys.readouterr().err


def test_bootstrap_proceeds_over_a_revoked_release(w: World) -> None:
    """m2 (M16 conjunct `allowed`): the active entry revoked, files intact —
    bootstrap `install` is not refused and installs a new id."""
    first = installed(w)
    path = w.lay.etc / "releases.toml"
    path.write_text(
        path.read_text().replace(
            'status = "allowed"', 'status = "revoked"\nreason = "x"'
        )
    )
    assert w.bootstrap(["install"]) == 0
    assert os.readlink(w.lay.current) != f"releases/{first}"


def test_commands_run_as_df_build_only_after_step2(w: World) -> None:
    """m6, positive control of I6: a real install runs processes (`run_as`),
    and the first one comes after the last approved-CLI check."""
    record(w, plat, "run_as")
    assert w.bootstrap(w.init_argv()) == 0
    names = [c[0] for c in w.calls]
    assert "run_as" in names
    last_check = max(i for i, n in enumerate(names) if n == "approved_cli_problems")
    assert names.index("run_as") > last_check


def test_damaged_first_install_names_pending_then_current(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without `previous_current` the way out deletes `pending.toml` first,
    then unlinks `current` (never the other way: M10); no «return
    current»."""
    interrupt(w, w.bootstrap, w.init_argv(), "launcher")
    (w.lay.release_dir(FIRST) / "injected").write_text("x\n")
    assert w.writer(["install"]) == 2
    err = capsys.readouterr().err
    assert "прежний релиз" not in err and "верните" not in err
    pending_at = err.index(f"удалите {w.lay.pending}")
    assert pending_at < err.index(f"unlink {w.lay.current}")


#: I-2: every way the parsed command and `mode_of(argv)` could disagree.
ARGV_FORMS = {
    "dashdash-before-init": [
        "--",
        *"init --manifest-repo r --manifest-sha s --profile p --cli git=/g".split(),
    ],
    "dashdash-before-install": ["--", "install"],
    "dashdash-after-install": ["install", "--"],
    "dashdash-before-models": ["--", "models", "set", "x"],
    "dashdash-inside-models": ["models", "--", "set", "x"],
    "option-before": ["--json", "install"],
    "abbreviated-option": (
        "init --manifest-r r --manifest-sha s --profile p --cli git=/g".split()
    ),
    "prefix-subcommand": ["inst"],
    "repeated": ["install", "install"],
    "empty": [],
    "models-set": MODELS_SET,
}


@pytest.mark.parametrize("mode", ["reader", "writer", "bootstrap"])
@pytest.mark.parametrize("form", sorted(ARGV_FORMS))
def test_argv_forms_never_split_mode_and_dispatch(
    w: World, capsys: pytest.CaptureFixture[str], mode: str, form: str
) -> None:
    """I-2: each form in each mode is exit 2 before the euid check and any
    dispatch — except `models set`, which is a legitimate writer subcommand
    (reader: refused as a writer; writer: refused for root; bootstrap:
    refused as not init/install). Not root: a form that slipped past the
    mode check would be refused for root instead, so the message tells."""
    w.mp.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    argv = ARGV_FORMS[form]
    raw = state.RawSnapshot(0, None, None, None, None, False)
    read = None if mode == "bootstrap" else Stable(raw, 0)
    runs: list[object] = []
    w.mp.setattr(install, "run_install", lambda *a, **kw: runs.append(a))
    code = cli.main(
        argv,
        mode=mode,  # type: ignore[arg-type]
        read=read,
        lock_fd=None if mode != "writer" else 99,
        layout=w.lay,
    )
    err = capsys.readouterr().err
    assert code == 2, err
    assert "Traceback" not in err
    # only the legitimate writer form in writer mode reaches the euid check
    legit = (form, mode) == ("models-set", "writer")
    assert ("требует root" in err) == legit
    if "--" in argv and form != "dashdash-after-install":
        # the second layer: `--` itself never reaches argparse or a dispatch
        # (argparse would read `models -- set …` as `models set …`)
        assert "форма команды" in err, err
    assert runs == [] and not os.path.lexists(w.lay.var)


def test_later_never_indexes_an_unknown_command() -> None:
    with pytest.raises(cli.Refusal):
        cli._later("install")  # noqa: SLF001


@pytest.mark.parametrize(
    "argv,command,models_command",
    [
        (["x", "install"], "install", None),
        (["install"], "models", "list"),
        (["models", "set"], "models", "list"),
        (["models", "list"], "init", None),
        (["models", "list"], "models", "set"),
    ],
)
def test_check_mode_refuses_any_disagreement(
    argv: list[str], command: str, models_command: str | None
) -> None:
    """I-2, the recompute alone (no `--`): the parsed command's mode differs
    from `mode_of(argv)` — refused in every mode, whatever argparse made."""
    ns = argparse.Namespace(command=command)
    if models_command is not None:
        ns.models_command = models_command
    for mode in ("reader", "writer", "bootstrap"):
        with pytest.raises(cli.UsageError, match="форма команды"):
            cli._check_mode(argv, mode, ns)  # type: ignore[arg-type]  # noqa: SLF001
