"""Конфиг DarkFactory для тестов скриптов (`$XDG_CONFIG_HOME/darkfactory/config.toml`).

Скрипты берут учётки, профили gh и источник политики из конфига; зашитых
умолчаний в коде нет. Тест даёт конфиг явно: значения владельца живут здесь,
в данных теста, а не в скриптах.
"""

from __future__ import annotations

from pathlib import Path

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
) -> dict[str, str]:
    """Записать конфиг режима `test` в `xdg`; вернуть env с `XDG_CONFIG_HOME`."""
    agent_dir = agent_gh_config_dir or xdg / "gh-agent"
    human_dir = human_gh_config_dir or xdg / "gh-human"
    text = f"""\
schema = "darkfactory-config/v1"
mode = "test"

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
root = "{xdg / "workspace"}"

[harness]
author = "claude"
model = "test-model"
"""
    path = xdg / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(xdg)}
