"""Конфиг DarkFactory: `$XDG_CONFIG_HOME/darkfactory/config.toml` (дизайн §5.1).

Источник учёток, профилей gh, источника политики и корня воркспейса вместо
зашитых в коде значений владельца. Умолчаний и fallback нет: нет файла, нет
ключа, неверный тип, неизвестный ключ или `mode`, ключ, обязательный для
режима, отсутствует — `ConfigError` с названием файла и ключа.

Режимы: `strict` — `[policy]` обязателен, каталоги профилей gh — только из
перечня `STRICT_GH_DIRS` (равенство с перечнем песочницы авторинга,
`governance/secret_dirs.py`, закрепляет тест); `test` —
`[policy]` необязателен, ограничения на каталоги нет.

Якорь режима test: `mode = "test"` принимается ТОЛЬКО из канонического
конфига пользователя `<pw_dir>/.config/darkfactory/config.toml`, где
`pw_dir` — домашний каталог учётки из базы `pwd` (не `HOME`, не
`XDG_CONFIG_HOME`). Сверяется тождество файла, РЕАЛЬНО прочитанного:
`(st_dev, st_ino)` из `fstat` его дескриптора против `os.stat` канона
(симлинки канона разрешаются; канона нет — отказ), а не пути после чтения —
иначе подмена файла ↔ симлинки на канон между чтением и сверкой (TOCTOU)
прошла бы. Путь чтения следует
`XDG_CONFIG_HOME`, и без якоря любой процесс подсунул бы поддельный конфиг
test с `accounts.human = <любой логин>` — синтетическая политика приняла бы
его утверждающим без человека (та же ручка «env подменяет утверждающих», что
закрывает отказ `AUTHORIZED_APPROVER_ACCOUNTS`). strict из перенаправленного
пути работает как прежде: его доверие от пути конфига не зависит (источник
политики — SSOT).

Модуль исполняется скриптами мержа (`df_config.sh` → `python3 -I <этот файл>
get <ключ>`), поэтому он под authority-root и харнесс-гвардом. Импорты только
stdlib: файл вендорится дословно (discovery) и запускается под `-I` без пакета
`governance`. Файл читается при каждом `load()`, кэша на импорте нет.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 11):  # noqa: UP036 — запуск системным python3
    # `tomllib` — с 3.11; на старом `python3` (macOS: 3.9) — именованный отказ.
    _ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
    print(
        f"df_config: нужен Python ≥ 3.11 (сейчас {_ver}); "
        "установите python3 ≥ 3.11 или uv",
        file=sys.stderr,
    )
    raise SystemExit(2)

import os  # noqa: E402
import pwd  # noqa: E402
import re  # noqa: E402
import tomllib  # noqa: E402
from collections.abc import Callable, Mapping  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Literal  # noqa: E402

SCHEMA = "darkfactory-config/v1"
#: Каталоги профилей gh (относительно `~`), допустимые в strict.
STRICT_GH_DIRS: tuple[str, ...] = (
    ".config/review",
    ".config/gh",
    ".config/ai-prosto",
    ".ssh",
)
#: Что в режиме test реально выключено (дописывают следующие срезы).
#: Сам режим test принимается только из `canonical_config_path()` (якорь).
TEST_RELAXATIONS: tuple[str, ...] = (
    "репо политики утверждения не используется: утверждающий — accounts.human",
)
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
MODES = ("strict", "test")

_TOP = {"schema", "mode", "accounts", "policy", "workspace", "harness", "app"}
_TABLES = {
    "accounts": ("human", "agent", "human_gh_config_dir", "agent_gh_config_dir"),
    "policy": ("repo", "ref", "path"),
    "workspace": ("root",),
    "harness": ("author", "model"),
    "app": ("id", "key_file"),
}
_GH_DIR_KEYS = ("human_gh_config_dir", "agent_gh_config_dir")


class ConfigError(Exception):
    """Конфиг не читается или не проходит проверку — отказ конфигурации."""


@dataclass(frozen=True)
class Accounts:
    """Учётки человека и агента и каталоги их профилей gh."""

    human: str
    agent: str
    human_gh_config_dir: Path
    agent_gh_config_dir: Path


@dataclass(frozen=True)
class PolicySource:
    """Координаты политики утверждающих: репо, ref и путь файла в нём."""

    repo: str
    ref: str
    path: str


@dataclass(frozen=True)
class DfConfig:
    """Разобранный и проверенный конфиг DarkFactory."""

    mode: Literal["strict", "test"]
    accounts: Accounts
    policy: PolicySource | None
    workspace_root: Path
    author_harness: str
    author_model: str
    app_id: int | None
    app_key_file: Path | None


def config_home(env: Mapping[str, str]) -> Path:
    """`$XDG_CONFIG_HOME` (только абсолютный, как требует XDG), иначе `~/.config`.

    То же правило обязана применять discovery для `act.toml` (M2, заметки
    части A п. 11) — расхождение дало бы draining только у одного из двух.
    """
    raw = env.get("XDG_CONFIG_HOME", "")
    return Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".config"


def test_banner(cfg: DfConfig) -> str | None:
    """Баннер режима test (что выключено); в strict — `None`."""
    if cfg.mode == "strict":
        return None
    return "режим test: выключено — " + "; ".join(TEST_RELAXATIONS)


def config_path(env: Mapping[str, str]) -> Path:
    """Путь конфига: `<config_home>/darkfactory/config.toml` (`config_home`)."""
    return config_home(env) / "darkfactory" / "config.toml"


def canonical_config_path() -> Path:
    """Канонический конфиг учётки: `<pw_dir>/.config/darkfactory/config.toml`.

    Только отсюда принимается `mode = "test"` (якорь, см. docstring модуля).
    Нет записи учётки в `pwd` или `pw_dir` не абсолютный — `ConfigError`."""
    try:
        home = _real_home()
    except KeyError as exc:
        raise ConfigError(f"нет записи учётки uid={os.getuid()} в pwd") from exc
    if not home.is_absolute():
        raise ConfigError(f"домашний каталог учётки (pwd) не абсолютный: {home!r}")
    return home / ".config" / "darkfactory" / "config.toml"


def _real_home() -> Path:
    """Домашний каталог учётки процесса из базы `pwd` — не `HOME`."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def load(env: Mapping[str, str] = os.environ) -> DfConfig:
    """Прочитать и проверить конфиг; любой дефект — `ConfigError`.

    `mode = "test"` не из `canonical_config_path()` — `ConfigError` (якорь)."""
    path = config_path(env)
    data, read_stat = _read(path)
    parser = _Parser(path)
    cfg = parser.parse(data)
    if cfg.mode == "test":
        _require_canonical(parser, path, read_stat)
    return cfg


