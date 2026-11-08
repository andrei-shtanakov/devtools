"""Конфиг DarkFactory для тестов скриптов (`$XDG_CONFIG_HOME/darkfactory/config.toml`).

Скрипты берут учётки, профили gh и источник политики из конфига; зашитых
умолчаний в коде нет. Тест даёт конфиг явно: значения владельца живут здесь,
в данных теста, а не в скриптах.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from governance import df_config

#: Временный «домашний каталог учётки» (`pw_dir`) теста — якорь режима test.
REAL_HOME_ENV = "DF_TEST_REAL_HOME"
#: Каталог тестового шима `python3` (см. `use_test_real_home`).
SHIM_DIR = Path(__file__).resolve().parent / "df_real_home_shim"

#: Переменные окружения оператора, которые перекрыли бы конфиг теста.
ENV_OVERRIDES = (
    "REVIEW_LOGIN",
    "AGENT_LOGIN",
    "REVIEW_GH_CONFIG_DIR",
    "MERGE_GH_CONFIG_DIR",
    "HUMAN_GH_CONFIG_DIR",
)


def write_config(
    xdg: Path,
    *,
    agent: str = "ai-prosto",
    human: str = "andrei-shtanakov",
    agent_gh_config_dir: Path | None = None,
    human_gh_config_dir: Path | None = None,
    policy_repo: str = "andrei-shtanakov/approval-policy",
    policy_ref: str = "main",
    policy_path: str = "policy/approvers.env",
    mode: str = "test",
    workspace_root: Path | None = None,
) -> dict[str, str]:
    """Записать конфиг режима `mode` в `xdg`; вернуть env с `XDG_CONFIG_HOME`.

    Режим test привязывается к якорю (`anchor_test_config`)."""
    agent_dir = agent_gh_config_dir or xdg / "gh-agent"
    human_dir = human_gh_config_dir or xdg / "gh-human"
    text = f"""\
schema = "darkfactory-config/v1"
mode = "{mode}"

[accounts]
human = "{human}"
agent = "{agent}"
human_gh_config_dir = "{human_dir}"
agent_gh_config_dir = "{agent_dir}"

[policy]
repo = "{policy_repo}"
ref = "{policy_ref}"
path = "{policy_path}"

[workspace]
root = "{workspace_root or xdg / "workspace"}"

[harness]
author = "claude"
model = "test-model"
"""
    path = xdg / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode == "test":
        anchor_test_config(xdg)
    return {"XDG_CONFIG_HOME": str(xdg)}


def use_test_real_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """ТОЛЬКО ДЛЯ ТЕСТОВ: `pw_dir` учётки — временный `home` (якорь режима test).

    Загрузчик принимает `mode = "test"` лишь из
    `<pw_dir>/.config/darkfactory/config.toml` (`df_config.canonical_config_path`).
    В процессе pytest подменяется `df_config._real_home`; дочерним процессам
    (`python3 -I governance/df_config.py` из скриптов) monkeypatch не доходит —
    им в начало PATH ставится шим `python3` (`SHIM_DIR`), подменяющий `pw_dir`
    на `$DF_TEST_REAL_HOME`. Якорь это не ослабляет: в бою так сделать можно,
    лишь подложив исполняемый файл в PATH (это уже исполнение кода от имени
    учётки), а не выставив ключ конфига или переменную, которую читает
    загрузчик. Пустой `DF_TEST_REAL_HOME` в env дочернего процесса — шим
    прозрачен (так тест проверяет якорь настоящим интерпретатором)."""
    monkeypatch.setenv(REAL_HOME_ENV, str(home))
    monkeypatch.setenv("PATH", f"{SHIM_DIR}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(df_config, "_real_home", _test_real_home)


def anchor_test_config(xdg: Path) -> None:
    """Сделать `xdg` каноническим: `$DF_TEST_REAL_HOME/.config` → `xdg`.

    Последний записанный конфиг test — канонический (симлинка, `realpath`)."""
    home = os.environ.get(REAL_HOME_ENV)
    if not home:
        return
    link = Path(home) / ".config"
    if link.resolve() == xdg.resolve():
        return
    if link.is_symlink():
        link.unlink()
    link.symlink_to(xdg.resolve(), target_is_directory=True)


def _test_real_home() -> Path:
    return Path(os.environ[REAL_HOME_ENV])
