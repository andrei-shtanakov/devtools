"""Пользовательский список целей конвейера рядом с конфигом DarkFactory.

В режиме test целью конвейера служит собственное репо коллеги, а не репо
флота. Оно регистрируется в небольшом TOML-файле `targets.toml` рядом с
каноническим конфигом (`df_config.canonical_config_path().parent`):

    schema = "darkfactory-targets/v1"

    [targets.my-repo]
    path = "/Users/me/src/my-repo"
    repo_url = "git@github.com:me/my-repo.git"

Файл пишет только явная регистрация пользователем:
`python -m governance.user_targets add [<dir>]`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from governance import df_config, run_state

SCHEMA = "darkfactory-targets/v1"
_FILE_NAME = "targets.toml"
_ENTRY_KEYS = frozenset({"path", "repo_url"})
_URL_PREFIXES = (
    "git@github.com:",
    "https://github.com/",
    "ssh://git@github.com/",
)


class TargetsError(RuntimeError):
    """Список целей нечитаем/неверен или регистрация невозможна."""


@dataclass(frozen=True)
class UserTarget:
    """Запись списка: имя репо, абсолютный каталог чекаута, origin-URL."""

    repo: str
    path: Path
    repo_url: str


def slug_from_url(url: str) -> str:
    """`owner/name` из remote-URL (формы git@ / https / ssh)."""
    for prefix in _URL_PREFIXES:
        if url.startswith(prefix):
            return url.removeprefix(prefix).removesuffix(".git")
    raise TargetsError(f"неизвестная форма remote-URL: {url!r}")


def targets_path() -> Path:
    """`targets.toml` рядом с каноническим конфигом (`XDG_CONFIG_HOME` не влияет)."""
    return df_config.canonical_config_path().parent / _FILE_NAME


def _parse_entry(file: Path, repo: str, raw: object) -> UserTarget:
    where = f"{file}: [targets.{repo}]"
    try:
        run_state.validate_id_component(repo, label="repo")
    except ValueError as exc:
        raise TargetsError(f"{where}: {exc}") from exc
    if not isinstance(raw, dict):
        raise TargetsError(f"{where}: ожидалась таблица")
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        raise TargetsError(f"{where}: неизвестный ключ {unknown[0]!r}")
    for key in sorted(_ENTRY_KEYS):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise TargetsError(f"{where}: ключ {key!r} обязателен (строка)")
    path = Path(raw["path"])
    if not path.is_absolute():
        raise TargetsError(f"{where}: path {raw['path']!r} не абсолютный")
    if not path.is_dir():
        raise TargetsError(f"{where}: path {raw['path']!r} — не каталог")
    slug_from_url(raw["repo_url"])  # форма URL; TargetsError без контекста
    return UserTarget(repo=repo, path=path, repo_url=raw["repo_url"])


def _read(file: Path) -> dict[str, UserTarget]:
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TargetsError(f"{file} не читается как TOML: {exc}") from exc
    if data.get("schema") != SCHEMA:
        raise TargetsError(f"{file}: ключ 'schema' обязателен и равен {SCHEMA!r}")
    extra = sorted(set(data) - {"schema", "targets"})
    if extra:
        raise TargetsError(f"{file}: неизвестный ключ {extra[0]!r}")
    table = data.get("targets", {})
    if not isinstance(table, dict):
        raise TargetsError(f"{file}: 'targets' должна быть таблицей")
    return {repo: _parse_entry(file, repo, raw) for repo, raw in table.items()}


def load() -> dict[str, UserTarget]:
    """Все цели списка (порядок файла); нет файла — пусто."""
    try:
        file = targets_path()
    except df_config.ConfigError as exc:
        raise TargetsError(str(exc)) from exc
    if not os.path.lexists(file):
        return {}
    return _read(file)


def lookup(repo: str) -> UserTarget | None:
    """Цель по имени репо или None."""
    return load().get(repo)


def _git_out(target_dir: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(target_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise TargetsError(f"git не запускается для {target_dir}: {exc}") from exc
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        raise TargetsError(
            f"{target_dir}: git {' '.join(args)} не дал результата "
            f"(не git-чекаут или нет origin): {proc.stderr.strip()}"
        )
    return out


def _q(value: str) -> str:
    """TOML basic string (JSON-квотирование, UTF-8 без \\u-суррогатов)."""
    return json.dumps(value, ensure_ascii=False)


def _render(entries: list[UserTarget]) -> str:
    lines = [f"schema = {_q(SCHEMA)}"]
    for t in entries:
        lines += [
            "",
            f"[targets.{_q(t.repo)}]",
            f"path = {_q(str(t.path))}",
            f"repo_url = {_q(t.repo_url)}",
        ]
    return "\n".join(lines) + "\n"


def _write_atomic(file: Path, text: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f"{file.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, file)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise TargetsError(f"{file} не записывается: {exc}") from exc


def register(target_dir: Path) -> UserTarget:
    """Внести git-чекаут в список (идемпотентно); вернуть запись."""
    top = Path(_git_out(target_dir, "rev-parse", "--show-toplevel"))
    repo = top.name
    try:
        run_state.validate_id_component(repo, label="repo")
    except ValueError as exc:
        raise TargetsError(f"{target_dir}: {exc}") from exc
    url = _git_out(target_dir, "remote", "get-url", "origin")
    slug_from_url(url)
    new = UserTarget(repo=repo, path=Path(os.path.realpath(top)), repo_url=url)
    existing = load()
    old = existing.get(repo)
    if old == new:
        return new
    if old is not None:
        raise TargetsError(
            f"репо {repo!r} уже зарегистрирован ({old.path}, {old.repo_url}) — "
            f"правьте файл {targets_path()} вручную"
        )
    _write_atomic(targets_path(), _render([*existing.values(), new]))
    return new


def main(argv: list[str]) -> int:
    """CLI: `add [<dir>]` и `list`. Код 0 или 2 (ошибка — в stderr)."""
    usage = "usage: python -m governance.user_targets (add [<dir>] | list)"
    try:
        if argv[:1] == ["add"] and len(argv) <= 2:
            t = register(Path(argv[1]) if len(argv) == 2 else Path.cwd())
            print(t.repo, t.path, t.repo_url)
            return 0
        if argv == ["list"]:
            for t in load().values():
                print(t.repo, t.path, t.repo_url)
            return 0
    except TargetsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
