"""Вызов ревьюера в изоляции (спека D4).

Изоляция обеспечивается конструкцией вызова, а не просьбой к модели:
`--tools ""` снимает все инструменты, `--restricted` игнорирует
пользовательские настройки, `--safe-mode` снимает CLAUDE.md, скиллы, плагины,
хуки и MCP, `--strict-mcp-config` отрезает MCP оператора. Рабочий каталог —
пустой временный каталог вне воркспейса: даже если дискавери инструкций
где-то переживёт флаги, подхватывать будет нечего.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from governance.edge_check.rules import EdgeCheckError

#: I3: явный allowlist окружения подпроцесса ревьюера. Изоляционные флаги
#: закрывают настройки, CLAUDE.md, скиллы и MCP, но не транспорт вызова
#: модели — без allowlist подпроцесс наследует ВСЁ окружение оператора
#: (включая случайные секреты), а куда фактически ушёл запрос, по записи
#: не восстановить.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "CLOUD_ML_REGION",
    "ANTHROPIC_VERTEX_PROJECT_ID",
)


def reviewer_env() -> dict[str, str]:
    """Окружение подпроцесса: только транспорт вызова модели (находка I3).

    Состав allowlist (не значения!) записывается в результат, чтобы было
    видно, что именно могло уйти наружу.
    """
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


def reviewer_argv(model: str, schema_path: Path, effort: str | None) -> list[str]:
    """Собрать argv ревьюера: ноль инструментов, ни одной кастомизации.

    Каждый флаг закрывает свой источник постороннего входа: `--tools ""` —
    инструменты, `--restricted`/`--safe-mode`/`--strict-mcp-config` —
    пользовательские настройки, CLAUDE.md, скиллы, плагины, хуки и MCP.
    """
    try:
        schema_text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EdgeCheckError(
            "missing_schema", f"{schema_path}: схема ответа не найдена"
        ) from exc
    argv = ["claude", "-p", "--model", model]
    if effort:
        argv += ["--effort", effort]
    return [
        *argv,
        "--json-schema", schema_text,
        "--output-format", "json",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts", "none",
        "--tools", "",
    ]


def harness_version(binary: str = "claude") -> str | None:
    """Версия харнесса разово (находка I3, спека §4.3); `None` — честный
    признак, что версию узнать не удалось, а не молчаливая пустота."""
    try:
        proc = subprocess.run(  # noqa: S603 — бинарь фиксирован по умолчанию
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=reviewer_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def run_reviewer(
    prompt_text: str,
    argv: list[str],
    workdir: Path,
    timeout: int,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Один подпроцесс; промпт — со stdin, чтобы вход не попал в argv.

    `env` по умолчанию — `reviewer_env()` (находка I3): подпроцесс не
    наследует окружение оператора целиком.
    """
    if not workdir.is_dir() or any(workdir.iterdir()):
        raise EdgeCheckError(
            "unsafe_workdir",
            f"{workdir}: рабочий каталог ревьюера обязан быть пустым",
        )
    try:
        proc = subprocess.run(  # noqa: S603 — argv собран здесь же
            argv,
            input=prompt_text,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=timeout,
            env=env if env is not None else reviewer_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise EdgeCheckError("timeout", f"ревьюер не ответил за {timeout} с") from exc
    except OSError as exc:
        raise EdgeCheckError(
            "reviewer_failed", f"ревьюер не запустился: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise EdgeCheckError(
            "reviewer_failed", f"вывод ревьюера не в UTF-8: {exc}"
        ) from exc
    if proc.returncode != 0:
        raise EdgeCheckError(
            "reviewer_failed",
            f"ревьюер вернул {proc.returncode}: {proc.stderr.strip()[:400]}",
        )
    return proc.stdout