def _require_canonical(parser: _Parser, path: Path, read: os.stat_result) -> None:
    """Якорь test: ПРОЧИТАННЫЙ файл (fstat его дескриптора) — тот же inode, что
    канонический. Сверка по пути после чтения оставляла бы гонку подмены
    файла ↔ симлинки на канон в перенаправленном каталоге (TOCTOU)."""
    try:
        canonical = canonical_config_path()
    except ConfigError as exc:
        raise parser.fail("mode", f"режим test: {exc}") from exc
    try:
        want = os.stat(canonical)
    except OSError:
        same = False
    else:
        same = (read.st_dev, read.st_ino) == (want.st_dev, want.st_ino)
    if not same:
        raise parser.fail(
            "mode",
            f"режим test принимается только из {canonical}; конфиг "
            f"прочитан из {path} (перенаправление XDG_CONFIG_HOME/HOME)",
        )


def workspace_root(env: Mapping[str, str] | None = None) -> Path:
    """Корень workspace (`workspace.root`): единственный источник раскладки
    соседних репо. Конфиг обязателен — нет файла или он битый: `ConfigError`."""
    env = os.environ if env is None else env
    return load(env).workspace_root


def workspace_dir(name: str, env: Mapping[str, str] | None = None) -> Path:
    """`<workspace.root>/<name>`; нет каталога — `ConfigError` с названным
    путём (до любого subprocess). Префикс «конфиг DarkFactory: » ставит
    вызывающий."""
    return require_dir(workspace_root(env), name)


def require_dir(root: Path, name: str) -> Path:
    """`root/name`, если это каталог; иначе `ConfigError` с названным путём."""
    path = root / name
    if not path.is_dir():
        raise ConfigError(f"workspace.root={root}: нет каталога {name}")
    return path


