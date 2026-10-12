"""Каталог вызова, профиль Seatbelt и допуск харнесса авторского агента.

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§11, §12: агент пишет только в свежий каталог вызова
`<calls>/<run_id>/<call>/{stage,tmp,claude-config}`, созданный раннером вне
песочницы через `safe_io`; профиль генерируется из `realpath` этих каталогов.
Генератор — чистая функция; настоящим `sandbox-exec` его проверяет проба
(TASK-004). TASK-002.

§4, §15: `run_probe` — проба-канарейка под той же обёрткой и профилем,
что и агент; структурированный результат с названным пунктом отказа.
TASK-004.

§14: `admission_check` допускает исполняемый файл харнесса по записи
`contracts/author-sandbox/v1/admitted.toml` — `(realpath, sha256, macos_major,
profile_sha256)` — НЕ исполняя кандидата. TASK-003.

Модуль — в защищённой цепочке песочницы (authority-root, шаг H1); импортирует
только stdlib и модули цепочки. Граница гарантий — дизайн §17, §19 п.3.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Mapping, Sequence
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


# --- проба-канарейка (§4, §15, TASK-004) -------------------------------------

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROBE_TIMEOUT = 60.0
#: Потолок каждого подпроцесса внутри пробы; сумма всех внутренних
#: потолков меньше `PROBE_TIMEOUT` — сработавшим оказывается названный
#: пункт, а не безымянный внешний таймаут.
PROBE_STEP_TIMEOUT = 5.0
#: Apple Events под профилем не отвечают (F21) — ждём столько и пишем
#: «нет ответа», а не «доставка закрыта» (§17 п.7).
APPLE_EVENTS_TIMEOUT = 3.0
#: Четыре пробы связки (§4 п.3): по учёткам, общий поиск, учётка модели.
KEYCHAIN_PROBES = (
    ("-s", "gh:github.com", "-a", "ai-prosto"),
    ("-s", "gh:github.com", "-a", "andrei-shtanakov"),
    ("-s", "gh:github.com"),
    ("-s", "Claude Code-credentials"),
)
BAIT = Path(".config/ai-prosto/.sandbox-bait")
#: Канарейка связки: элемент, который раннер кладёт вне песочницы перед
#: пробой и снимает после. Ненулевой `security` по именам §4 п.3 не отличает
#: «запрещено» от «нет элемента»; канарейка есть всегда — без обёртки её
#: находят (код 0), под профилем — нет.
KEYCHAIN_CANARY = "devtools-sandbox-probe"
_LSAPPINFO = "/usr/bin/lsappinfo"
#: Засчитанный отказ файловой операции — только запрет профиля (§15).
_DENIED = "EPERM"
#: `/dev/tty`: новая сессия даёт ENXIO, запрет профиля — EPERM (F28).
_TTY_DENIED = frozenset({"EPERM", "ENXIO", "EACCES"})
_GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

#: Скрипт пробы: исполняется под обёрткой, только stdlib, печатает JSON
#: исходов. Сам ничего не решает — оценку делает раннер (`_evaluate`).
PROBE_SCRIPT = r"""
import errno, json, os, subprocess, sys

cfg = json.load(open(sys.argv[1]))
out = {}


def code(exc):
    return errno.errorcode.get(exc.errno, str(exc.errno))


def attempt(name, fn):
    try:
        fn()
        out[name] = "ok"
    except OSError as exc:
        out[name] = code(exc)


def create(path):
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def write(path, flags=os.O_APPEND):
    fd = os.open(path, os.O_WRONLY | flags)
    try:
        os.write(fd, b"x")
    finally:
        os.close(fd)


def run(name, argv, timeout=cfg["step_timeout"]):
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout
        )
        out[name] = f"rc={proc.returncode}"
    except subprocess.TimeoutExpired:
        out[name] = "timeout"
    except OSError as exc:
        out[name] = code(exc)


for name, path in cfg["sentinels"].items():
    attempt("sentinel:" + name, lambda p=path: create(p))

