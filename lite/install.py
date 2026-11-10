"""Лёгкая установка DarkFactory (режим test): комплектация, конфиг, `dfl`.

Запускается из `install.sh`: клонирует закреплённые соседние репо рядом с
devtools, пишет конфиг режима test и кладёт обёртку `dfl` в `~/.local/bin`.
Каталог `lite/` лежит вне `governance/`, поэтому может звать `uv`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from governance import df_config
from governance import run_state as rs
from lite.pipeline import chain_open

HERE = Path(__file__).resolve().parent
COMPONENTS = HERE / "components.toml"
TEMPLATE = HERE.parent / "templates" / "darkfactory" / "config.test.example.toml"
SCHEMA = "darkfactory-components/v1"
TERMINAL = ("completed", "merged_unverified", "brief_ready")
NET_HINT = "проверьте сеть и доступ к репо, затем повторите install.sh"
LAUNCHER = """\
#!/bin/sh
# DarkFactory (режим test) — создано install.sh; повторный install.sh перепишет.
here=$PWD
cd "{devtools}" || exit 2
exec uv run --frozen --group governance python -m lite.dfl --cwd "$here" "$@"
"""

Runner = Callable[..., subprocess.CompletedProcess]


class InstallError(Exception):
    """Установка невозможна — отказ с понятной причиной."""


@dataclass(frozen=True)
class Component:
    """Закреплённый соседний репо: адрес, коммит и способ установки."""

    name: str
    url: str
    sha: str
    tool: bool


def load_components(path: Path = COMPONENTS) -> list[Component]:
    """Прочитать комплектацию; неверная схема или не 40-hex `sha` — ошибка."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise InstallError(f"{path}: ожидалась schema = {SCHEMA!r}")
    out = []
    for name, body in data.get("repos", {}).items():
        sha = str(body.get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise InstallError(f"{path}: у {name} sha не 40-hex: {sha!r}")
        url = str(body.get("url", ""))
        if not url:
            raise InstallError(f"{path}: у {name} нет url")
        out.append(Component(name, url, sha, bool(body.get("tool", False))))
    return out


def active_runs(runs_root: Path) -> list[str]:
    """run_id незаконченных прогонов (или нечитаемых).

    Незакончен — статус не терминальный, либо test-engineer прогон
    `completed`, чья цепочка после прогона ещё не дошла до code-accept."""
    if not runs_root.is_dir():
        return []
    active = []
    for run_json in sorted(runs_root.glob("*/run.json")):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
            if not _run_active(data):
                continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        active.append(run_json.parent.name)
    return active


def _run_active(data: dict) -> bool:
    status = data["status"]
    if status not in TERMINAL:
        return True
    frame = (data.get("interview") or {}).get("frame")
    return chain_open(data.get("mode"), frame, status, data.get("ops") or {})


def _git(run: Runner, directory: Path, *args: str, check: bool = True):
    """git в `directory`; сбой при `check` — InstallError со stderr git."""
    cmd = ["git", "-C", str(directory), *args]
    try:
        return run(cmd, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise InstallError(_git_failure(args[0], directory, exc)) from exc


def _git_failure(verb: str, directory: Path, exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
    detail = f": {stderr}" if stderr else f" (код {exc.returncode})"
    return f"git {verb} в {directory} не удался{detail} — {NET_HINT}"


def _check_existing(directory: Path, comp: Component, run: Runner) -> None:
    probe = _git(run, directory, "rev-parse", "--git-dir", check=False)
    if probe.returncode != 0:
        raise InstallError(f"{directory}: каталог есть, но это не git-репозиторий")
    origin = _git(run, directory, "config", "--get", "remote.origin.url", check=False)
    if origin.stdout.strip() != comp.url:
        raise InstallError(
            f"{directory}: origin {origin.stdout.strip()!r} не совпадает с {comp.url}"
        )
    if _git(run, directory, "status", "--porcelain").stdout.strip():
        raise InstallError(
            f"{directory}: локальные изменения — зафиксируйте или уберите"
        )
    if _git(run, directory, "symbolic-ref", "-q", "HEAD", check=False).returncode == 0:
        raise InstallError(f"{directory}: нештатная ветка (ожидался detached HEAD)")


def _install_deps(directory: Path, comp: Component, run: Runner) -> None:
    if comp.tool:
        run(["uv", "tool", "install", "--force", str(directory)], check=True)
        return
    cmd = ["uv", "sync"]
    if (directory / "uv.lock").exists():
        cmd.append("--frozen")
    run(cmd, check=True, cwd=directory)


def check_components(
    root: Path, comps: list[Component], run: Runner = subprocess.run
) -> None:
    """Проверить ВСЕ существующие клоны до любого fetch/checkout (отказ без эффектов)."""
    for comp in comps:
        directory = root / comp.name
        if directory.exists():
            _check_existing(directory, comp, run)


def sync_component(root: Path, comp: Component, run: Runner = subprocess.run) -> None:
    """Привести `root/<name>` к закреплённому коммиту и поставить зависимости."""
    directory = root / comp.name
    if directory.exists():
        _check_existing(directory, comp, run)
        _git(run, directory, "fetch", "origin")
        _git(run, directory, "checkout", "--detach", comp.sha)
    else:
        _fresh_clone(directory, comp, run)
    _install_deps(directory, comp, run)


def _fresh_clone(directory: Path, comp: Component, run: Runner) -> None:
    """Клон на пин; любой сбой удаляет свежий каталог (иначе вечный отказ)."""
    try:
        _git(run, directory.parent, "clone", comp.url, str(directory))
        _git(run, directory, "checkout", "--detach", comp.sha)
    except InstallError:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def write_config(login: str, root: Path, path: Path, template: Path = TEMPLATE) -> bool:
    """Записать конфиг test из шаблона; существующий не трогать (`False`)."""
    if path.exists():
        return False
    text = template.read_text(encoding="utf-8")
    text = text.replace("{login}", login).replace("{root}", str(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def write_launcher(bin_dir: Path, devtools: Path) -> Path:
    """Записать обёртку `dfl` (режим 0o755) в `bin_dir`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    out = bin_dir / "dfl"
    out.write_text(LAUNCHER.format(devtools=devtools), encoding="utf-8")
    out.chmod(0o755)
    return out


def _login_hint(run: Runner) -> str:
    try:
        res = run(
            ["gh", "api", "user", "-q", ".login"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _resolve_login(args: argparse.Namespace, run: Runner) -> str:
    if args.login:
        return str(args.login)
    hint = _login_hint(run)
    if args.yes and hint:
        return hint
    if args.yes:
        raise InstallError(
            "--yes: логин GitHub не узнать (gh не вошёл) — укажите --login <логин>"
        )
    prompt = f"Логин GitHub [{hint}]: " if hint else "Логин GitHub: "
    try:
        login = input(prompt).strip() or hint
    except EOFError as exc:
        raise InstallError(
            "логин GitHub не спросить: ввод не из терминала — укажите --login <логин>"
        ) from exc
    if not login:
        raise InstallError("не указан логин GitHub (--login)")
    return login


def _warn_if_not_test(path: Path) -> None:
    try:
        mode = tomllib.loads(path.read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError):
        mode = None
    if mode != "test":
        print(
            f"Предупреждение: {path} не в режиме test (mode={mode!r}); "
            "dfl работает только в режиме test — конфиг не изменён.",
            file=sys.stderr,
        )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="install.sh")
    parser.add_argument("--login", help="логин GitHub для конфига")
    parser.add_argument("--yes", action="store_true", help="без вопросов")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runs_root: Path | None = None,
    devtools: Path | None = None,
    components: Path = COMPONENTS,
    config_path: Path | None = None,
    bin_dir: Path | None = None,
    run: Runner = subprocess.run,
) -> int:
    """Точка входа install.sh. Коды: 0 — готово, 1 — ошибка, 2 — есть прогоны."""
    args = _parse(argv)
    runs = rs.RUNS_ROOT if runs_root is None else runs_root
    active = active_runs(runs)
    if active:
        print(
            "Есть незавершённые прогоны DarkFactory: " + ", ".join(active),
            file=sys.stderr,
        )
        print(
            "Компоненты посреди прогона не обновляются. В каталоге репо каждого "
            "прогона: dfl status покажет, что дальше; закончите его (dfl run) "
            "или бросьте (dfl abandon) — и повторите установку.",
            file=sys.stderr,
        )
        return 2
    dt = HERE.parent if devtools is None else devtools
    try:
        return _install(args, run, dt, components, config_path, bin_dir)
    except (InstallError, df_config.ConfigError) as exc:
        print(f"Ошибка установки: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"Ошибка установки: {_cmd_text(exc)} завершилась с кодом "
            f"{exc.returncode} (вывод выше) — исправьте причину и повторите install.sh",
            file=sys.stderr,
        )
        return 1


def _cmd_text(exc: subprocess.CalledProcessError) -> str:
    cmd = exc.cmd
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


def _install(
    args: argparse.Namespace,
    run: Runner,
    devtools: Path,
    components: Path,
    config_path: Path | None,
    bin_dir: Path | None,
) -> int:
    root = devtools.parent
    comps = load_components(components)
    check_components(root, comps, run)
    for comp in comps:
        print(f"Компонент {comp.name} @ {comp.sha[:12]}")
        sync_component(root, comp, run)
    cfg = df_config.canonical_config_path() if config_path is None else config_path
    if cfg.exists():
        _warn_if_not_test(cfg)
    else:
        write_config(_resolve_login(args, run), root, cfg)
        print(f"Конфиг записан: {cfg}")
    target = Path.home() / ".local" / "bin" if bin_dir is None else bin_dir
    launcher = write_launcher(target, devtools)
    print(f"Обёртка: {launcher}")
    if str(target) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f'Добавьте в PATH: export PATH="{target}:$PATH"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