def agent_gh_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Профиль gh агента: env `REVIEW_GH_CONFIG_DIR`, иначе
    `accounts.agent_gh_config_dir` (порядок `review-pr.sh`; одно правило для
    `ops` и `act_*`). Пустое значение env — как отсутствие."""
    env = os.environ if env is None else env
    raw = env.get("REVIEW_GH_CONFIG_DIR")
    return Path(raw) if raw else load(env).accounts.agent_gh_config_dir


def agent_login(env: Mapping[str, str] | None = None) -> str:
    """Учётка агента: env `REVIEW_LOGIN`, иначе `accounts.agent` (порядок
    `review-pr.sh`). Пустое значение env — как отсутствие."""
    env = os.environ if env is None else env
    return env.get("REVIEW_LOGIN") or load(env).accounts.agent


def _read(path: Path) -> tuple[dict[str, object], os.stat_result]:
    """TOML конфига и `fstat` дескриптора, с которого он прочитан (якорь)."""
    try:
        with open(path, "rb") as fh:
            read_stat = os.fstat(fh.fileno())
            text = fh.read().decode("utf-8")
    except FileNotFoundError as exc:
        if path.is_symlink():
            raise ConfigError(f"{path}: висячая симлинка — конфиг не читается") from exc
        raise ConfigError(f"{path}: файла нет — конфиг обязателен") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: не читается ({exc})") from exc
    try:
        return tomllib.loads(text), read_stat
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: не разбирается как TOML ({exc})") from exc


class _Parser:
    """Проверка разобранного TOML; каждое сообщение называет файл и ключ."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def fail(self, key: str, why: str) -> ConfigError:
        return ConfigError(f"{self.path}: {key} — {why}")

    def parse(self, data: dict[str, object]) -> DfConfig:
        self.unknown(data, _TOP, "")
        schema = self.string(data, "schema", "schema")
        if schema != SCHEMA:
            raise self.fail("schema", f"ожидается {SCHEMA!r}, а не {schema!r}")
        mode = self.string(data, "mode", "mode")
        if mode not in MODES:
            raise self.fail("mode", f"неизвестный режим {mode!r}; {'|'.join(MODES)}")
        strict = mode == "strict"
        acc = self.table(data, "accounts")
        accounts = Accounts(
            human=self.login(acc, "human"),
            agent=self.login(acc, "agent"),
            human_gh_config_dir=self.gh_dir(acc, "human_gh_config_dir", strict),
            agent_gh_config_dir=self.gh_dir(acc, "agent_gh_config_dir", strict),
        )
        if strict and accounts.human_gh_config_dir == accounts.agent_gh_config_dir:
            # Один профиль на обе учётки стёр бы различитель merged_by agent/human.
            raise self.fail(
                "accounts.human_gh_config_dir",
                "в strict совпадает с accounts.agent_gh_config_dir — "
                "у человека и агента профили gh обязаны различаться",
            )
        if strict and accounts.agent == accounts.human:
            # Одна учётка на обе роли стёрла бы тот же различитель merged_by.
            raise self.fail(
                "accounts.agent",
                "в strict совпадает с accounts.human — "
                "учётки человека и агента обязаны различаться",
            )
        harness = self.table(data, "harness")
        app = self.table(data, "app") if "app" in data else None
        return DfConfig(
            mode="strict" if strict else "test",
            accounts=accounts,
            policy=self.policy(data, strict),
            workspace_root=self.path_value(
                self.table(data, "workspace"), "root", "workspace.root"
            ),
            author_harness=self.string(harness, "author", "harness.author"),
            author_model=self.string(harness, "model", "harness.model"),
            app_id=None if app is None else self.integer(app, "id", "app.id"),
            app_key_file=None
            if app is None
            else self.path_value(app, "key_file", "app.key_file"),
        )

    def policy(self, data: dict[str, object], strict: bool) -> PolicySource | None:
        if "policy" not in data:
            if strict:
                raise self.fail("policy", "таблица обязательна в режиме strict")
            return None
        pol = self.table(data, "policy")
        return PolicySource(
            repo=self.string(pol, "repo", "policy.repo"),
            ref=self.string(pol, "ref", "policy.ref"),
            path=self.string(pol, "path", "policy.path"),
        )

    def unknown(self, data: Mapping[str, object], known: set[str], where: str) -> None:
        for key in sorted(set(data) - known):
            raise ConfigError(f"{self.path}: неизвестный ключ {where}{key}")

    def table(self, data: dict[str, object], name: str) -> dict[str, object]:
        if name not in data:
            raise self.fail(name, "таблица обязательна")
        value = data[name]
        if not isinstance(value, dict):
            raise self.fail(name, "ожидается таблица")
        self.unknown(value, set(_TABLES[name]), f"{name}.")
        return value

    def raw(self, data: Mapping[str, object], key: str, dotted: str) -> object:
        if key not in data:
            raise self.fail(dotted, "ключ обязателен")
        return data[key]

    def string(self, data: Mapping[str, object], key: str, dotted: str) -> str:
        value = self.raw(data, key, dotted)
        if not isinstance(value, str) or not value:
            raise self.fail(dotted, "ожидается непустая строка")
        return value

    def login(self, data: Mapping[str, object], key: str) -> str:
        dotted = f"accounts.{key}"
        value = self.string(data, key, dotted)
        if not _LOGIN.fullmatch(value):
            raise self.fail(dotted, f"{value!r} — не один логин GitHub")
        return value

    def integer(self, data: Mapping[str, object], key: str, dotted: str) -> int:
        value = self.raw(data, key, dotted)
        if type(value) is not int:
            raise self.fail(dotted, "ожидается целое число")
        return value

    def path_value(self, data: Mapping[str, object], key: str, dotted: str) -> Path:
        raw = self.string(data, key, dotted)
        if raw.startswith("~/") and raw[2:].startswith("/"):
            # `Path.home() / "/x"` — это `/x`: корень вместо домашнего каталога.
            raise self.fail(dotted, f"путь {raw!r}: после `~/` лишний `/`")
        path = _expand(raw)
        if not path.is_absolute():
            raise self.fail(dotted, f"путь {raw!r} не абсолютный (после `~/`)")
        return path

    def gh_dir(self, acc: Mapping[str, object], key: str, strict: bool) -> Path:
        dotted = f"accounts.{key}"
        path = self.path_value(acc, key, dotted)
        allowed = [Path.home() / rel for rel in STRICT_GH_DIRS]
        if strict and path not in allowed:
            names = ", ".join(f"~/{rel}" for rel in STRICT_GH_DIRS)
            raise self.fail(
                dotted,
                f"в strict — только каталог, закрытый песочницей ({names})",
            )
        return path


