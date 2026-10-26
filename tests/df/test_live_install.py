"""The pure parts of `tests/df/live_install.py` (the run itself is CI-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.df import live_install
from tests.df.root_world import CORE_MANIFEST
from tests.df.test_cli import README, documented_command


def test_install_argv_is_the_readme_command() -> None:
    """Only the three placeholders differ from the README tokens."""
    argv = live_install.install_argv("/usr/bin/python3")
    tokens = documented_command()
    checkout = str(Path(live_install.__file__).resolve().parents[2])
    changed = [(a, b) for a, b in zip(tokens, argv, strict=True) if a != b]
    assert changed == [
        ("<python-3.12+>", "/usr/bin/python3"),
        ("<чекаут-devtools>/df/bootstrap.py", f"{checkout}/df/bootstrap.py"),
        ("<sha-манифеста>", CORE_MANIFEST[1]),
    ]
    assert argv[argv.index("--cli", argv.index("tar=/usr/bin/tar")) + 1] == (
        f"uv={live_install.UV_DIR}/uv"
    )


def test_uv_targets_are_the_readme_values() -> None:
    """The archive names and sha256 the script checks are the README's."""
    text = README.read_text(encoding="utf-8")
    for target, sha in live_install.UV_TARGETS.values():
        assert f"uv-{target}.tar.gz" in text
        assert sha in text
    assert f"-m 0755 {live_install.UV_DIR}" in text


def test_main_refuses_outside_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never on a workstation: no `GITHUB_ACTIONS`, exit 2, nothing written."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert live_install.main(["--out", str(tmp_path), "--work", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_bootstrap_python_recipe_is_the_readme_one() -> None:
    """The macOS recipe the script runs is the one README «Подготовка» gives."""
    text = README.read_text(encoding="utf-8").replace("\\\n    ", "")
    recipe = (
        f"sudo {live_install.UV_DIR}/uv python install {live_install.PYTHON}"
        f" --install-dir {live_install.BOOTSTRAP_PYTHON_DIR}"
    )
    assert recipe in " ".join(text.split())
