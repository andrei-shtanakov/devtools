"""`launch exec` and `darkfactory launch` (stage 2a, Task 5)."""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path

import pytest

from df import cli, launch, launcher_entry, state
from df.config import Account, dump_accounts
from df.journal import Stable, StepFailed, Transitional
from df.state import Snapshot
from tests.df.conftest import FakeLayout
from tests.df.launch_world import (
    entry,
    fake_os,
    put_accounts,
    put_record,
    put_registry,
    record,
    snapshot,
    stdin_of,
    trust_paths,
)

A, B = entry("rel-a"), entry("rel-b")
HOSTILE = {
    "SSH_AUTH_SOCK": "/tmp/agent",
    "GH_TOKEN": "t",
    "GITHUB_TOKEN": "t",
    "DYLD_INSERT_LIBRARIES": "/x.dylib",
    "LD_PRELOAD": "/x.so",
    "PYTHONPATH": "/evil",
    "HOME": "/Users/human",
    "PATH": "/evil/bin",
}


@pytest.fixture()
def world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> FakeLayout:
    fake_layout.make_dirs()
    fake_layout.layout.runs_dir.mkdir(parents=True)
    put_accounts(fake_layout)
    trust_paths(monkeypatch)
    monkeypatch.setattr(os, "environ", dict(HOSTILE))
    return fake_layout


def execve_of(ev) -> tuple[str, list[str], dict[str, str]]:  # noqa: ANN001
    (found,) = [e for e in ev.log if e[0] == "execve"]
    return found[1], found[2], found[3]


def test_role_env_ignores_caller(world: FakeLayout) -> None:
    env = launch.role_env(world.layout, "df-exec")
    assert tuple(env) == launch.ENV_ALLOW
    assert env["HOME"] == str(world.layout.role_home("df-exec"))
    assert (env["PATH"], env["LANG"], env["TZ"]) == ("/usr/bin:/bin", "C.UTF-8", "UTC")
    assert not set(HOSTILE) - set(launch.ENV_ALLOW) & set(env)
    assert launch.role_cwd(world.layout, "df-review") == (
        world.layout.role_home("df-review") / "work"
    )