def _expand(raw: str) -> Path:
    """Ведущее `~/` (или `~`) — домашний каталог; прочее — как есть."""
    if raw == "~":
        return Path.home()
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    return Path(raw)


def _policy_field(name: str) -> Callable[[DfConfig], str]:
    def get(cfg: DfConfig) -> str:
        if cfg.policy is None:
            raise ConfigError(f"policy.{name} — таблица [policy] не задана")
        return str(getattr(cfg.policy, name))

    return get


_GETTERS: dict[str, Callable[[DfConfig], str]] = {
    "mode": lambda c: c.mode,
    "accounts.human": lambda c: c.accounts.human,
    "accounts.agent": lambda c: c.accounts.agent,
    "accounts.human_gh_config_dir": lambda c: str(c.accounts.human_gh_config_dir),
    "accounts.agent_gh_config_dir": lambda c: str(c.accounts.agent_gh_config_dir),
    "policy.repo": _policy_field("repo"),
    "policy.ref": _policy_field("ref"),
    "policy.path": _policy_field("path"),
    "workspace.root": lambda c: str(c.workspace_root),
    "harness.author": lambda c: c.author_harness,
    "harness.model": lambda c: c.author_model,
}


def get_value(cfg: DfConfig, key: str) -> str:
    """Значение ключа CLI (`mode`, `accounts.*`, `policy.*`, `workspace.root`)."""
    getter = _GETTERS.get(key)
    if getter is None:
        raise ConfigError(f"{key} — неизвестный ключ CLI; {', '.join(_GETTERS)}")
    return getter(cfg)


def main(argv: list[str], env: Mapping[str, str] = os.environ) -> int:
    """`get <ключ>` → значение в stdout, 0; ошибка → stderr, 2."""
    if len(argv) != 2 or argv[0] != "get":
        print("использование: df_config.py get <ключ>", file=sys.stderr)
        return 2
    try:
        cfg = load(env)
    except ConfigError as exc:
        print(f"конфиг DarkFactory: {exc}", file=sys.stderr)
        return 2
    try:
        value = get_value(cfg, argv[1])
    except ConfigError as exc:
        print(f"конфиг DarkFactory: {config_path(env)}: {exc}", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
