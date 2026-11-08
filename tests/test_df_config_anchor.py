"""Якорь режима test (ревью discovery#67): `mode = "test"` принимается только из
канонического конфига пользователя `<pw_dir>/.config/darkfactory/config.toml`.

Путь конфига следует `XDG_CONFIG_HOME`; без якоря любой процесс подсунул бы
поддельный конфиг `mode = "test"` с `accounts.human = <любой логин>`, и
синтетическая политика приняла бы этот логин утверждающим без человека.
`pw_dir` — из базы учёток (`pwd`), не `HOME` и не `XDG_CONFIG_HOME`.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest

from governance import df_config
from governance.df_config import ConfigError

ROOT = Path(__file__).resolve().parent.parent
#: Настоящий `_real_home` — до подмены автоюз-фикстурой (импорт модуля теста
#: идёт при сборе, раньше фикстур).
REAL_HOME_FN = getattr(df_config, "_real_home", None)

CONFIG = """\
schema = "darkfactory-config/v1"
mode = "{mode}"

[accounts]
human = "human-y"
agent = "bot-x"
human_gh_config_dir = "~/.config/gh"
agent_gh_config_dir = "~/.config/review"

[policy]
repo = "org-z/approval-policy"
ref = "main"
path = "policy/approvers.env"

[workspace]
root = "/nonexistent/ws"

[harness]
author = "claude"
model = "test-model"
"""


def _place(xdg: Path, mode: str) -> dict[str, str]:
    """Конфиг в `xdg` как есть — без привязки тестового якоря."""
    path = xdg / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG.format(mode=mode), encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(xdg)}


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Домашний каталог учётки (`pw_dir`) — временный."""
    real_home = tmp_path / "home"
    real_home.mkdir()
    monkeypatch.setattr(df_config, "_real_home", lambda: real_home)
    return real_home


def test_test_mode_from_redirected_xdg_refused(home: Path, tmp_path: Path) -> None:
    env = _place(tmp_path / "forged", "test")
    with pytest.raises(ConfigError) as exc:
        df_config.load(env)
    msg = str(exc.value)
    assert "mode" in msg and "режим test" in msg
    assert str(home / ".config" / "darkfactory" / "config.toml") in msg
    assert str(tmp_path / "forged" / "darkfactory" / "config.toml") in msg
    assert "XDG_CONFIG_HOME" in msg


def test_test_mode_from_canonical_path_loads(home: Path) -> None:
    env = _place(home / ".config", "test")
    assert df_config.load(env).mode == "test"
    assert df_config.canonical_config_path() == (
        home / ".config" / "darkfactory" / "config.toml"
    )


def test_strict_from_redirected_xdg_still_loads(home: Path, tmp_path: Path) -> None:
    """Доверие strict от пути конфига не зависит (источник политики — SSOT)."""
    env = _place(tmp_path / "elsewhere", "strict")
    assert df_config.load(env).mode == "strict"


def test_canonical_reached_via_symlinked_xdg_loads(home: Path, tmp_path: Path) -> None:
    _place(home / ".config", "test")
    link = tmp_path / "xdg-link"
    link.symlink_to(home / ".config")
    assert df_config.load({"XDG_CONFIG_HOME": str(link)}).mode == "test"


def test_redirected_home_is_not_canonical(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`HOME` перенаправлен (XDG не задан): конфиг `$HOME/.config` — не канон."""
    fake = tmp_path / "fake-home"
    _place(fake / ".config", "test")
    monkeypatch.setenv("HOME", str(fake))
    with pytest.raises(ConfigError, match="режим test принимается только из"):
        df_config.load({})


def test_real_home_is_pw_dir_not_home_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert REAL_HOME_FN is not None
    monkeypatch.setattr(df_config, "_real_home", REAL_HOME_FN)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pw_dir = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert df_config.canonical_config_path() == (
        pw_dir / ".config" / "darkfactory" / "config.toml"
    )


def test_cli_real_interpreter_refuses_redirected_test_mode(tmp_path: Path) -> None:
    """Дочерний процесс без тестового шима (настоящий интерпретатор, `-I`):
    конфиг test из перенаправленного XDG — отказ 2 с названным ключом."""
    env = {**os.environ, **_place(tmp_path / "forged", "test")}
    done = subprocess.run(
        [sys.executable, "-I", str(ROOT / "governance" / "df_config.py")]
        + ["get", "accounts.human"],
        cwd="/",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stdout) == (2, ""), done.stderr
    assert "режим test принимается только из" in done.stderr