def test_exec_main_scrubs_the_environment(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    snap = snapshot("rel-a", A)
    launch.exec_main(["df-exec", "check"], layout=world.layout, snap=snap)
    _, _, env = execve_of(ev)
    assert env == launch.role_env(world.layout, "df-exec")
    assert os.environ == env  # the process's own environment is scrubbed too


def test_exec_main_order(world: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    ev = fake_os(monkeypatch, stdin=b"run-1\n")
    put_record(world, record("run-1", "rel-a"))
    snap = snapshot("rel-b", A, B)
    launch.exec_main(["df-exec", "run"], layout=world.layout, snap=snap)
    names = ev.names()
    assert names[0] == "closerange"
    assert names.index("closerange") < names.index("identity")
    assert names.index("identity") < names.index("chdir")
    assert names.index("chdir") < names.index("stdin") < names.index("execve")
    assert names[-1] == "execve"
    assert ev.log[0][:2] == ("closerange", 3)
    # the scrub is done before stdin and the release choice (m2)
    assert ev.environ_at_stdin == launch.role_env(world.layout, "df-exec")
    assert ("chdir", launch.role_cwd(world.layout, "df-exec")) in ev.log


@pytest.mark.parametrize("field", ["uid", "euid", "gid", "egid"])
def test_exec_main_refuses_uid_mismatch(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    real = getattr(os, f"get{field}")()
    monkeypatch.setattr(os, f"get{field}", lambda: real + 1)
    ev = fake_os(monkeypatch)
    with pytest.raises(StepFailed, match="accounts.toml"):
        launch.exec_main(
            ["df-exec", "check"], layout=world.layout, snap=snapshot("rel-a", A)
        )
    assert ev.names()[0] == "closerange"
    assert "chdir" not in ev.names() and "execve" not in ev.names()


@pytest.mark.parametrize("what", ["other-uid", "creating", "missing"])
def test_exec_main_refuses_a_bad_account(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    ev = fake_os(monkeypatch)
    if what == "other-uid":
        put_accounts(world, uid=os.getuid() + 1)
    elif what == "creating":
        put_accounts(world, state="creating")
    else:
        row = Account("df-review", os.getuid(), os.getgid(), "ready", "t")
        world.layout.accounts.write_text(dump_accounts({"df-review": row}))
    with pytest.raises(StepFailed):
        launch.exec_main(
            ["df-exec", "check"], layout=world.layout, snap=snapshot("rel-a", A)
        )
    assert "execve" not in ev.names()


def test_release_for_check_uses_the_snapshot(world: FakeLayout) -> None:
    snap = snapshot("rel-b", A, B)
    for op in ("check", "check-github"):
        got = launch.release_for(world.layout, op, snap, stdin_of(b""))
        assert got == ("rel-b", world.layout.release_dir("rel-b"), None)


def test_release_for_check_without_current_or_revoked(world: FakeLayout) -> None:
    with pytest.raises(StepFailed, match="release_unknown"):
        launch.release_for(world.layout, "check", snapshot(None, A), stdin_of(b""))
    revoked = entry("rel-a", "revoked")
    with pytest.raises(StepFailed, match="release_revoked"):
        launch.release_for(
            world.layout, "check", snapshot("rel-a", revoked), stdin_of(b"")
        )


def test_release_for_run_uses_record_not_current(world: FakeLayout) -> None:
    put_record(world, record("run-1", "rel-a"))
    snap = snapshot("rel-b", A, B)
    got = launch.release_for(world.layout, "run", snap, stdin_of(b"run-1\n"))
    assert got == ("rel-a", world.layout.release_dir("rel-a"), "run-1")
    got = launch.release_for(world.layout, "review", snap, stdin_of(b"run-1"))
    assert got[0] == "rel-a"


def test_release_for_run_revoked(world: FakeLayout) -> None:
    put_record(world, record("run-1", "rel-a"))
    snap = snapshot("rel-b", entry("rel-a", "revoked"), B)
    with pytest.raises(StepFailed, match="release_revoked"):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-1"))


def test_release_for_run_unknown_and_digest(world: FakeLayout) -> None:
    put_record(world, record("run-1", "rel-a"))
    put_record(world, record("run-2", "rel-a", "sha256:" + "e" * 64))
    snap = snapshot("rel-b", A, B)
    with pytest.raises(StepFailed, match="release_unknown"):
        launch.release_for(
            world.layout, "run", snapshot("rel-b", B), stdin_of(b"run-1")
        )
    with pytest.raises(StepFailed, match="release_digest_mismatch"):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-2"))
    no_registry = Snapshot("rel-b", None, None, None, False, 1, None)
    with pytest.raises(StepFailed, match="release_unknown"):
        launch.release_for(world.layout, "run", no_registry, stdin_of(b"run-1"))


@pytest.mark.parametrize(
    "stdin",
    [b"", b"\n", b"a/b", b"../x", b"a b", b"x" * 257, "яр".encode(), b"run\x001"],
)
def test_release_for_refuses_a_bad_run_id(world: FakeLayout, stdin: bytes) -> None:
    snap = snapshot("rel-a", A)
    with pytest.raises(StepFailed, match="run_id"):
        launch.release_for(world.layout, "run", snap, stdin_of(stdin))


def test_release_for_reads_at_most_257_bytes(world: FakeLayout) -> None:
    asked: list[int] = []

    def endless(n: int) -> bytes:
        asked.append(n)
        return b"x" * n

    with pytest.raises(StepFailed, match="run_id"):
        launch.release_for(world.layout, "run", snapshot("rel-a", A), endless)
    assert sum(asked) <= 257


def test_release_for_reads_a_split_run_id(world: FakeLayout) -> None:
    put_record(world, record("run-1", "rel-a"))
    parts = [b"ru", b"n-1\n", b""]
    got = launch.release_for(
        world.layout, "run", snapshot("rel-a", A), lambda _n: parts.pop(0)
    )
    assert got[2] == "run-1"


def test_record_must_be_roots_plain_file(world: FakeLayout) -> None:
    snap = snapshot("rel-a", A)
    path = put_record(world, record("run-1", "rel-a"))
    world.set_owner(path, os.getuid() + 7)
    with pytest.raises(StepFailed, match="не root"):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-1"))
    path = put_record(world, record("run-2", "rel-a"), mode=0o664)
    with pytest.raises(StepFailed, match="запись для группы"):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-2"))
    path = put_record(world, record("run-4", "rel-a"), mode=0o646)
    with pytest.raises(StepFailed, match="запись для группы"):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-4"))
    path = world.layout.runs_dir / "run-3.json"
    path.symlink_to(world.layout.runs_dir / "run-1.json")
    with pytest.raises(StepFailed):
        launch.release_for(world.layout, "run", snap, stdin_of(b"run-3"))
    with pytest.raises(StepFailed):
        launch.release_for(world.layout, "run", snap, stdin_of(b"nope"))


def test_record_of_another_run_id_is_refused(world: FakeLayout) -> None:
    put_record(world, record("run-1", "rel-a"))
    (world.layout.runs_dir / "run-9.json").write_text(
        (world.layout.runs_dir / "run-1.json").read_text()
    )
    with pytest.raises(StepFailed, match="run-1"):
        launch.release_for(
            world.layout, "run", snapshot("rel-a", A), stdin_of(b"run-9")
        )


def test_execve_paths_match_stage1_layout(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch, stdin=b"run-1")
    put_record(world, record("run-1", "rel-a"))
    launch.exec_main(
        ["df-exec", "run"], layout=world.layout, snap=snapshot("rel-b", A, B)
    )
    program, args, _env = execve_of(ev)
    release = world.layout.release_dir("rel-a")
    # `df.build`: `UV_PROJECT_ENVIRONMENT = <release>/venv/<repo>`, sources in
    # `<release>/src/<repo>`; this repo is `devtools`
    python = release / "venv" / "devtools" / "bin" / "python"
    script = release / "src" / "devtools" / "df" / "launch_release.py"
    assert program == str(python)
    assert args == [
        str(python),
        "-I",
        "-B",
        str(script),
        "df-exec",
        "run",
        "rel-a",
        "run-1",
    ]


def test_launch_script_is_this_repos_module() -> None:
    from df import launch_release

    root = Path(launch.__file__).parents[1]
    assert Path(launch_release.__file__).relative_to(root) == Path(
        *launch.LAUNCH_SCRIPT
    )
    assert launch.RELEASE_REPO == root.name or launch.RELEASE_REPO == "devtools"


def test_execve_for_check_passes_a_dash(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    launch.exec_main(
        ["df-exec", "check"], layout=world.layout, snap=snapshot("rel-a", A)
    )
    assert execve_of(ev)[1][-3:] == ["check", "rel-a", "-"]
    assert "stdin" not in ev.names()


def test_human_main_chdir_before_sudo(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    launch.human_main(["df-review", "check"], layout=world.layout)
    assert ev.log == [
        ("chdir", Path("/")),
        ("closerange", 3, ev.log[1][2]),
        (
            "execv",
            "/usr/bin/sudo",
            [
                "sudo", "-n", "-u", "df-review",
                str(world.layout.libexec / "df-launch"), "df-review", "check",
            ],
        ),
    ]  # fmt: skip


@pytest.mark.parametrize(
    "argv",
    [
        ["df-publisher", "check"],
        ["root", "check"],
        ["df-exec"],
        ["df-exec", "review"],
        ["df-review", "run"],
        ["df-review", "check-github"],
        ["df-exec", "check", "x"],
    ],
)
def test_wrong_role_or_operation_is_refused(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    ev = fake_os(monkeypatch)
    with pytest.raises(StepFailed):
        launch.human_main(argv, layout=world.layout)
    with pytest.raises(StepFailed):
        launch.exec_main(argv, layout=world.layout, snap=snapshot("rel-a", A))
    assert "execve" not in ev.names() and "execv" not in ev.names()


def test_check_github_only_df_exec(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    launch.exec_main(
        ["df-exec", "check-github"], layout=world.layout, snap=snapshot("rel-a", A)
    )
    assert "execve" in ev.names()
    with pytest.raises(StepFailed, match="не допускается"):
        launch.exec_main(
            ["df-review", "check-github"],
            layout=world.layout,
            snap=snapshot("rel-a", A),
        )


def test_closerange_bound_is_the_hard_limit(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    import resource

    ev = fake_os(monkeypatch)
    launch.exec_main(
        ["df-exec", "check"], layout=world.layout, snap=snapshot("rel-a", A)
    )
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    hi = ev.log[0][2]
    assert hi >= soft
    if hard != resource.RLIM_INFINITY:
        assert hi == hard
    monkeypatch.setattr(
        resource, "getrlimit", lambda _r: (1024, resource.RLIM_INFINITY)
    )
    assert launch._fd_limit() == launch._FD_CAP  # noqa: SLF001
    monkeypatch.setattr(resource, "getrlimit", lambda _r: (1024, 1 << 20))
    assert launch._fd_limit() == 1 << 20  # noqa: SLF001


def test_untrusted_ancestors_refuse_the_record(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_record(world, record("run-1", "rel-a"))
    monkeypatch.setattr(
        launch.plat, "trusted_path_problems", lambda p: [f"{p}: group-writable"]
    )
    with pytest.raises(StepFailed, match="group-writable"):
        launch.release_for(
            world.layout, "run", snapshot("rel-a", A), stdin_of(b"run-1")
        )


def test_hard_linked_record_is_refused(world: FakeLayout) -> None:
    path = put_record(world, record("run-1", "rel-a"))
    os.link(path, world.layout.runs_dir / "twin.json")
    with pytest.raises(StepFailed, match="жёстких ссылок"):
        launch.release_for(
            world.layout, "run", snapshot("rel-a", A), stdin_of(b"run-1")
        )


def test_roles_can_read_what_launch_exec_reads(world: FakeLayout) -> None:
    """`launch exec` runs as the role and reads `accounts.toml` under
    `var/lib/df/install`: the stage-1 writer lock keeps that directory and
    its ancestors 0755 and the accounts file is written 0644 (roles READ it)."""
    import inspect
    import stat

    from df import fsutil, journal
    from df import platform as plat

    with journal.writer_lock(world.layout):
        for d in (world.layout.var, world.layout.install_dir):
            assert stat.S_IMODE(d.stat().st_mode) == 0o755, d
    default = inspect.signature(fsutil.write_atomic).parameters["mode"].default
    assert default == 0o644
    writes = [
        ln
        for ln in inspect.getsource(plat).splitlines()
        if "write_atomic(layout.accounts" in ln
    ]
    assert writes and not any("mode" in ln for ln in writes)


def test_cli_launch_op_must_fit_the_role() -> None:
    for argv in (
        ["launch", "df-exec", "review"],
        ["launch", "exec", "df-review", "run"],
        ["launch", "df-exec", "frobnicate"],
    ):
        with pytest.raises(cli.UsageError):
            cli._parse(argv)  # noqa: SLF001
    assert cli._parse(["launch", "df-review", "check"]).operation == "check"  # noqa: SLF001


# --- the snapshot is the only source ------------------------------------------

FORBIDDEN = ("releases.toml", "install.toml", "models.toml", "current")


class Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hits: list[str] = []
        # Tasks 6 and 7: add `publisher serve` and `run start|resume` here
        for name in ("open", "readlink", "stat", "lstat", "listdir", "scandir"):
            real = getattr(os, name)
            monkeypatch.setattr(os, name, self._wrap(real))
        for owner in (builtins, io):
            monkeypatch.setattr(owner, "open", self._wrap(owner.open))

    def _wrap(self, real):  # noqa: ANN001, ANN202
        def seen(path, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
            if isinstance(path, (str, os.PathLike)) and Path(path).name in FORBIDDEN:
                self.hits.append(str(path))
            return real(path, *a, **kw)

        return seen


def test_spy_sees_builtin_open(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy(monkeypatch)
    world.layout.etc.mkdir(parents=True, exist_ok=True)
    (world.layout.etc / "models.toml").write_text("x")
    (world.layout.etc / "models.toml").read_text()
    open(world.layout.etc / "install.toml", "a").close()
    assert len(spy.hits) >= 2


def test_service_commands_reread_nothing(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the snapshot, `releases.toml`, `install.toml`, `models.toml` and
    `current` are not read: `current = B`, the run on `allowed` A runs A; a
    file edited after the snapshot mixes nothing in."""
    ev = fake_os(monkeypatch, stdin=b"run-1")
    put_record(world, record("run-1", "rel-a"))
    snap = snapshot("rel-b", A, B)
    put_registry(world, entry("rel-a", "revoked"), B)  # edited after the snapshot
    (world.layout.etc / "install.toml").write_text("garbage")
    (world.layout.etc / "models.toml").write_text("garbage")
    spy = Spy(monkeypatch)
    launch.exec_main(["df-exec", "run"], layout=world.layout, snap=snap)
    assert execve_of(ev)[1][-2:] == ["rel-a", "run-1"]
    revoked = snapshot("rel-b", entry("rel-a", "revoked"), B)
    ev.log.clear()
    monkeypatch.setattr(launch, "_stdin", stdin_of(b"run-1"))
    with pytest.raises(StepFailed, match="release_revoked"):
        launch.exec_main(["df-exec", "run"], layout=world.layout, snap=revoked)
    launch.exec_main(["df-exec", "check"], layout=world.layout, snap=snap)
    assert spy.hits == []


# --- through cli.main ---------------------------------------------------------


def raw_of(world: FakeLayout, current: str | None) -> state.RawSnapshot:
    from df.config import ReleasesConfig, dump_releases

    text = dump_releases(ReleasesConfig((A,))).encode()
    return state.RawSnapshot(1, current, text, None, None, False)


def reader(argv: list[str], world: FakeLayout, read) -> int:  # noqa: ANN001
    return cli.main(argv, mode="reader", read=read, lock_fd=None, layout=world.layout)


def test_cli_launch_exec_reaches_execve(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    read = Stable(raw_of(world, "rel-a"), 1)
    code = reader(["launch", "exec", "df-exec", "check"], world, read)
    assert code == 1  # exec_main returns only when a fake execve does
    assert execve_of(ev)[1][-3:] == ["check", "rel-a", "-"]


def test_cli_launch_exec_refusals_are_exit_2(
    world: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_os(monkeypatch)
    read = Stable(raw_of(world, "rel-z"), 1)
    assert reader(["launch", "exec", "df-exec", "check"], world, read) == 2
    assert "release_unknown" in capsys.readouterr().err
    assert reader(["launch", "exec", "df-exec", "check"], world, Transitional(1)) == 2


def test_cli_launch_human_form(
    world: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = fake_os(monkeypatch)
    # the human form needs no snapshot: even a transitional one is fine
    reader(["launch", "df-exec", "check"], world, Transitional(1))
    assert ev.names() == ["chdir", "closerange", "execv"]
    assert launcher_entry.mode_of(["launch", "df-exec", "check"]) == "reader"


@pytest.mark.parametrize(
    ("argv", "way_in"),
    [
        (["df-exec", "run"], "darkfactory run start|resume"),
        (["df-review", "review"], "df-review-run"),
    ],
)
def test_human_entry_refuses_run_and_review(
    world: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    way_in: str,
) -> None:
    ev = fake_os(monkeypatch)
    with pytest.raises(StepFailed, match="darkfactory run start"):
        launch.human_main(argv, layout=world.layout)
    assert ev.log == []
    # `launch exec` still takes them (the wrappers call it after the drop)
    code = cli.main(
        ["launch", *argv],
        mode="reader",
        read=Stable(raw_of(world, "rel-a"), 1),
        lock_fd=None,
        layout=world.layout,
    )
    assert code == 2 and way_in in capsys.readouterr().err
    assert ev.log == []
    assert cli._parse(["launch", "exec", *argv]).operation == argv[1]  # noqa: SLF001


def test_human_ops_are_the_operator_rules_of_sudoers(world: FakeLayout) -> None:
    from df import sudoers

    grants = sudoers.expected_grants(world.layout, "alice")["alice"]
    found: dict[str, set[str]] = {}
    for grant in grants:
        command, role, op = grant.command.rsplit(" ", 2)
        assert command == str(world.layout.libexec / "df-launch")
        assert grant.runas == role
        found.setdefault(role, set()).add(op)
    assert found == {r: set(ops) for r, ops in launch.HUMAN_OPS.items()}
    assert all(ops <= launch.ROLE_OPS[r] for r, ops in launch.HUMAN_OPS.items())