st, outside, stage, tmp = cfg["stand"], cfg["outside"], cfg["stage"], cfg["tmp"]
attempt("stand:unlink-gitfile", lambda: os.unlink(st["gitfile_unlink"]))
attempt(
    "stand:rename-gitfile",
    lambda: os.rename(st["gitfile_rename"], st["gitfile_rename"] + "-x"),
)
attempt("stand:replace-gitfile", lambda: write(st["gitfile_replace"], os.O_TRUNC))
attempt("stand:config", lambda: write(st["config"]))
attempt("stand:hook", lambda: create(st["hook"]))
attempt("stand:gitdir-write", lambda: write(st["gitdir_head"]))
attempt("stage:nested-git-dir", lambda: os.makedirs(stage + "/nested/.git"))
attempt("stage:nested-gitfile", lambda: (os.makedirs(stage + "/nested2", exist_ok=True), create(stage + "/nested2/.git")))
attempt("link:outside", lambda: os.link(outside, stage + "/hl"))
attempt("link:gitfile", lambda: os.link(st["gitfile_link"], stage + "/hl-git"))
attempt("move:outside", lambda: os.rename(cfg["outside_mv"], stage + "/moved"))
attempt("symlink:runner-made", lambda: write(cfg["runner_link"]))
attempt("symlink:agent-made", lambda: (os.symlink(outside, stage + "/l2"), write(stage + "/l2")))
attempt("read:bait", lambda: open(cfg["bait"], "rb").read())
attempt("tty:open", lambda: os.close(os.open("/dev/tty", os.O_RDWR)))
attempt("allow:stage", lambda: (os.makedirs(stage + "/.probe/a/b"), create(stage + "/.probe/a/b/n")))
attempt("allow:tmp", lambda: create(tmp + "/.probe"))

for i, args in enumerate(cfg["keychain"]):
    run(f"keychain:{i}", ["/usr/bin/security", "find-generic-password", *args])
run(
    "keychain:canary",
    ["/usr/bin/security", "find-generic-password", "-s", cfg["keychain_canary"]],
)
eff = cfg["effects"]
run("launchd:submit", ["/bin/launchctl", "submit", "-l", cfg["label_submit"], "--",
    "/bin/sh", "-c", "echo x > " + eff + "/submit"])
plist = tmp + "/probe.plist"


def put(path, text, mode=0o644):
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


def prepared(name, fn):
    # подготовка внутри песочницы (файлы в tmp/); не вышла — исход пункта
    try:
        fn()
        return True
    except OSError as exc:
        out[name] = "prep:" + code(exc)
        return False


if prepared("launchd:bootstrap", lambda: put(plist, cfg["plist"])):
    run("launchd:bootstrap", ["/bin/launchctl", "bootstrap", "gui/%d" % os.getuid(), plist])
run("cron:exec", ["/usr/bin/crontab", "-l"])
app = tmp + "/Probe.app/Contents"


def build_app():
    os.makedirs(app + "/MacOS")
    put(app + "/Info.plist", cfg["app_plist"])
    # живёт 3 с после эффекта: к оценке (проба ждёт Apple Events) раннер
    # видит запущенный .app в lsappinfo — основной признак §15
    put(app + "/MacOS/probe", "#!/bin/sh\necho x > " + eff + "/open\nsleep 3\n", 0o755)


if prepared("launchservices:open", build_app):
    run("launchservices:open", ["/usr/bin/open", "-g", "-j", tmp + "/Probe.app"])
run("apple-events", ["/usr/bin/osascript", "-e", 'tell application "Finder" to get name'],
    timeout=cfg["ae_timeout"])
