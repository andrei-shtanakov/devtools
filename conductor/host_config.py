"""Конфиг хоста conductor (спека среза 1, §4.2).

Ошибка конфига — `ConfigError` (находка CFG-INVALID): записей нет, чтение и
выдача — как в срезе 0.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# state/ среза 0 занят runs/ и conductor.lock — init-state требует пустой каталог
FLEET_STATE_DIR = Path("/srv/conductor/opstate")
SHARED_DIR = Path("/srv/conductor/shared")
# Один lock хоста на все профили: они делят журнал установки (§5.5). Он вне
# восстанавливаемых каталогов состояния — recover/init-state его не трогают.
LOCK_PATH = Path("/srv/conductor/state/conductor.lock")
STOP_POINTS = frozenset({"after_comment", "before_close", "after_intent", "after_send"})
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
Profile = Literal["fleet", "acceptance"]

_TOP = {"app", "run", "acceptance"}
_APP = {"app_id", "installation_id", "private_key"}
_RUN = {"profile", "shadow", "state_dir", "lock"}
_OPTIONAL = {"lock"}
_ACC = {"sandbox", "outside", "stop_points"}


class ConfigError(Exception):
    """CFG-INVALID: конфиг хоста не принят."""


@dataclass(frozen=True)
class HostConfig:
    """Разобранный конфиг хоста."""

    app_id: int
    installation_id: int
    private_key: Path
    profile: Profile
    shadow: bool
    state_dir: Path
    lock: Path = LOCK_PATH
    sandbox: str | None = None
    outside: tuple[str, ...] = ()
    stop_points: frozenset[str] = frozenset()


def _table(data: dict[str, Any], name: str, keys: set[str]) -> dict[str, Any]:
    raw = data.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[{name}] не таблица")
    if unknown := set(raw) - keys:
        raise ConfigError(f"[{name}]: неизвестные ключи {sorted(unknown)}")
    if missing := keys - _OPTIONAL - set(raw) if name in ("app", "run") else set():
        raise ConfigError(f"[{name}]: нет ключей {sorted(missing)}")
    return raw


def _positive(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key}={value!r}: нужно целое > 0")
    return value


def _abs_path(value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError(f"{key}={value!r}: нужен абсолютный путь")
    return Path(value)


def _repos(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(v, str) and REPO_RE.match(v) for v in value
    ):
        raise ConfigError(f"{key}: нужен список owner/name")
    return tuple(value)


def load_host_config(path: Path) -> HostConfig:
    """Прочитать и проверить конфиг хоста; любая ошибка — ConfigError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML: {exc}") from exc
    if unknown := set(data) - _TOP:
        raise ConfigError(f"неизвестные таблицы {sorted(unknown)}")
    if "app" not in data or "run" not in data:
        raise ConfigError("нужны таблицы [app] и [run]")
    app, run = _table(data, "app", _APP), _table(data, "run", _RUN)
    acc = _table(data, "acceptance", _ACC)
    profile = run["profile"]
    if profile not in ("fleet", "acceptance"):
        raise ConfigError(f"profile={profile!r}")
    if not isinstance(run["shadow"], bool):
        raise ConfigError(f"shadow={run['shadow']!r}: нужно true/false")
    base = {
        "app_id": _positive(app["app_id"], "app_id"),
        "installation_id": _positive(app["installation_id"], "installation_id"),
        "private_key": _abs_path(app["private_key"], "private_key"),
        "shadow": run["shadow"],
        "state_dir": _abs_path(run["state_dir"], "state_dir"),
        "lock": _abs_path(run.get("lock", str(LOCK_PATH)), "lock"),
    }
    if base["lock"].is_relative_to(base["state_dir"]):
        raise ConfigError("lock внутри state_dir: init-state --recover его перенёс бы")
    if profile == "fleet":
        if "acceptance" in data:
            raise ConfigError("[acceptance] в профиле fleet")
        return HostConfig(profile="fleet", **base)
    sandbox = acc.get("sandbox")
    if not isinstance(sandbox, str) or not REPO_RE.match(sandbox):
        raise ConfigError(f"sandbox={sandbox!r}: нужен owner/name")
    stops = acc.get("stop_points", [])
    if not isinstance(stops, list) or not set(stops) <= STOP_POINTS:
        raise ConfigError(f"stop_points={stops!r}: допустимы {sorted(STOP_POINTS)}")
    if base["state_dir"] == FLEET_STATE_DIR:
        raise ConfigError("state_dir acceptance совпадает с боевым")
    return HostConfig(
        profile="acceptance",
        sandbox=sandbox,
        outside=_repos(acc.get("outside", []), "outside"),
        stop_points=frozenset(stops),
        **base,
    )
