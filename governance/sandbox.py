"""Каталог вызова, профиль Seatbelt и допуск харнесса авторского агента.

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§11, §12: агент пишет только в свежий каталог вызова
`<calls>/<run_id>/<call>/{stage,tmp,claude-config}`, созданный раннером вне
песочницы через `safe_io`; профиль генерируется из `realpath` этих каталогов.
Генератор — чистая функция; настоящим `sandbox-exec` его проверяет проба
(TASK-004). TASK-002.

§14: `admission_check` допускает исполняемый файл харнесса по записи
`contracts/author-sandbox/v1/admitted.toml` — `(realpath, sha256, macos_major,
profile_sha256)` — НЕ исполняя кандидата. TASK-003.

Модуль — в защищённой цепочке песочницы (authority-root, шаг H1); импортирует
только stdlib и модули цепочки. Граница гарантий — дизайн §17, §19 п.3.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from governance import safe_io

#: Подкаталоги вызова — единственные разрешённые агенту корни записи (§11).
CALL_SUBDIRS = ("stage", "tmp", "claude-config")
#: Каталоги учётных данных оператора, закрытые на чтение (§3).
SECRET_DIRS = (".config/review", ".config/gh", ".config/ai-prosto", ".ssh")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: Шаблон профиля §12. Подстановки — `{stage}`, `{tmp}`, `{claude_config}`,
#: `{secret_reads}`; других фигурных скобок в тексте нет. Хэш шаблона без
#: подстановок — поле `profile_sha256` записи допуска (§14).
PROFILE_TEMPLATE = """\
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath {stage}) (subpath {tmp})
                   (subpath {claude_config})
                   (literal "/dev/null") (regex #"^/dev/fd/"))
(deny file-write* (regex #"(^|/)\\.git(/|$)"))
(deny file-ioctl (regex #"^/dev/tty"))
(deny file-read* {secret_reads})
(deny mach-lookup (global-name "com.apple.SecurityServer")
                  (global-name "com.apple.securityd.xpc")
                  (global-name "com.apple.security.agent")
                  (global-name-prefix "com.apple.coreservices.")
                  (global-name-prefix "com.apple.lsd."))
"""


class SandboxError(RuntimeError):
    """Каталог вызова или профиль построить нельзя — агент не запускается."""


@dataclass(frozen=True)
class CallDir:
    """Свежий каталог вызова; `root` — канонический (`realpath`)."""

    root: Path

    @property
    def stage(self) -> Path:
        return self.root / "stage"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def claude_config(self) -> Path:
        return self.root / "claude-config"


def calls_root(environ: Mapping[str, str] | None = None) -> Path:
    """Канонический путь корня `<calls>` (§11); ничего не создаёт.

    `$XDG_STATE_HOME/devtools/agent-calls`, без переменной —
    `~/.local/state/devtools/agent-calls`. Относительный `XDG_STATE_HOME` —
    отказ: его разрешение зависело бы от cwd раннера. Корень создаёт
    `create_call_dir` — после проверки непересечения, чтобы отказ не оставлял
    каталогов в цели.
    """
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    if not os.path.isabs(xdg):
        raise SandboxError(f"XDG_STATE_HOME {xdg!r} не абсолютный")
    return Path(os.path.realpath(Path(xdg) / "devtools" / "agent-calls"))


def target_git_dirs(target_dir: Path) -> tuple[Path, Path]:
    """gitdir и common dir цели (`git rev-parse --path-format=absolute`)."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(target_dir),
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) != 2:
        raise SandboxError(
            f"gitdir цели {target_dir} не определён: {proc.stderr.strip()}"
        )
    return Path(lines[0]), Path(lines[1])


@dataclass(frozen=True)
class ForbiddenRoots:
    """Корни, с которыми каталог вызова не пересекается (§12 п.2).

    Все пять полей обязательны: неполный набор не выразить, и проверка
    непересечения не выключается значением аргумента.
    """

    target_dir: Path
    gitdir: Path
    common_dir: Path
    devtools_root: Path
    run_dir: Path

    def named(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("target_dir", self.target_dir),
            ("gitdir", self.gitdir),
            ("common dir", self.common_dir),
            ("чекаут devtools", self.devtools_root),
            ("run_dir", self.run_dir),
        )


def forbidden_roots(
    target_dir: Path, *, run_dir: Path, devtools_root: Path
) -> ForbiddenRoots:
    """Набор §12 п.2 для цели: gitdir и common dir — из `git rev-parse`."""
    git_dir, common_dir = target_git_dirs(target_dir)
    return ForbiddenRoots(target_dir, git_dir, common_dir, devtools_root, run_dir)


def _check_id(kind: str, value: str) -> str:
    if not _ID.fullmatch(value):
        raise SandboxError(f"{kind} {value!r}: ждали [A-Za-z0-9._-], не с точки")
    return value


def create_call_dir(
    calls: Path,
    run_id: str,
    *,
    forbidden: ForbiddenRoots,
    call_id: str | None = None,
) -> CallDir:
    """Создать свежий `<calls>/<run_id>/<call>` с подкаталогами (§12 п.1–2).

    `calls` — канонический путь корня (`calls_root`). Пересечение будущего
    каталога вызова с любым из `forbidden` (ни предок, ни потомок; по
    `realpath`) — отказ ДО создания чего-либо, в том числе самого корня;
    существующий `<call>` — отказ.
    """
    root = str(calls)
    if not os.path.isabs(root) or os.path.realpath(root) != root:
        raise SandboxError(f"корень вызовов {root!r} не канонический (realpath)")
    run = _check_id("run_id", run_id)
    call = _check_id("call", call_id or uuid.uuid4().hex)
    path = PurePosixPath(root, run, call)
    for name, other in forbidden.named():
        real = PurePosixPath(os.path.realpath(other))
        if safe_io.paths_overlap(path, real):
            raise SandboxError(f"каталог вызова {path} пересекается с {name} {real}")
    try:
        os.makedirs(root, exist_ok=True)
        if os.path.realpath(root) != root:
            raise SandboxError(f"корень вызовов {root!r} подменён ссылкой")
        safe_io.ensure_dirs(calls, run)
        safe_io.create_dir(calls, f"{run}/{call}")
        for sub in CALL_SUBDIRS:
            safe_io.create_dir(calls, f"{run}/{call}/{sub}")
    except (OSError, safe_io.SafeIOError) as exc:
        raise SandboxError(f"каталог вызова {path}: {exc}") from exc
    return CallDir(Path(path))


def _refuse_control(path: str) -> None:
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        raise SandboxError(f"путь {path!r} с управляющим символом")


def _sbpl_path(path: Path) -> str:
    """Литерал SBPL для `realpath(path)`: `\\` и `"` экранируются.

    Управляющие символы (в том числе NUL и перевод строки) — отказ до
    канонизации: такой путь не выражается одной строкой профиля.
    """
    raw = str(path)
    if not os.path.isabs(raw):
        # относительный достроился бы cwd раннера — у агента это target_dir
        raise SandboxError(f"путь {raw!r} не абсолютный")
    _refuse_control(raw)
    real = os.path.realpath(raw)
    _refuse_control(real)  # цель ссылки тоже
    return '"' + real.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_profile(call: CallDir, *, home: Path) -> str:
    """Профиль §12 для каталога вызова `call`; `home` — домашний оператора.

    Seatbelt сверяет разрешённый путь, поэтому подставляются `realpath`
    (в том числе закрытых каталогов — `~/.config` бывает ссылкой).
    """
    secret = " ".join(f"(subpath {_sbpl_path(home / rel)})" for rel in SECRET_DIRS)
    return PROFILE_TEMPLATE.format(
        stage=_sbpl_path(call.stage),
        tmp=_sbpl_path(call.tmp),
        claude_config=_sbpl_path(call.claude_config),
        secret_reads=secret,
    )


def profile_template_sha256() -> str:
    """sha256 шаблона профиля без подстановок — для записи допуска (§14).

    В хэш входят и константы, которые генератор подставляет в шаблон
    (`CALL_SUBDIRS`, `SECRET_DIRS`): ослабление запрета чтения учёток —
    тоже дрейф профиля и обязано снимать допуск.
    """
    material = "\n".join(
        (PROFILE_TEMPLATE, "\0".join(CALL_SUBDIRS), "\0".join(SECRET_DIRS))
    )
    return hashlib.sha256(material.encode()).hexdigest()


# --- допуск исполняемого файла харнесса (§14, TASK-003) ---------------------

#: Потолок чтения кандидата для sha256 (claude 2.1.296 — ≈240 МБ).
ADMISSION_READ_LIMIT = 1 << 30
ADMISSION_READ_TIMEOUT = 60.0
_SW_VERS = "/usr/bin/sw_vers"  # системный бинарь, не кандидат
_HEX64 = re.compile(r"[0-9a-f]{64}")
_FIELDS: dict[str, type] = {
    "harness": str,
    "version": str,
    "realpath": str,
    "sha256": str,
    "macos_major": int,
    "profile_sha256": str,
    "evidence": str,
}


@dataclass(frozen=True)
class Admission:
    """Допущенный исполняемый файл: запускать именно `realpath` (§14)."""

    harness: str
    version: str
    realpath: Path
    sha256: str
    macos_major: int
    profile_sha256: str
    evidence: str


def _not_admitted(why: str) -> SandboxError:
    return SandboxError(f"харнесс не проверен под песочницей: {why}")


def _record(raw: Any, index: int) -> Admission:
    if not isinstance(raw, dict) or set(raw) != set(_FIELDS):
        raise SandboxError(f"admitted[{index}]: ждали ровно поля {sorted(_FIELDS)}")
    for name, kind in _FIELDS.items():
        if type(raw[name]) is not kind:
            raise SandboxError(f"admitted[{index}].{name}: ждали {kind.__name__}")
    for name in ("sha256", "profile_sha256"):
        if not _HEX64.fullmatch(raw[name]):
            raise SandboxError(f"admitted[{index}].{name}: ждали 64 hex")
    if not os.path.isabs(raw["realpath"]):
        raise SandboxError(f"admitted[{index}].realpath: не абсолютный")
    return Admission(**{**raw, "realpath": Path(raw["realpath"])})


def load_admitted(contract_path: Path) -> tuple[Admission, ...]:
    """Записи контракта допуска; нет файла или записей — отказ (§14)."""
    try:
        data = tomllib.loads(contract_path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise _not_admitted(f"нет контракта {contract_path}") from exc
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SandboxError(f"контракт допуска {contract_path}: {exc}") from exc
    schema = data.get("schema")
    if type(schema) is not int or schema != 1:  # True == 1.0 == 1 в Python
        raise SandboxError(f"контракт допуска {contract_path}: schema != 1")
    raw = data.get("admitted", [])
    if not isinstance(raw, list):
        raise SandboxError(f"контракт допуска {contract_path}: admitted не массив")
    records = tuple(_record(item, i) for i, item in enumerate(raw))
    if not records:
        raise _not_admitted("в контракте допуска нет записей (до шага H2)")
    return records


def _macos_major() -> int:
    """Мажорная версия macOS из `sw_vers` — кандидат не исполняется."""
    try:
        proc = subprocess.run(
            [_SW_VERS, "-productVersion"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f"версия macOS не определена: {exc}") from exc
    head = proc.stdout.strip().split(".")[0]
    if proc.returncode != 0 or not head.isdigit():
        raise SandboxError(f"версия macOS не определена: {proc.stderr.strip()}")
    return int(head)


def _file_sha256(real: str) -> str:
    try:
        data = safe_io.read_regular(
            Path("/"),
            real.lstrip("/"),
            limit=ADMISSION_READ_LIMIT,
            timeout=ADMISSION_READ_TIMEOUT,
        )
    except safe_io.SafeIOError as exc:
        raise _not_admitted(f"{real}: {exc}") from exc
    return hashlib.sha256(data).hexdigest()


def admission_check(
    contract_path: Path, *, harness: str = "claude", path: str | None = None
) -> Admission:
    """Допуск исполняемого файла `harness` из PATH раннера (§14).

    `shutil.which` → `realpath` → sha256 содержимого через `safe_io` →
    `sw_vers` → сверка `(realpath, sha256, macos_major, profile_sha256)` с
    записями контракта. Кандидат НЕ исполняется ни с каким аргументом,
    включая `--version`: версия берётся из записи. Иначе SandboxError с
    названным полем расхождения.
    """
    records = [r for r in load_admitted(contract_path) if r.harness == harness]
    found = shutil.which(harness, path=path)
    if found is None:
        raise _not_admitted(f"{harness!r} не найден в PATH раннера")
    real = os.path.realpath(found)
    same_path = [r for r in records if str(r.realpath) == real]
    if not same_path:
        raise _not_admitted(f"{harness} {real} нет в контракте допуска")
    actual = {
        "sha256": _file_sha256(real),
        "macos_major": _macos_major(),
        "profile_sha256": profile_template_sha256(),
    }
    misses: list[str] = []
    for record in same_path:
        wrong = [k for k, v in actual.items() if getattr(record, k) != v]
        if not wrong:
            return record
        misses.append(f"{record.version}: не совпали {', '.join(wrong)}")
    raise _not_admitted(f"{harness} {real} — " + "; ".join(misses))
