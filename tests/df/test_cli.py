"""The README's first-install command is the command `df.cli` accepts (Task 14).

The argv is read from the README section, not copied here: the test fails when
the documented command drifts from the `init` parser or from `InstallConfig`.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import pytest

from df import cli, launcher_entry, roles_setup, state
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
