"""The README's first-install command is the command `df.cli` accepts (Task 14).

The argv is read from the README section, not copied here: the test fails when
the documented command drifts from the `init` parser or from `InstallConfig`.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import admin_tree, cli, launcher_entry, roles_setup, state
from df.config import REQUIRED_CLI, ConfigError, InstallConfig
from df.journal import Stable
from df.paths import Layout

README = Path(__file__).resolve().parents[2] / "README.md"
SECTION = "## Установка DarkFactory: этап 1"
#: The one placeholder of the documented argv: the human's manifest commit.
SHA_PLACEHOLDER = "<sha-манифеста>"
SHA = "0" * 40
#: The interpreter of the documented command: the human's Python >= 3.12.
PYTHON_PLACEHOLDER = "<python-3.12+>"


def documented_command() -> list[str]:
    """The tokens of the README's `bootstrap.py init` block (continuations joined)."""
    text = README.read_text(encoding="utf-8")
    section = text.split(SECTION, 1)[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```sh\n(.*?)```", section, re.DOTALL)
    command = next(b for b in blocks if "df/bootstrap.py init" in b)
    return shlex.split(command.replace("\\\n", " "))


def documented_argv() -> list[str]:
    """The argv `bootstrap.py` hands to `df.cli`, the placeholder filled."""
    tokens = documented_command()
    at = next(i for i, t in enumerate(tokens) if t.endswith("/df/bootstrap.py"))
    argv = tokens[at + 1 :]
    assert [t for t in argv if t.startswith("<")] == [SHA_PLACEHOLDER]
    return [SHA if t == SHA_PLACEHOLDER else t for t in argv]


def test_documented_bootstrap_argv_parses() -> None:
    """The documented command is `sudo <python> -I -B -S …/df/bootstrap.py init`
    and its argv builds the `InstallConfig` of profile `core`, nothing run."""
    assert documented_command()[:5] == ["sudo", PYTHON_PLACEHOLDER, "-I", "-B", "-S"]
    argv = documented_argv()
    assert launcher_entry.mode_of(argv) == "writer"
    args = cli._parse(argv)  # noqa: SLF001 - the `init` parser itself
    assert args.command == "init"
    assert cli._init_config(args) == InstallConfig(  # noqa: SLF001
        "andrei-shtanakov/ai-orchestrators-workspace",
        SHA,
        "core",
        {
            "git": Path("/usr/bin/git"),
            "tar": Path("/usr/bin/tar"),
            "uv": Path("/usr/local/libexec/darkfactory/bin/uv"),
        },
    )


def test_documented_bootstrap_without_tar_refused_before_any_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """S1-R5-N6: the same argv without `--cli tar=…` is a `ConfigError`, and
    bootstrap refuses it before the lock creates anything."""
    argv = documented_argv()
    at = argv.index("tar=/usr/bin/tar")
    assert argv[at - 1] == "--cli" and "tar" in REQUIRED_CLI
    argv = argv[: at - 1] + argv[at + 1 :]
    with pytest.raises(ConfigError, match="missing required: tar"):
        cli._init_config(cli._parse(argv))  # noqa: SLF001
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    lay = Layout(tmp_path / "root")
    code = cli.main(argv, mode="bootstrap", read=None, lock_fd=None, layout=lay)
    assert code == cli.EXIT_REFUSED
    assert "ConfigError: approved_cli: missing required: tar" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


# --- Task 4: roles setup and the service subcommands ------------------------

SERVICE = [
    ["launch", "exec", "df-exec", "check"],
    ["launch", "df-review", "check"],
    ["publisher", "serve", "merge"],
    ["publisher", "serve", "self-check"],
    ["precheck", "serve"],
    ["run", "start", "--product", "7"],
    ["run", "resume", "run-1"],
    ["run", "resume", "run-1", "--accept-model-change", "--from-step", "2"],
]
RAW = state.RawSnapshot(0, None, None, None, None, False)


def test_cli_roles_setup_is_writer() -> None:
    assert launcher_entry.mode_of(["roles", "setup"]) == "writer"
    assert launcher_entry.mode_of(["roles", "setup", "--operator", "a"]) == "writer"
    assert launcher_entry.mode_of(["setup-agent"]) == "writer"
    assert launcher_entry.mode_of(["roles"]) == "reader"


def test_cli_roles_setup_without_root_refused_by_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    lay = Layout(Path(os.path.realpath(tmp_path)))
    code = cli.main(
        ["roles", "setup", "--operator", "a"],
        mode="writer",
        read=Stable(RAW, 0),
        lock_fd=99,
        layout=lay,
    )
    assert code == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", SERVICE)
def test_cli_service_commands_are_readers(argv: list[str]) -> None:
    assert launcher_entry.mode_of(argv) == "reader"
    assert cli.parsed_mode(cli._parse(argv)) == "reader"  # noqa: SLF001


@pytest.mark.parametrize("argv", [a for a in SERVICE if a[0] == "publisher"])
def test_publisher_serve_reaches_the_frame(
    argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `df-publisher` account here: the frame refuses, as a response."""
    lay = Layout(Path(os.path.realpath(tmp_path)))
    code = cli.main(argv, mode="reader", read=Stable(RAW, 0), lock_fd=None, layout=lay)
    assert code == 2
    assert '"outcome":"refused"' in capsys.readouterr().out


def _perimeter_layout(tmp_path: Path, uid: int) -> Layout:
    from df.config import Account, dump_accounts

    lay = Layout(Path(os.path.realpath(tmp_path)))
    lay.install_dir.mkdir(parents=True)
    lay.perimeter_dir.mkdir(parents=True, mode=0o700)
    row = Account("df-publisher", uid, os.getgid(), "ready", "t")
    lay.accounts.write_text(dump_accounts({"df-publisher": row}))
    return lay


def _perimeter_init(lay: Layout) -> int:
    return cli.main(
        ["publisher", "perimeter-init"],
        mode="reader",
        read=Stable(RAW, 0),
        lock_fd=None,
        layout=lay,
    )


def test_cli_perimeter_init_is_a_reader() -> None:
    argv = ["publisher", "perimeter-init"]
    assert launcher_entry.mode_of(argv) == "reader"
    assert cli.parsed_mode(cli._parse(argv)) == "reader"  # noqa: SLF001


def test_cli_perimeter_init_creates_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from df.publisher import perimeter

    lay = _perimeter_layout(tmp_path, os.geteuid())
    assert _perimeter_init(lay) == 0
    assert "perimeter-init: created" in capsys.readouterr().out
    assert _perimeter_init(lay) == 0
    assert "perimeter-init: present" in capsys.readouterr().out
    (lay.perimeter_dir / perimeter.DB_NAME).unlink()
    assert _perimeter_init(lay) == 2
    assert perimeter.LOST_TEXT in capsys.readouterr().err
    assert not (lay.perimeter_dir / perimeter.DB_NAME).exists()


def test_cli_perimeter_init_orphan_journal_is_for_a_human(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from df.publisher import perimeter

    lay = _perimeter_layout(tmp_path, os.geteuid())
    (lay.perimeter_dir / perimeter.JOURNAL).write_bytes(b"old pages")
    assert _perimeter_init(lay) == 2
    assert perimeter.INTEGRITY_TEXT in capsys.readouterr().err
    assert not (lay.perimeter_dir / perimeter.DB_NAME).exists()


def test_cli_perimeter_init_only_as_the_publisher(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lay = _perimeter_layout(tmp_path, os.geteuid() + 1)
    assert _perimeter_init(lay) == 2
    assert "только от учётки df-publisher" in capsys.readouterr().err
    assert os.listdir(lay.perimeter_dir) == []


BAD_SERVICE = [
    ["publisher", "serve"],
    ["publisher", "serve", "merge", "extra"],
    ["publisher", "serve", "bogus"],
    ["publisher"],
    ["run"],
    ["run", "start"],
    ["run", "start", "extra"],
    ["run", "stop"],
    ["launch"],
    ["launch", "exec", "df-exec"],
    ["launch", "exec", "df-exec", "check", "extra"],
    ["launch", "df-exec"],
    ["launch", "df-exec", "check", "extra"],
    ["launch", "exec", "df-publisher", "check"],
    ["launch", "exec", "root", "check"],
    ["launch", "exec", "df-exec", "Check;rm"],
    ["roles"],
    ["roles", "setup", "extra"],
    ["precheck"],
    ["precheck", "serve", "extra"],
    ["precheck", "other"],
]


@pytest.mark.parametrize("argv", BAD_SERVICE)
def test_wrong_argument_shape_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(cli.UsageError):
        cli._parse(argv)  # noqa: SLF001


def test_run_usage_error_names_the_step_once() -> None:
    with pytest.raises(cli.UsageError) as caught:
        cli._parse(["run", "start"])  # noqa: SLF001
    assert str(caught.value).startswith("run: start: ")
    assert "run: run:" not in str(caught.value)


def test_publisher_serve_extra_arg_refused_before_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class NoStdin:
        def read(self, *_a: object) -> str:
            raise AssertionError("stdin was read")

        readline = read
        buffer = None

    monkeypatch.setattr("sys.stdin", NoStdin())
    lay = Layout(Path(os.path.realpath(tmp_path)))
    code = cli.main(
        ["publisher", "serve", "merge", "extra"],
        mode="reader",
        read=Stable(RAW, 0),
        lock_fd=None,
        layout=lay,
    )
    assert code == 2
    assert "extra" in capsys.readouterr().err


def test_roles_setup_needs_an_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    monkeypatch.delenv("SUDO_USER", raising=False)
    lay = Layout(Path(os.path.realpath(tmp_path)))
    code = cli.main(
        ["roles", "setup"],
        mode="writer",
        read=Stable(RAW, 0),
        lock_fd=99,
        layout=lay,
    )
    assert code == 2
    assert "укажите --operator" in capsys.readouterr().err


def test_roles_setup_operator_defaults_to_sudo_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUDO_USER", "carol")
    args = cli._parse(["roles", "setup"])  # noqa: SLF001
    assert cli._operator_of(args) == "carol"  # noqa: SLF001
    args = cli._parse(["roles", "setup", "--operator", "dave"])  # noqa: SLF001
    assert cli._operator_of(args) == "dave"  # noqa: SLF001
    monkeypatch.setenv("SUDO_USER", "")
    with pytest.raises(cli.Refusal, match="укажите --operator"):
        cli._operator_of(cli._parse(["roles", "setup"]))  # noqa: SLF001


def test_empty_operator_is_not_replaced_by_sudo_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUDO_USER", "carol")
    args = cli._parse(["roles", "setup", "--operator", ""])  # noqa: SLF001
    assert cli._operator_of(args) == ""  # noqa: SLF001


def test_entry_refuses_roles_setup_before_any_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The guarantee `main` cannot give: the entry refuses a non-root writer
    before it opens or creates the lock chain."""
    monkeypatch.setattr(launcher_entry, "ROOT_UID", os.geteuid() + 1)
    code = launcher_entry.run(["roles", "setup", "--operator", "a"], root=str(tmp_path))
    assert code == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


# --- Task 4a: the stage-2 sequence of the README -----------------------------

SECTION_2 = "## Установка DarkFactory: этап 2"


def documented_stage2() -> list[list[str]]:
    """The commands of the README's stage-2 `sh` block, one argv each."""
    text = README.read_text(encoding="utf-8")
    section = text.split(SECTION_2, 1)[1].split("\n## ", 1)[0]
    block = re.findall(r"```sh\n(.*?)```", section, re.DOTALL)[0]
    return [shlex.split(line) for line in block.splitlines() if line.strip()]


def test_documented_stage2_sequence_parses() -> None:
    """`cli set openssl` first, then `roles setup`; both writers under sudo,
    and the path is the one `roles setup`'s refusal names."""
    commands = documented_stage2()
    assert [c[:2] for c in commands] == [["sudo", "darkfactory"]] * 2
    argvs = [c[2:] for c in commands]
    assert argvs == [["cli", "set", "openssl", "/usr/bin/openssl"], ["roles", "setup"]]
    for argv in argvs:
        assert launcher_entry.mode_of(argv) == "writer"
        assert cli.parsed_mode(cli._parse(argv)) == "writer"  # noqa: SLF001
    assert " ".join(commands[0]) in roles_setup._NO_OPENSSL  # noqa: SLF001


# --- stage 2b Task 6: `precheck serve` as `df-precheck` ---------------------

RELEASE = "r1"
ADMIN_DIGEST = "sha256:" + "a" * 64


@pytest.fixture()
def environ() -> Iterator[None]:
    """`precheck serve` replaces the process environment: give it back."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _precheck_world(
    tmp_path: Path, uid: int, *, program: bool = True, site: bool = True
) -> tuple[Layout, Stable]:
    """An account `df-precheck` of `uid`, release `r1` current, its admin
    tree with the 2b files as stand-ins (`program`: `act_precheck.py`
    among them) or a legacy one."""
    from df.config import (
        Account,
        ReleaseEntry,
        ReleasesConfig,
        dump_accounts,
        dump_releases,
    )

    lay = Layout(Path(os.path.realpath(tmp_path)))
    lay.install_dir.mkdir(parents=True)
    row = Account("df-precheck", uid, os.getgid(), "ready", "t")
    lay.accounts.write_text(dump_accounts({"df-precheck": row}))
    tree = lay.admin_dir(RELEASE)
    (tree / "df").mkdir(parents=True)
    if program:
        for rel in admin_tree.LEGACY_2B.files():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text("")
        if site:
            (tree / "site").mkdir()
    entry = ReleaseEntry(RELEASE, "d", ADMIN_DIGEST, "3.12.13", "allowed", None)
    releases = dump_releases(ReleasesConfig((entry,))).encode()
    raw = state.RawSnapshot(0, RELEASE, releases, None, None, False)
    return lay, Stable(raw, 0)


def _precheck(lay: Layout, read: object) -> int:
    return cli.main(
        ["precheck", "serve"],
        mode="reader",
        read=read,  # type: ignore[arg-type]
        lock_fd=None,
        layout=lay,
    )


def _no_serve(_admin_dir: Path) -> object:
    raise AssertionError("the precheck program was loaded")


def test_precheck_serve_is_a_reader() -> None:
    argv = ["precheck", "serve"]
    assert launcher_entry.mode_of(argv) == "reader"
    assert cli.parsed_mode(cli._parse(argv)) == "reader"  # noqa: SLF001


@pytest.mark.parametrize("uid_delta", [1, None])
def test_precheck_serve_refuses_other_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
    uid_delta: int | None,
) -> None:
    """Another UID (or no `df-precheck` account): exit 2 before anything
    else -- the environment, `sys.path` and the program stay untouched."""
    lay, read = _precheck_world(tmp_path, os.getuid() + (uid_delta or 0))
    if uid_delta is None:
        from df.config import dump_accounts

        lay.accounts.write_text(dump_accounts({}))
    monkeypatch.setattr(cli, "_load_serve", _no_serve)
    os.environ["DF_TEST_SENTINEL"] = "kept"
    path = list(sys.path)
    assert _precheck(lay, read) == 2
    assert "только от учётки df-precheck" in capsys.readouterr().err
    assert os.environ["DF_TEST_SENTINEL"] == "kept"
    assert sys.path == path


def test_precheck_serve_resets_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environ: None,
) -> None:
    """The role's own process: the environment becomes exactly PATH, LANG
    and the role's HOME; `site` goes to the END of `sys.path`; then
    `serve(stdin, stdout, admin_digest=<record>, home=<role home>,
    complete_v2=True)` (a 2b tree: no list in its `df/`, `LEGACY_2B`)."""
    lay, read = _precheck_world(tmp_path, os.getuid())
    tree = lay.admin_dir(RELEASE)
    seen: dict[str, object] = {}

    def serve(
        stdin: object,
        stdout: object,
        *,
        admin_digest: str,
        home: Path,
        complete_v2: bool,
    ) -> int:
        seen.update(
            env=dict(os.environ),
            path=list(sys.path),
            streams=(stdin, stdout),
            admin_digest=admin_digest,
            home=home,
            complete_v2=complete_v2,
        )
        return 0

    def load(admin_dir: Path) -> object:
        seen["loaded_from"] = admin_dir
        seen["path_at_load"] = list(sys.path)
        return serve

    monkeypatch.setattr(cli, "_load_serve", load)
    monkeypatch.setattr(sys, "path", list(sys.path))
    for key, value in (("PYTHONPATH", "/evil"), ("GH_TOKEN", "t"), ("LANG", "x")):
        os.environ[key] = value
    before = list(sys.path)
    assert str(tree / "site") not in before
    assert _precheck(lay, read) == 0
    home = lay.role_home("df-precheck")
    assert seen["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(home)}
    assert seen["path_at_load"] == [*before, str(tree / "site")]
    assert seen["loaded_from"] == tree
    assert seen["streams"] == (sys.stdin.buffer, sys.stdout)
    assert (seen["admin_digest"], seen["home"]) == (ADMIN_DIGEST, home)
    assert seen["complete_v2"] is True


def test_precheck_serve_refuses_tree_without_2b_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
) -> None:
    """The program without a 2b module it imports: refused by name before
    anything is imported (not a `ModuleNotFoundError` inside it)."""
    lay, read = _precheck_world(tmp_path, os.getuid())
    (lay.admin_dir(RELEASE) / "governance" / "act_time.py").unlink()
    monkeypatch.setattr(cli, "_load_serve", _no_serve)
    path = list(sys.path)
    assert _precheck(lay, read) == 2
    err = capsys.readouterr().err
    assert "нет модулей предпроверки 2b: governance/act_time.py" in err
    assert sys.path == path


def test_precheck_serve_reaps_after_reset_before_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: None
) -> None:
    """2b Task 10: the leftovers of earlier runs are reaped as the role,
    after the environment reset and before the program loads."""
    lay, read = _precheck_world(tmp_path, os.getuid())
    order: list[object] = []

    def reap(uid: int) -> None:
        order.append(("reap", uid, dict(os.environ)))

    def load(_admin_dir: Path) -> object:
        order.append("load")
        return lambda *_a, **_k: 0

    monkeypatch.setattr(cli, "_reap", reap)
    monkeypatch.setattr(cli, "_load_serve", load)
    monkeypatch.setattr(sys, "path", list(sys.path))
    os.environ["GH_TOKEN"] = "t"
    assert _precheck(lay, read) == 0
    home = lay.role_home("df-precheck")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(home)}
    assert order == [("reap", os.getuid(), env), "load"]


def test_precheck_serve_passes_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: None
) -> None:
    lay, read = _precheck_world(tmp_path, os.getuid())
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(cli, "_load_serve", lambda _d: lambda *_a, **_k: 2)
    assert _precheck(lay, read) == 2


def test_precheck_serve_refuses_release_without_precheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
) -> None:
    """Spec 3.9 rev 10: a legacy release (no `governance/act_precheck.py`
    in its admin tree) is refused by name, exit 2, nothing loaded."""
    lay, read = _precheck_world(tmp_path, os.getuid(), program=False)
    monkeypatch.setattr(cli, "_load_serve", _no_serve)
    os.environ["DF_TEST_SENTINEL"] = "kept"
    path = list(sys.path)
    assert _precheck(lay, read) == 2
    err = capsys.readouterr().err
    assert "релиз без предпроверки" in err and "governance/act_precheck.py" in err
    assert sys.path == path and os.environ["DF_TEST_SENTINEL"] == "kept"


def test_precheck_serve_refuses_transitional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
) -> None:
    from df.journal import Transitional

    lay, _read = _precheck_world(tmp_path, os.getuid())
    monkeypatch.setattr(cli, "_load_serve", _no_serve)
    assert _precheck(lay, Transitional(1)) == 2
    assert capsys.readouterr().err


def test_precheck_program_from_another_tree_refused(tmp_path: Path) -> None:
    """The program must come from the checked admin tree: a
    `governance.act_precheck` found elsewhere (here: this checkout) is a
    refusal, never served."""
    pytest.importorskip("steward", reason="steward — без группы governance")
    with pytest.raises(cli.Refusal, match="act_precheck"):
        cli._load_serve(tmp_path)  # noqa: SLF001


def test_precheck_serve_refuses_tree_without_site(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
) -> None:
    """The program without `site/`: a named refusal (exit 2), not a
    traceback inside the program; nothing loaded, nothing changed."""
    lay, read = _precheck_world(tmp_path, os.getuid(), site=False)
    monkeypatch.setattr(cli, "_load_serve", _no_serve)
    os.environ["DF_TEST_SENTINEL"] = "kept"
    path = list(sys.path)
    assert _precheck(lay, read) == 2
    assert "нет каталога site/" in capsys.readouterr().err
    assert sys.path == path and os.environ["DF_TEST_SENTINEL"] == "kept"


@pytest.fixture()
def fresh_governance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No `governance*` module loaded before or after; `sys.path` restored."""

    def drop() -> None:
        for name in [n for n in sys.modules if n.split(".")[0] == "governance"]:
            del sys.modules[name]

    saved = {n: m for n, m in sys.modules.items() if n.split(".")[0] == "governance"}
    monkeypatch.setattr(sys, "path", list(sys.path))
    drop()
    try:
        yield
    finally:
        drop()
        sys.modules.update(saved)


def _fake_tree(root: Path) -> Path:
    """An admin tree whose `governance.act_precheck.serve` answers 7."""
    tree = root / "admin" / RELEASE
    (tree / "governance").mkdir(parents=True)
    (tree / "governance" / "__init__.py").write_text("")
    (tree / "governance" / "act_precheck.py").write_text(
        "def serve(*_a, **_k):\n    return 7\n"
    )
    return tree


def test_load_serve_from_the_checked_tree(
    tmp_path: Path, fresh_governance: None
) -> None:
    """The positive path as the entry sets it up: the tree first on
    `sys.path` (`launcher_entry._import_df`) -- `serve` of that tree."""
    tree = _fake_tree(Path(os.path.realpath(tmp_path)))
    sys.path.insert(0, str(tree))
    assert cli._load_serve(tree)() == 7  # noqa: SLF001


def test_load_serve_refuses_a_module_outside_the_tree(
    tmp_path: Path, fresh_governance: None
) -> None:
    """The module resolves to another tree than the one named: refused --
    also when the named tree is the same directory by another spelling (not
    `realpath`ed): the comparison is exact, the entry passes real paths."""
    real = Path(os.path.realpath(tmp_path))
    tree = _fake_tree(real / "a")
    other = _fake_tree(real / "b")
    sys.path.insert(0, str(other))
    with pytest.raises(cli.Refusal, match="не из"):
        cli._load_serve(tree)  # noqa: SLF001
    for name in [n for n in sys.modules if n.split(".")[0] == "governance"]:
        del sys.modules[name]
    sys.path[0] = str(tree)
    spelled = real / "a" / "admin" / ".." / "admin" / RELEASE
    with pytest.raises(cli.Refusal, match="не из"):
        cli._load_serve(spelled)  # noqa: SLF001


def _installed_tree(lay: Layout, *, old: bool) -> Path:
    """The admin tree an installer builds from this checkout as the pin:
    in `df/` the pin's own list (grown past 2b: the v2 program), and outside
    `df/` the real files -- of `LEGACY_2B` only for a 2b installer
    (`old`, spec 3.9 rev 11, the 2b -> 3a path), else of the whole list."""
    tree = lay.admin_dir(RELEASE)
    grown = admin_tree.load_tree_list(admin_tree_root())
    assert set(grown.files()) > set(admin_tree.LEGACY_2B.files())
    for rel in (admin_tree.LEGACY_2B if old else grown).files():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(admin_tree_root() / rel, tree / rel)
    shutil.copyfile(
        admin_tree_root() / admin_tree.TREE_LIST, tree / admin_tree.TREE_LIST
    )
    return tree


def admin_tree_root() -> Path:
    return Path(admin_tree.__file__).resolve().parents[1]


def _request_v1() -> dict[str, object]:
    """A v1 stop request answered before any I/O (no change: `refuse`)."""
    return {
        "schema": "df/precheck-request/v1",
        "nonce": "0" * 32,
        "kind": "stop",
        "repository_id": 42,
        "full_name": "o/r",
        "pr": 8,
        "head_sha": "b" * 40,
        "head_ref": "stop/x",
        "base_ref": "main",
        "base_tip": "c" * 40,
        "changes": [],
        "token": "ghs_t",
        "api_base": "http://127.0.0.1:9",
        "git_base": "http://127.0.0.1:9",
    }


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_old_installer_tree_v1_served_v2_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
    fresh_governance: None,
    schema: str,
) -> None:
    """A tree built by an installer older than its list, through the real
    `precheck serve` and the real program of the tree: the shortfall is
    named on stderr before the import; a v1 request is served (a v1
    verdict), a v2 one is refused `tree_incomplete` by name -- never a
    `ModuleNotFoundError`."""
    pytest.importorskip("steward", reason="steward — без группы governance")
    lay, read = _precheck_world(tmp_path, os.getuid())
    tree = _installed_tree(lay, old=True)
    verdict, err = _serve_in(tree, lay, read, schema, monkeypatch, capsys)
    assert admin_tree.INCOMPLETE_TREE in err
    assert "governance/act_precheck_v2.py" in err
    assert "ModuleNotFoundError" not in err and "Traceback" not in err
    if schema == "v1":
        assert verdict["schema"] == "df/precheck-verdict/v1"
        assert verdict["outcome"] == "refuse"
    else:
        assert verdict["schema"] == "df/precheck-verdict/v2"
        assert verdict["outcome"] == "tree_incomplete"
        assert "reinstall" in verdict["reason"]
    assert "governance.act_precheck_v2" not in sys.modules


def _serve_in(
    tree: Path,
    lay: Layout,
    read: object,
    schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[dict[str, object], str]:
    """The verdict and stderr of the real `precheck serve` over `tree` (first on
    `sys.path`, as the entry puts it) for a v1 or a v2 request."""
    sys.path.insert(0, str(tree))
    request = _request_v1()
    if schema == "v2":
        request = {"schema": "df/precheck-request/v2", "nonce": "0" * 32}
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
    monkeypatch.setattr(sys, "stdin", stdin)
    assert _precheck(lay, read) == 0
    out, err = capsys.readouterr()
    return json.loads(out), err


def test_complete_tree_imports_the_v2_program_after_the_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: None,
    fresh_governance: None,
) -> None:
    """A tree with every file of its list: nothing named on stderr, and a v2
    request is answered by `governance.act_precheck_v2` of that tree (until
    Task 7: `v2_unavailable`)."""
    pytest.importorskip("steward", reason="steward — без группы governance")
    lay, read = _precheck_world(tmp_path, os.getuid())
    tree = _installed_tree(lay, old=False)
    verdict, err = _serve_in(tree, lay, read, "v2", monkeypatch, capsys)
    assert admin_tree.INCOMPLETE_TREE not in err
    assert (verdict["outcome"], verdict["reason"]) == (
        "refuse",
        "v2_unavailable: protocol v2 is not served yet",
    )
    module = sys.modules["governance.act_precheck_v2"]
    assert Path(str(module.__file__)).parent == tree / "governance"


def test_only_import_module_of_df_outside_df_is_the_precheck() -> None:
    """`df` never imports `governance` statically; the one dynamic import of
    a non-`df` module is `precheck serve`'s, of exactly the precheck
    program. The entry's own `import_module` calls name `df.*` literals."""
    import ast

    calls: list[tuple[str, str]] = []
    root = Path(cli.__file__).parent
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                rel = path.relative_to(root.parent).as_posix()
                calls.append((rel, ast.unparse(node.args[0])))
    assert sorted(calls) == [
        ("df/cli.py", "PRECHECK_MODULE"),
        ("df/launcher_entry.py", "'df.cli'"),
        ("df/launcher_entry.py", "'df.paths'"),
        ("df/launcher_entry.py", "'df.state'"),
    ]
    assert cli.PRECHECK_MODULE == "governance.act_precheck"
