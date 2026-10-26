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

from df import cli, launcher_entry
from df.config import REQUIRED_CLI, ConfigError, InstallConfig
from df.paths import Layout

README = Path(__file__).resolve().parents[2] / "README.md"
SECTION = "## Установка DarkFactory: этап 1"
#: The one placeholder of the documented argv: the human's manifest commit.
SHA_PLACEHOLDER = "<sha-манифеста>"
SHA = "0" * 40


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
    assert documented_command()[:5] == ["sudo", "/usr/bin/python3", "-I", "-B", "-S"]
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
