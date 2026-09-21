"""Вызов ревьюера в изоляции (спека D4).

Изоляция обеспечивается конструкцией вызова, а не просьбой к модели:
`--tools ""` снимает все инструменты, `--restricted` игнорирует
пользовательские настройки, `--safe-mode` снимает CLAUDE.md, скиллы, плагины,
хуки и MCP, `--strict-mcp-config` отрезает MCP оператора. Рабочий каталог —
пустой временный каталог вне воркспейса: даже если дискавери инструкций
где-то переживёт флаги, подхватывать будет нечего.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from governance.edge_check.rules import EdgeCheckError


def reviewer_argv(model: str, schema_path: Path, effort: str | None) -> list[str]:
    argv = ["claude", "-p", "--model", model]
    if effort:
        argv += ["--effort", effort]
    return [
        *argv,
        "--json-schema", schema_path.read_text(encoding="utf-8"),
        "--output-format", "json",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts", "none",
        "--tools", "",
    ]


def run_reviewer(
    prompt_text: str, argv: list[str], workdir: Path, timeout: int
) -> str:
    """Один подпроцесс; промпт — со stdin, чтобы вход не попал в argv."""
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
        )
    except subprocess.TimeoutExpired as exc:
        raise EdgeCheckError("timeout", f"ревьюер не ответил за {timeout} с") from exc
    except OSError as exc:
        raise EdgeCheckError(
            "reviewer_failed", f"ревьюер не запустился: {exc}"
        ) from exc
    if proc.returncode != 0:
        raise EdgeCheckError(
            "reviewer_failed",
            f"ревьюер вернул {proc.returncode}: {proc.stderr.strip()[:400]}",
        )
    return proc.stdout