print(json.dumps(out))
"""

_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
<string>echo x &gt; {effect}</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
"""
_APP_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>probe</string>
<key>CFBundleIdentifier</key><string>{bundle_id}</string>
<key>LSUIElement</key><true/>
</dict></plist>
"""


@dataclass(frozen=True)
class ProbeTargets:
    """Что проба обязана НЕ суметь записать (§4 п.1, §10 п.3, §15).

    `run_dir` — каталог прогона (часовые в нём — рядом с `ledger.jsonl` — и в
    `run_dir/edge-check/`); `extra` — прочие каталоги (venv disputatio;
    `governance/` и корень при цели «сам devtools»). Все каталоги должны
    существовать: `ENOENT` часового — сбой подготовки, а не защита.
    """

    target_dir: Path
    bundle_dir: PurePosixPath
    git_dir: Path
    common_dir: Path
    run_dir: Path
    home: Path
    extra: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    """Исход пробы: `failures` пусты — песочница действует (§15).

    Каждая строка `failures` — «пункт: исход»; `notes` — исходы, которые
    отказом не являются, но названы (Apple Events «нет ответа», §17 п.7).
    """

    failures: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def _wrap(profile_path: Path) -> list[str]:
    """Обёртка запуска под профилем — та же, что у агента (TASK-005)."""
    return [SANDBOX_EXEC, "-f", str(profile_path)]


def _spawn(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: Path, timeout: float
) -> tuple[int | None, bytes]:
    """Новая сессия, `stdin` из `/dev/null`; по выходу/таймауту — `SIGKILL`
    группе (гигиена, §12). Код `None` — таймаут."""
    proc = subprocess.Popen(
        list(argv),
        env=dict(env),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        out, code = b"", None
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if code is None:
        proc.communicate()
    return code, out


def _git(*args: str | Path) -> None:
    proc = subprocess.run(
        ["git", *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **_GIT_ENV},
    )
    if proc.returncode != 0:
        raise SandboxError(f"стенд пробы: git {args[0]}: {proc.stderr.strip()}")


#: Разрушающие пробы файла `.git` worktree — каждой свой worktree, иначе
#: первая удавшаяся (`unlink`) маскирует остальные под `ENOENT`.
_STAND_WORKTREES = ("unlink", "rename", "replace", "link")


def _build_stand(root: Path) -> dict[str, str]:
    """Одноразовый git-репо + worktree вне разрешённых корней (§15)."""
    main = root / "main"
    ident = ("-c", "user.name=probe", "-c", "user.email=probe@localhost")
    _git("init", "-q", main)
    _git("-C", main, *ident, "commit", "-q", "--allow-empty", "-m", "stand")
    stand = {
        "config": str(main / ".git" / "config"),
        "hook": str(main / ".git" / "hooks" / "probe-hook"),
        "common_dir": str(main / ".git"),
    }
    for name in _STAND_WORKTREES:
        wt = root / f"wt-{name}"
        _git("-C", main, "worktree", "add", "-q", "--detach", wt)
        stand[f"gitfile_{name}"] = str(wt / ".git")
    gitfile = Path(stand["gitfile_link"]).read_text()
    gitdir = Path(gitfile.split("gitdir:", 1)[1].strip())
    stand["gitdir"] = str(gitdir)
    stand["gitdir_head"] = str(gitdir / "HEAD")
    return stand


def _sentinels(targets: ProbeTargets, stand: Mapping[str, str]) -> dict[str, str]:
    """Имена часовых: уникальные, в каждом запретном каталоге (§15)."""
    tag = f".sandbox-sentinel-{uuid.uuid4().hex}"
    dirs: dict[str, Path] = {
        "run_dir/edge-check": targets.run_dir / "edge-check",
        "run_dir": targets.run_dir,
        "home": targets.home,
        "target_dir": targets.target_dir,
        "bundle_dir": targets.target_dir / targets.bundle_dir,
        "gitdir": targets.git_dir,
        "common dir": targets.common_dir,
        "stand gitdir": Path(stand["gitdir"]),
        "stand common dir": Path(stand["common_dir"]),
    }
    dirs.update({f"extra:{p}": p for p in targets.extra})
    by_path: dict[str, list[str]] = {}
    for name, path in dirs.items():
        by_path.setdefault(os.path.realpath(path), []).append(name)
    # один каталог под двумя именами (gitdir = common dir у цели без worktree)
    # — один часовой, иначе второй получил бы EEXIST
    return {" = ".join(names): str(Path(p) / tag) for p, names in by_path.items()}


#: Пункты скрипта пробы помимо часовых; исход обязан быть у каждого —
#: пропавший пункт (а не только удавшийся) — отказ.
PROBE_ITEMS = frozenset(
    {
        "stand:unlink-gitfile",
        "stand:rename-gitfile",
        "stand:replace-gitfile",
        "stand:config",
        "stand:hook",
        "stand:gitdir-write",
        "stage:nested-git-dir",
        "stage:nested-gitfile",
        "link:outside",
        "link:gitfile",
        "move:outside",
        "symlink:runner-made",
        "symlink:agent-made",
        "read:bait",
        "tty:open",
        "allow:stage",
        "allow:tmp",
        *(f"keychain:{i}" for i in range(len(KEYCHAIN_PROBES))),
        "keychain:canary",
        "launchd:submit",
        "launchd:bootstrap",
        "cron:exec",
        "launchservices:open",
        "apple-events",
    }
)


def _evaluate(
    out: Mapping[str, str],
    expected: frozenset[str],
    env_ok: str | None,
    effects: Sequence[str],
) -> ProbeResult:
    failures: list[str] = [
        f"{name}: исхода нет — пункт не исполнен"
        for name in sorted(expected - set(out))
    ]
    notes: list[str] = []
    for name, got in out.items():
        if name.startswith("allow:"):
            if got != "ok":
                failures.append(f"{name}: базовая половина не прошла ({got})")
        elif name == "tty:open":
            if got not in _TTY_DENIED:
                failures.append(f"{name}: {got}")
        elif name == "apple-events":
            if got == "timeout":
                notes.append("apple-events: нет ответа (закрытие не доказано, §17)")
            elif got == "rc=0":
                failures.append(f"{name}: ответили — не действует")
        elif name == "cron:exec":
            # `crontab -l` без crontab тоже ненулевой — засчитан только
            # запрет исполнения (F20)
            if got != _DENIED:
                failures.append(f"{name}: {got} — не действует")
        elif name.split(":")[0] in ("keychain", "launchd", "launchservices"):
            if got.startswith("prep:"):
                failures.append(f"{name}: {got[5:]} — сбой подготовки")
            elif got == "rc=0" or got == "timeout":
                failures.append(f"{name}: {got} — не действует")
        elif got == "ok":
            failures.append(f"{name}: удалось — регрессия профиля")
        elif got != _DENIED:
            failures.append(f"{name}: {got} — сбой подготовки")
    if env_ok is not None:
        failures.append(env_ok)
    failures.extend(f"эффект вне песочницы: {e}" for e in effects)
    return ProbeResult(tuple(failures), tuple(notes))


def _env_mismatch(code: int | None, raw: bytes, env: Mapping[str, str]) -> str | None:
    """Окружение внутри — ровно `env` вызова (§4 п.4); `None` — равно."""
    if code != 0:
        return f"env: проба окружения не прошла (код {code})"
    inside = {item.split(b"=", 1)[0].decode() for item in raw.split(b"\0") if item}
    if inside == set(env):
        return None
    extra, missing = sorted(inside - set(env)), sorted(set(env) - inside)
    return f"env: лишние {extra}, недостающие {missing}"


def _app_running(bundle_id: str) -> bool:
    """Пробный `.app` запущен LaunchServices — то есть вне песочницы (F21)."""
    proc = subprocess.run(
        [_LSAPPINFO, "find", f"bundleID={bundle_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def _keychain(*args: str) -> int:
    proc = subprocess.run(
        ["/usr/bin/security", *args],
        capture_output=True,
        check=False,
        timeout=PROBE_STEP_TIMEOUT,
    )
    return proc.returncode


def _label_alive(label: str) -> bool:
    proc = subprocess.run(
        ["/bin/launchctl", "list", label], capture_output=True, check=False
    )
    return proc.returncode == 0


def _cleanup(
    call: CallDir,
    sentinels: Mapping[str, str],
    labels: Sequence[str],
    bait: Path,
    canary: str,
) -> None:
    # гигиена: процессы, запущенные из каталога вызова вне песочницы (.app
    # при сорванной пробе стартует с задержкой ~2 с — что успели)
    subprocess.run(["/usr/bin/pkill", "-9", "-f", str(call.root)], check=False)
    try:
        _keychain("delete-generic-password", "-s", canary)
    except subprocess.TimeoutExpired:
        pass
    for path in sentinels.values():
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    domain = f"gui/{os.getuid()}"
    for label in labels:
        for argv in (["remove", label], ["bootout", f"{domain}/{label}"]):
            subprocess.run(["/bin/launchctl", *argv], capture_output=True, check=False)
    try:
        os.unlink(bait)
    except FileNotFoundError:
        pass
    for sub in (call.stage, call.tmp, call.claude_config):
        for child in os.listdir(sub):
            target = sub / child
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    for name in (
        "stand",
        "effects",
        "probe.py",
        "probe.json",
        "outside.txt",
        "outside-mv.txt",
    ):
        target = call.root / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()


def run_probe(
    call: CallDir,
    profile: str,
    env: Mapping[str, str],
    targets: ProbeTargets,
    *,
    timeout: float = PROBE_TIMEOUT,
) -> ProbeResult:
    """Проба-канарейка §4/§15 под обёрткой `profile` с окружением `env`.

    До пробы раннер создаёт каталоги бандла (`safe_io.ensure_dirs`, N6),
    пишет `<call>/profile.sb` (остаётся для запуска агента), одноразовый
    стенд и внешние образцы в `<call>` (агенту не пишется), приманку
    `~/.config/ai-prosto/.sandbox-bait`. Следы убираются при любом исходе.
    """
    safe_io.ensure_dirs(targets.target_dir, targets.bundle_dir)
    safe_io.publish(call.root, "profile.sb", profile.encode())
    profile_path = call.root / "profile.sb"
    labels = (
        f"dev.devtools.sandbox-probe.{uuid.uuid4().hex}",
        f"dev.devtools.sandbox-probe.{uuid.uuid4().hex}",
    )
    bait = targets.home / BAIT
    canary = f"{KEYCHAIN_CANARY}-{uuid.uuid4().hex}"
    app_id = labels[0] + ".app"
    sentinels: dict[str, str] = {}
    try:
        secret = uuid.uuid4().hex
        if _keychain("add-generic-password", "-s", canary, "-a", "probe", "-w", secret):
            return ProbeResult(
                ("keychain:canary: элемент не создан — сбой подготовки",), ()
            )
        stand = _build_stand(call.root / "stand")
        sentinels = _sentinels(targets, stand)
        effects = call.root / "effects"
        effects.mkdir()
        for name in ("outside.txt", "outside-mv.txt"):
            safe_io.publish(call.root, name, b"outside\n")
        os.symlink(call.root / "outside.txt", call.stage / ".runner-link")
        bait.parent.mkdir(parents=True, exist_ok=True)
        bait.write_text("bait\n")
        cfg = {
            "sentinels": sentinels,
            "stand": stand,
            "outside": str(call.root / "outside.txt"),
            "outside_mv": str(call.root / "outside-mv.txt"),
            "runner_link": str(call.stage / ".runner-link"),
            "stage": str(call.stage),
            "tmp": str(call.tmp),
            "bait": str(bait),
            "keychain": [list(a) for a in KEYCHAIN_PROBES],
            "keychain_canary": canary,
            "step_timeout": PROBE_STEP_TIMEOUT,
            "effects": str(effects),
            "label_submit": labels[0],
            "plist": _PLIST.format(label=labels[1], effect=effects / "bootstrap"),
            "app_plist": _APP_PLIST.format(bundle_id=app_id),
            "ae_timeout": APPLE_EVENTS_TIMEOUT,
        }
        safe_io.publish(call.root, "probe.json", json.dumps(cfg).encode())
        safe_io.publish(call.root, "probe.py", PROBE_SCRIPT.encode())
        wrap = _wrap(profile_path)
        argv = [sys.executable, "-I", "-B", str(call.root / "probe.py")]
        code, raw = _spawn(
            [*wrap, *argv, str(call.root / "probe.json")],
            env=env,
            cwd=targets.target_dir,
            timeout=timeout,
        )
        if code is None:
            return ProbeResult(("проба: таймаут — не действует",), ())
        lines = raw.decode(errors="replace").strip().splitlines()
        try:
            out = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            out = {}
        if code != 0 or not out:
            return ProbeResult((f"проба: код {code}, исходов нет",), ())
        env_code, env_raw = _spawn(
            [*wrap, "/usr/bin/env", "-0"], env=env, cwd=targets.target_dir, timeout=10
        )
        time.sleep(0.5)  # эффект запущенного вне песочницы — доп. условие
        seen = sorted(os.listdir(effects))
        seen += [f"launchctl {label}" for label in labels if _label_alive(label)]
        if _app_running(app_id):
            seen.append(f"lsappinfo {app_id}")
        outside_changed = [
            n
            for n in ("outside.txt", "outside-mv.txt")
            if not (call.root / n).is_file()
            or (call.root / n).read_bytes() != b"outside\n"
        ]
        expected = PROBE_ITEMS | {f"sentinel:{name}" for name in sentinels}
        result = _evaluate(out, expected, _env_mismatch(env_code, env_raw, env), seen)
        if outside_changed:
            result = ProbeResult(
                (*result.failures, f"внешний файл изменён: {outside_changed}"),
                result.notes,
            )
        return result
    finally:
        _cleanup(call, sentinels, labels, bait, canary)


# --- процедура допуска `make sandbox-admit` (§14, TASK-006) ------------------
#
# Исполняет оператор с реальным харнессом: кандидат запускается под профилем
# §12 на одноразовой цели, исходы берутся из СЫРЫХ `tool_result` его
# `stream-json`, не из пересказа модели. Итог — протокол в `docs/evidence/` и
# ПРЕДЛОЖЕННАЯ запись допуска в stdout; `contracts/` процедура не пишет —
# запись вносит оператор шагом H2. Не прошло — запись не предлагается;
# расширение профиля — изменение дизайна, не строка контракта.
#
# Форма `stream-json` снята с claude 2.1.296 (замер 2026-10-12): Bash с
# ненулевым кодом — `is_error: true`, content `Exit code N`; `cd` за пределы
# cwd харнесс откатывает («Shell cwd was reset»), поэтому сохранение `cd`
# проверяется подкаталогом цели.

ADMIT_TIMEOUT = 900.0
ADMIT_NODE = "a/b/node.md"
ADMIT_PROMPT = """\
Run exactly these tool calls, in this order, one call each, and nothing else:
1) Bash: true
2) Bash: false
3) Bash: exit 3
4) Bash: cd src
5) Bash: pwd
6) Write the file {node} with content: admit-probe
7) Write the file {escape} with content: x
Then reply DONE.
"""


@dataclass(frozen=True)
class ToolCall:
    """Вызов инструмента и его сырой результат из `stream-json`."""

    tool: str
    input: Mapping[str, Any]
    is_error: bool
    content: str


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def parse_stream_json(lines: Sequence[str]) -> list[ToolCall]:
    """Пары `tool_use` → `tool_result` в порядке вызовов; без результата —
    `is_error` и пустой content (исход не доказан)."""
    uses: dict[str, tuple[str, Mapping[str, Any]]] = {}
    order: list[str] = []
    results: dict[str, tuple[bool, str]] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                uses[part["id"]] = (part.get("name", ""), part.get("input") or {})
                order.append(part["id"])
            elif part.get("type") == "tool_result":
                results[part["tool_use_id"]] = (
                    bool(part.get("is_error")),
                    _text(part.get("content")),
                )
    return [ToolCall(*uses[i], *results.get(i, (True, ""))) for i in order]


def admit_expectations(
    node: str, escape: str, subdir: str
) -> tuple[tuple[str, str, bool, str | None], ...]:
    """(инструмент, команда/путь, ждём ошибку, ждём content) — N2, F24/F26."""
    return (
        ("Bash", "true", False, None),
        ("Bash", "false", True, "Exit code 1"),
        ("Bash", "exit 3", True, "Exit code 3"),
        ("Bash", "cd src", False, None),
        ("Bash", "pwd", False, subdir),
        ("Write", node, False, None),
        ("Write", escape, True, None),
    )


def _key(call: ToolCall) -> str:
    raw = call.input.get("command" if call.tool == "Bash" else "file_path", "")
    return str(raw).strip()


def check_transcript(
    calls: Sequence[ToolCall],
    expectations: Sequence[tuple[str, str, bool, str | None]],
) -> list[str]:
    """Отказы протокола: шаг не исполнен, код/ошибка/вывод не те.

    Лишний вызов, который УДАЛСЯ, — тоже отказ: он мог подменить проверяемую
    способность (`mkdir -p` вместо Write с созданием родителей). Неудавшиеся
    лишние вызовы (повтор запрещённой записи) — примечание (`extra_calls`)."""
    failures: list[str] = []
    left = list(calls)
    for tool, key, want_error, want in expectations:
        match = next((c for c in left if c.tool == tool and _key(c) == key), None)
        name = f"{tool} {key}"
        if match is None:
            failures.append(f"{name}: шаг не исполнен")
            continue
        left.remove(match)
        if match.is_error != want_error:
            failures.append(
                f"{name}: is_error={match.is_error}, ждали {want_error} "
                f"({match.content.strip()[:200]!r})"
            )
        elif want is not None and match.content.strip() != want:
            failures.append(
                f"{name}: вывод {match.content.strip()[:200]!r}, ждали {want!r}"
            )
    failures += [
        f"лишний удавшийся вызов: {c.tool} {_key(c)[:120]}"
        for c in left
        if not c.is_error
    ]
    return failures


def extra_calls(
    calls: Sequence[ToolCall],
    expectations: Sequence[tuple[str, str, bool, str | None]],
) -> list[str]:
    """Неудавшиеся вызовы сверх ожидаемых — примечание протокола."""
    left = list(calls)
    for tool, key, _, _ in expectations:
        match = next((c for c in left if c.tool == tool and _key(c) == key), None)
        if match is not None:
            left.remove(match)
    return [
        f"лишний неудавшийся вызов: {c.tool} {_key(c)[:120]}"
        for c in left
        if c.is_error
    ]


def surface(paths: Sequence[Path]) -> set[str]:
    """Имена первого уровня наблюдаемых каталогов (`$HOME`, `/private/tmp`)."""
    out: set[str] = set()
    for root in paths:
        try:
            out |= {str(root / name) for name in os.listdir(root)}
        except OSError:
            out.add(f"{root} (не читается)")
    return out


def admission_record(admission: Admission) -> str:
    """Строка `[[admitted]]` для `admitted.toml` — вносит оператор (H2)."""
    return (
        "[[admitted]]\n"
        f'harness = "{admission.harness}"\n'
        f'version = "{admission.version}"\n'
        f'realpath = "{admission.realpath}"\n'
        f'sha256 = "{admission.sha256}"\n'
        f"macos_major = {admission.macos_major}\n"
        f'profile_sha256 = "{admission.profile_sha256}"\n'
        f'evidence = "{admission.evidence}"\n'
    )


def render_admit_protocol(
    admission: Admission,
    *,
    date: str,
    probe: ProbeResult,
    failures: Sequence[str],
    calls: Sequence[ToolCall],
    notes: Sequence[str] = (),
) -> str:
    """Протокол допуска для `docs/evidence/` — факты, не пересказ модели."""
    verdict = "ПРОШЛО — запись допуска предложена" if not failures else "НЕ ПРОШЛО"
    rows = "\n".join(
        f"| {c.tool} | `{_key(c)[:80]}` | {c.is_error} | `{c.content.strip()[:80]}` |"
        for c in calls
    )
    lines = [
        f"# Допуск харнесса под песочницей авторинга — {date}",
        "",
        (
            "Процедура `make sandbox-admit` (дизайн "
            "`docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md` "
            "§14; tasks-спека TASK-006)."
        ),
        "",
        f"- харнесс: {admission.harness} {admission.version}",
        f"- realpath: `{admission.realpath}`",
        f"- sha256: `{admission.sha256}`",
        f"- macOS: {admission.macos_major}",
        f"- шаблон профиля sha256: `{admission.profile_sha256}`",
        f"- проба §15: {'зелёная' if probe.ok else 'красная'}"
        + "".join(f"\n  - {item}" for item in (*probe.failures, *probe.notes)),
        "",
        f"**Итог: {verdict}.**",
        "",
        "## Сырые `tool_result` (stream-json)",
        "",
        "| инструмент | вход | is_error | content |",
        "|---|---|---|---|",
        rows,
        "",
    ]
    if notes:
        lines += ["## Примечания", "", *(f"- {n}" for n in notes), ""]
    if failures:
        lines += ["## Отказы", "", *(f"- {f}" for f in failures), ""]
    else:
        lines += [
            "## Запись допуска (вносит оператор, шаг H2)",
            "",
            "```toml",
            admission_record(admission).rstrip(),
            "```",
            "",
        ]
    return "\n".join(lines)


def _admit_stand(base: Path) -> tuple[Path, Path]:
    """Одноразовая цель (git, `src/`, бандл с узлом) и каталог прогона."""
    target = base / "target"
    _git("init", "-q", target)
    (target / "src").mkdir()
    (target / "ws" / "spec").mkdir(parents=True)
    (target / "ws" / "spec" / "00-charter.md").write_text("# charter\n")
    run_dir = base / "run"
    (run_dir / "edge-check").mkdir(parents=True)
    (run_dir / "ledger.jsonl").write_text("")
    return target, run_dir


def admit(
    *, harness_path: str | None, model: str, evidence_dir: Path, date: str
) -> int:
    """`make sandbox-admit`: 0 — прошло, запись допуска в stdout; 1 — не
    прошло (запись не предлагается); 2 — процедуру не исполнить.

    Кандидат фиксируется (`realpath`, sha256, macOS) ДО любого запуска.
    Красная проба §15 останавливает процедуру до запуска харнесса — как и
    граница (§15: регрессия профиля — вызов не делается). Протокол пишется
    в `evidence_dir` при любом исходе исполненной процедуры (коды 0 и 1);
    код 2 — протокола нет.
    """
    # модули ЗЦ: граница импортирует этот модуль, поэтому — в функции
    from governance import author_launch

    if sys.platform != "darwin" or not os.path.exists(SANDBOX_EXEC):
        print("sandbox-admit: только macOS с /usr/bin/sandbox-exec", file=sys.stderr)
        return 2
    found = harness_path or shutil.which("claude")
    if not found:
        print("sandbox-admit: claude не найден в PATH", file=sys.stderr)
        return 2
    real = os.path.realpath(found)
    try:
        sha256, major = _file_sha256(real), _macos_major()
    except SandboxError as exc:
        print(f"sandbox-admit: кандидат не зафиксирован: {exc}", file=sys.stderr)
        return 2
    base = Path(os.path.realpath(tempfile.mkdtemp(prefix="devtools-admit-")))
    call: CallDir | None = None
    version, failures, notes, calls = "unknown", [], [], []
    try:
        target, run_dir = _admit_stand(base)
        call = create_call_dir(
            calls_root(),
            f"admit-{uuid.uuid4().hex[:12]}",
            forbidden=forbidden_roots(
                target, run_dir=run_dir, devtools_root=author_launch.DEVTOOLS_ROOT
            ),
        )
        env = author_launch.call_env(call, author_launch.author_claude_token())
        git_dir, common_dir = target_git_dirs(target)
        probe = run_probe(
            call,
            render_profile(call, home=Path.home()),
            env,
            ProbeTargets(
                target,
                PurePosixPath("ws/spec"),
                git_dir,
                common_dir,
                run_dir,
                Path.home(),
            ),
        )
        failures = [f"проба §15: {f} — харнесс не запускался" for f in probe.failures]
        if probe.ok:
            version, failures, notes, calls = _admit_run(call, target, real, model, env)
    except (SandboxError, safe_io.SafeIOError, OSError) as exc:
        print(f"sandbox-admit: процедура не исполнена: {exc}", file=sys.stderr)
        return 2
    except author_launch.AgentIsolationError as exc:
        print(f"sandbox-admit: {exc}", file=sys.stderr)
        return 2
    finally:
        if call is not None:
            shutil.rmtree(call.root, ignore_errors=True)
            try:
                os.rmdir(call.root.parent)  # `<calls>/admit-…` — свой, пустой
            except OSError:
                pass
        shutil.rmtree(base, ignore_errors=True)
    name = f"{date}-author-sandbox-admit-{version}.md"
    admission = Admission(
        "claude",
        version,
        Path(real),
        sha256,
        major,
        profile_template_sha256(),
        f"docs/evidence/{name}",
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / name).write_text(
        render_admit_protocol(
            admission,
            date=date,
            probe=probe,
            failures=failures,
            calls=calls,
            notes=notes,
        ),
        encoding="utf-8",
    )
    print(f"протокол: {evidence_dir / name}", file=sys.stderr)
    if failures:
        print("sandbox-admit: НЕ ПРОШЛО — запись не предлагается:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(admission_record(admission), end="")
    return 0


def _admit_run(
    call: CallDir, target: Path, real: str, model: str, env: Mapping[str, str]
) -> tuple[str, list[str], list[str], list[ToolCall]]:
    """Харнесс под профилем после зелёной пробы: (версия, отказы,
    примечания, вызовы инструментов)."""
    from governance import author_launch

    safe_io.publish(
        call.root,
        "claude-config/settings.json",
        author_launch.ISOLATED_CLAUDE_SETTINGS.encode(),
    )
    wrap = _wrap(call.root / "profile.sb")
    failures: list[str] = []
    code, out = _spawn([*wrap, real, "--version"], env=env, cwd=target, timeout=60)
    words = out.decode(errors="replace").split()
    version = words[0] if code == 0 and words else "unknown"
    if version == "unknown":
        failures.append(f"версия кандидата под профилем не получена (код {code})")
    node, escape = call.stage / ADMIT_NODE, target / "ws" / "spec" / "escape.md"
    watched = (Path.home(), Path("/private/tmp"))
    before = surface(watched)
    code, raw = _spawn(
        [
            *wrap,
            real,
            "-p",
            "--model",
            model,
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--output-format",
            "stream-json",
            "--verbose",
            ADMIT_PROMPT.format(node=node, escape=escape),
        ],
        env=env,
        cwd=target,
        timeout=ADMIT_TIMEOUT,
    )
    new = sorted(surface(watched) - before)
    calls = parse_stream_json(raw.decode(errors="replace").splitlines())
    expectations = admit_expectations(str(node), str(escape), str(target / "src"))
    if code != 0:
        failures.append(f"харнесс вернул {code}")
    failures += check_transcript(calls, expectations)
    if not node.is_file() or node.read_text().strip() != "admit-probe":
        failures.append(f"файл {ADMIT_NODE} в заготовке не записан")
    if os.path.lexists(escape):
        failures.append("запись в дерево цели удалась")
    if new:
        failures.append(f"вне каталога вызова появилось: {new}")
    return version, failures, extra_calls(calls, expectations), calls


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m governance.sandbox admit [--harness-path P] [--model M]`."""
    import argparse
    import datetime

    parser = argparse.ArgumentParser(prog="governance.sandbox")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("admit", help="процедура допуска харнесса (§14)")
    cmd.add_argument("--harness-path", default=None)
    cmd.add_argument("--model", default="claude-opus-5")
    cmd.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "docs" / "evidence",
    )
    args = parser.parse_args(argv)
    return admit(
        harness_path=args.harness_path,
        model=args.model,
        evidence_dir=args.evidence_dir,
        date=datetime.date.today().isoformat(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
