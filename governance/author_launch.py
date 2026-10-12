"""Граница запуска авторского агента — единственное место его порождения.

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§11–§15, §19 п.1; tasks-спека `spec/author-agent-sandbox-tasks.md` TASK-005.
`RealOps.author` и `RealOps.author_disp` только делегируют сюда
(`launch_author`, `refuse_disp`); это закрепляет тест инварианта
`tests/test_author_sandbox_invariant.py`.

Порядок внутри `launch_author` — до создания каталога вызова и до платного
вызова: ОС, `sandbox-exec`, вложенность, харнесс (= claude, §13 Q1), допуск
исполняемого файла (§14), элемент связки с токеном, `bundle_dir` (§15);
затем каталог вызова и профиль (§12), проба (§15) — перед КАЖДЫМ запуском, с
тем же профилем и env; затем запуск и перенос. Любой отказ — исход с
причиной, агент не запущен; флага, переменной или режима обхода нет.

Граница гарантий (§19 п.3): обязательная гарантия v1 — против авторского
агента во время вызова и его потомков. От намеренного обхода границы через
незащищённый код раннера модуль не защищает — это остаточный риск ревью PR.

Модуль — в защищённой цепочке песочницы (authority-root, шаг H1); импортирует
только stdlib и модули цепочки.
"""

from __future__ import annotations

import dataclasses
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from governance import safe_io, sandbox

DEVTOOLS_ROOT = Path(__file__).resolve().parent.parent
#: Контракт допуска исполняемых файлов харнесса (§14; запись — шаг H2).
ADMITTED_CONTRACT = (
    DEVTOOLS_ROOT / "contracts" / "author-sandbox" / "v1" / "admitted.toml"
)

# --- харнесс авторинга -------------------------------------------------------

# Лимиты codex, 2026-09-03; парный слой к харнессу ревьюера в review-pr.sh.
# Выбор: env AUTHOR_HARNESS/AUTHOR_MODEL > ~/.config/ai-prosto/harness.env >
# вшитый codex. Модель привязана к слою своего харнесса (урок ревью PR #121):
# харнесс со слоя выше не наследует модель слоя ниже. Файл ПАРСИТСЯ
# (KEY=VALUE, export/отступы терпимы), не исполняется. Вшитое умолчание этим
# срезом НЕ меняется (§13): подставить claude там, где выбран codex, — та
# самая автоматическая замена, которую решение владельца запрещает.
_HARNESS_ENV_KEYS = (
    "AUTHOR_HARNESS",
    "AUTHOR_MODEL",
    "REVIEW_HARNESS",
    "REVIEW_MODEL",
)
#: Распознаваемые имена харнесса (корректность конфигурации, §19 п.1).
KNOWN_HARNESSES = ("claude", "codex")
#: Единственный харнесс авторинга под песочницей v1 (§13, Q1 = (а)).
SUPPORTED_HARNESS = "claude"
#: Авторинг-бэкенды прогона: `codex` — `Ops.author`, `disp` — вне v1 (§11).
KNOWN_BACKENDS = ("codex", "disp")
DEFAULT_CLAUDE_MODEL = "claude-opus-5"


def harness_env_values() -> dict[str, str]:
    """`*_HARNESS`/`*_MODEL`-строки операторского harness.env; нет файла — пусто."""
    path = Path(
        os.environ.get(
            "AI_PROSTO_HARNESS_ENV",
            str(Path.home() / ".config" / "ai-prosto" / "harness.env"),
        )
    )
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        for key in _HARNESS_ENV_KEYS:
            if line.startswith(f"{key}="):
                values[key] = line[len(key) + 1 :]
    return values


def harness_for(prefix: str) -> tuple[str, str]:
    """(harness, model) слоя `<prefix>_HARNESS`/`<prefix>_MODEL`.

    env > harness.env > вшитый codex; модель привязана к слою, из которого
    пришёл харнесс (env-харнесс НЕ наследует модель файла).
    """
    cfg = harness_env_values()
    env_harness = os.environ.get(f"{prefix}_HARNESS", "")
    harness = env_harness or cfg.get(f"{prefix}_HARNESS", "") or "codex"
    if env_harness:
        model = os.environ.get(f"{prefix}_MODEL", "")
    else:
        model = os.environ.get(f"{prefix}_MODEL", "") or cfg.get(f"{prefix}_MODEL", "")
    return harness, model


def harness_refusal(harness: str) -> str:
    """Текст отказа §13 для харнесса, отличного от claude (дословно)."""
    return (
        f"харнесс `{harness}` не поддерживается песочницей авторинга v1; "
        f"поддерживается claude (`AUTHOR_HARNESS={SUPPORTED_HARNESS}`)"
    )


# --- окружение агента --------------------------------------------------------

#: Переменные окружения, которые получает авторский агент (дизайн §3, F12):
#: env вызова собирается allowlist'ом, а не наследуется — в сессии оператора
#: десяток секретов (API-ключи, GH_TOKEN, токены ботов), и наследование
#: отдавало их каждому агенту. Замерено вживую 2026-09-29. Каждое имя — с
#: обоснованием:
AGENT_ENV_NAMES = frozenset(
    {
        # базовое: поиск бинарей, домашний каталог (конфиги харнессов)
        "PATH",
        "HOME",
        # без USER claude считает себя незалогиненным (замер I3, reviewer.py);
        # LOGNAME — POSIX-имя входа, запасной источник у git/getpass
        "USER",
        "LOGNAME",
        # Bash-инструмент claude берёт оболочку из SHELL; TERM — режим вывода
        "SHELL",
        "TERM",
        # свой TMPDIR харнесса (под песочницей — `<call>/tmp`, §12); кодировка
        # вывода (LC_* — префиксом ниже)
        "TMPDIR",
        "LANG",
        # базы XDG — пути, не секреты; поимённо, не префиксом: будущая
        # XDG_-переменная сама к агенту не попадёт
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        # транспорт модели claude — тот же набор, что у ревьюера edge-check
        # (`edge_check/reviewer.py`): учётка модели и облака — СВОИ учётные
        # данные агента (дизайн §3: видимость своей модельной учётки принята
        # для v1); CLAUDE_CODE_OAUTH_TOKEN — из отдельного элемента связки
        # ключей (§3), унаследованный перекрывается
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "MAX_THINKING_TOKENS",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "CLOUD_ML_REGION",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        # корпоративные сети: без прокси/CA транспорт до модели не достучится
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
    }
)
# OPENAI_API_KEY НАМЕРЕННО вне перечня: codex аутентифицируется через
# `~/.codex/auth.json` (вход ChatGPT), а ключ из env переключил бы его на
# оплату API — молча, при том что ключ в сессии оператора выставлен.
#: LC_* — семейство POSIX-локали (LC_ALL, LC_CTYPE, …): только имена локалей.
AGENT_ENV_PREFIXES = ("LC_",)
#: CA-набор системы: сеть харнесса при закрытой связке ключей (§8/S2, §12).
SSL_CERT_FILE = "/etc/ssl/cert.pem"


def agent_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Окружение авторского агента — allowlist из `environ` (по умолчанию
    текущего процесса); всё прочее, включая секреты, не передаётся."""
    source = os.environ if environ is None else environ
    return {
        name: value
        for name, value in source.items()
        if name in AGENT_ENV_NAMES or name.startswith(AGENT_ENV_PREFIXES)
    }


def call_env(call: sandbox.CallDir, token: str) -> dict[str, str]:
    """env вызова под песочницей (§12): allowlist + корни каталога вызова."""
    return {
        **agent_env(),
        "TMPDIR": str(call.tmp),
        "CLAUDE_CODE_TMPDIR": str(call.tmp),
        "CLAUDE_CONFIG_DIR": str(call.claude_config),
        "CLAUDE_CODE_OAUTH_TOKEN": token,
        # операторский CA (корпоративный прокси) сильнее; системный набор —
        # умолчание при закрытой связке ключей (§8/S2)
        "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE") or SSL_CERT_FILE,
    }


#: Элемент связки ключей с долгоживущим токеном claude для авторских вызовов
#: (дизайн §3, §8/S1). Раннер читает его сам; агент получает токен только
#: через env вызова — не из унаследованного окружения.
AUTHOR_CLAUDE_TOKEN_ITEM = "devtools-author-claude-oauth"
#: settings.json изолированного каталога конфигурации: разрешения — как у
#: `--dangerously-skip-permissions` авторского вызова.
ISOLATED_CLAUDE_SETTINGS = '{"permissions": {"defaultMode": "bypassPermissions"}}\n'


class AgentIsolationError(RuntimeError):
    """Изоляцию авторского агента обеспечить нельзя — вызов не делается."""


def author_claude_token() -> str:
    """Токен из связки ключей; нет элемента — AgentIsolationError."""
    done = subprocess.run(
        ["security", "find-generic-password", "-s", AUTHOR_CLAUDE_TOKEN_ITEM, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    token = done.stdout.strip()
    if done.returncode != 0 or not token:
        raise AgentIsolationError(
            f"нет элемента связки ключей {AUTHOR_CLAUDE_TOKEN_ITEM!r} с токеном "
            "claude (создайте `claude setup-token` + `security "
            "add-generic-password` в отдельном терминале) — авторский вызов "
            "claude без изоляции конфигурации не делается"
        )
    return token


def author_argv(binary: str, model: str, prompt: str) -> list[str]:
    """argv авторского вызова claude; `binary` — абсолютный путь из допуска.

    Паритет с флотским пресетом spec-runner (`--dangerously-skip-permissions`):
    запись ограничивает профиль §12, а не разрешения харнесса.
    `--strict-mcp-config`/`--no-session-persistence` — не тащить MCP оператора
    и не сорить сессиями.
    """
    return [
        binary,
        "-p",
        "--model",
        model or DEFAULT_CLAUDE_MODEL,
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
        prompt,
    ]


# --- контекст и исход вызова ------------------------------------------------


@dataclass(frozen=True)
class AuthorCall:
    """Неизменяемый контекст авторского вызова — только простые типы.

    `run_dir` — канонический (`realpath`) каталог прогона; `filename` — имя
    файла узла внутри `bundle_dir`. Раннер передаёт его через `Ops.author`;
    модуль не импортирует `run_state`.
    """

    run_id: str
    run_dir: str
    target_dir: str
    bundle_dir: str
    filename: str


@dataclass(frozen=True)
class LaunchOutcome:
    """Исход границы: `code` 0 — файл узла опубликован в бандл.

    `reason` — причина отказа или стопа; `quarantine` — перечень лишнего в
    заготовке (сохраняет раннер, §15 п.1).
    """

    code: int
    reason: str = ""
    quarantine: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code == 0


#: Код исхода «граница отказала до запуска агента» — как прежний код
#: ошибки конфигурации авторинга (шаг остаётся resumable).
REFUSED = 2
#: Таймаут вызова агента (§12: обязателен; истёк — SIGKILL группе).
AUTHOR_TIMEOUT = 3600.0
#: Потолок файла узла при переносе (§15).
NODE_LIMIT = 4 << 20
NODE_READ_TIMEOUT = 10.0
#: Транскрипт вызова (stream-json): каталог в `run_dir` — вне записи агента
#: (`run_dir` — запретный корень §12 п.2); потолок хранимых байт — сверх
#: него вывод дочитывается и отбрасывается (`truncated`); после выхода агента
#: канал дочитывается не дольше `TRANSCRIPT_GRACE` (держатель канала,
#: ушедший из группы, — `complete: false`).
TRANSCRIPTS = "author-transcripts"
TRANSCRIPT_LIMIT = 32 << 20
TRANSCRIPT_GRACE = 5.0
_READ_CHUNK = 1 << 16
_PUMP_TICK = 0.5
DISP_REFUSAL = (
    "авторинг-бэкенд disp вне v1 песочницы авторинга: его координатор "
    "обязан писать в .git цели (дизайн §11). Этот прогон: уберите каталог "
    "пайплайна `.disputatio/pipelines/<slug>` цели (если есть), положите "
    "файл узла руками и повторите resume — файл будет принят как есть. "
    "Новый прогон: author_backend=codex с AUTHOR_HARNESS=claude"
)


def _refuse(reason: str) -> LaunchOutcome:
    return LaunchOutcome(REFUSED, reason)


def refuse_disp() -> LaunchOutcome:
    """disp — всегда отказ «disp вне v1» (§11, §13); агент не запускается."""
    return _refuse(DISP_REFUSAL)


def check_config(target_dir: str, bundle_dir: str, author_backend: str) -> str | None:
    """Корректность конфигурации на общих входах (§19 п.1); None — корректна.

    Только распознаваемость: харнесс — известное имя, backend допустим,
    `bundle_dir` по контракту назначения. ОС, `sandbox-exec`, допуск и
    «харнесс = claude» здесь НЕ проверяются — конвейер без запуска агента
    работает при любой допустимой конфигурации; полный допуск — на границе.
    """
    harness, _ = harness_for("AUTHOR")
    if harness not in KNOWN_HARNESSES:
        return f"неизвестный AUTHOR_HARNESS: {harness!r} ({'|'.join(KNOWN_HARNESSES)})"
    if author_backend not in KNOWN_BACKENDS:
        return f"неизвестный author_backend: {author_backend!r}"
    target = Path(os.path.realpath(target_dir))
    try:
        git_dirs: tuple[Path, ...] = sandbox.target_git_dirs(target)
    except sandbox.SandboxError:
        git_dirs = ()  # не git-цель: полную проверку делает граница
    try:
        safe_io.validate_bundle_dir(target, bundle_dir, git_dirs=git_dirs)
    except safe_io.SafeIOError as exc:
        return str(exc)
    return None


# --- граница ---------------------------------------------------------------


def _os_name() -> str:
    return sys.platform


def _has_sandbox_exec() -> bool:
    return os.path.exists(sandbox.SANDBOX_EXEC)


def _nested_sandbox() -> str | None:
    """Причина отказа, если профиль не применяется (F9: вложенная песочница)."""
    try:
        proc = subprocess.run(
            [sandbox.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"профиль не применился: {exc}"
    if proc.returncode == 0:
        return None
    err = proc.stderr.strip()
    if "Operation not permitted" in err:
        return f"вложенная песочница: {err}"
    return f"профиль не применился: sandbox-exec вернул {proc.returncode}: {err}"


@dataclass(frozen=True)
class _Ready:
    """Всё, что проверено до создания каталога вызова."""

    binary: str
    model: str
    token: str
    target: Path
    bundle: PurePosixPath
    git_dir: Path
    common_dir: Path
    run_dir: Path


def _preflight(call: AuthorCall, contract: Path) -> _Ready | str:
    """Проверки §13 до каталога вызова; строка — причина отказа."""
    if _os_name() != "darwin":
        return "песочница авторинга поддерживается только на macOS"
    if not _has_sandbox_exec():
        return f"нет {sandbox.SANDBOX_EXEC}: песочница авторинга недоступна"
    nested = _nested_sandbox()
    if nested is not None:
        return nested
    harness, model = harness_for("AUTHOR")
    if harness != SUPPORTED_HARNESS:
        return harness_refusal(harness)
    try:
        admission = sandbox.admission_check(contract, harness=harness)
    except sandbox.SandboxError as exc:
        return str(exc)
    try:
        token = author_claude_token()
    except AgentIsolationError as exc:
        return str(exc)
    run_dir = Path(call.run_dir)
    if not run_dir.is_absolute() or os.path.realpath(run_dir) != str(run_dir):
        return f"run_dir {call.run_dir!r} не канонический (realpath)"
    target = Path(os.path.realpath(call.target_dir))
    try:
        git_dir, common_dir = sandbox.target_git_dirs(target)
        bundle = safe_io.validate_bundle_dir(
            target, call.bundle_dir, git_dirs=(git_dir, common_dir)
        )
    except (sandbox.SandboxError, safe_io.SafeIOError) as exc:
        return str(exc)
    return _Ready(
        str(admission.realpath),
        model,
        token,
        target,
        bundle,
        git_dir,
        common_dir,
        run_dir,
    )


def _probe_targets(ready: _Ready) -> sandbox.ProbeTargets:
    """Что проба обязана не суметь записать (§10 п.3, §15)."""
    extra = [DEVTOOLS_ROOT.parent / "disputatio" / ".venv"]
    if ready.target == DEVTOOLS_ROOT:
        extra.append(DEVTOOLS_ROOT / "governance")
    return sandbox.ProbeTargets(
        target_dir=ready.target,
        bundle_dir=ready.bundle,
        git_dir=ready.git_dir,
        common_dir=ready.common_dir,
        run_dir=ready.run_dir,
        home=Path.home(),
        extra=tuple(p for p in extra if p.is_dir()),
    )


@dataclass(frozen=True)
class AgentRun:
    """Исход запуска агента: `code` None — таймаут; `stdout` — захваченный
    stream-json (не больше `TRANSCRIPT_LIMIT`); `seen` — всего прочитано байт;
    `complete` — канал дочитан до EOF."""

    code: int | None
    stdout: bytes = b""
    seen: int = 0
    complete: bool = True

    @property
    def truncated(self) -> bool:
        return self.seen > len(self.stdout)


class _Capture:
    """Чтение канала с потолком: сверх `limit` байты считаются и отбрасываются
    (канал дочитывается — агент не блокируется на записи)."""

    def __init__(self, fd: int, limit: int) -> None:
        self.fd, self.limit = fd, limit
        self.chunks: list[bytes] = []
        self.kept = self.seen = 0
        self.eof = False

    def pump(self, wait: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], max(wait, 0.0))
        if not ready:
            return
        chunk = os.read(self.fd, _READ_CHUNK)
        if not chunk:
            self.eof = True
            return
        self.seen += len(chunk)
        room = self.limit - self.kept
        if room > 0:
            self.chunks.append(chunk[:room])
            self.kept += min(room, len(chunk))


def _run_agent(
    argv: list[str], *, env: Mapping[str, str], cwd: Path, timeout: float
) -> AgentRun:
    """Агент лидером новой сессии, `stdin` из `/dev/null`, stdout — в
    транскрипт (stderr — в каналы раннера); по выходу/таймауту — `SIGKILL`
    группе (гигиена §12). Срок общий: чтение канала его не продлевает."""
    proc = subprocess.Popen(
        argv,
        env=dict(env),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    cap = _Capture(proc.stdout.fileno(), TRANSCRIPT_LIMIT)
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if cap.eof:
                try:
                    proc.wait(timeout=left)
                except subprocess.TimeoutExpired:
                    pass
                break
            cap.pump(min(left, _PUMP_TICK))
        code = proc.poll()
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if code is None:
        proc.wait()
    grace = time.monotonic() + TRANSCRIPT_GRACE
    while not cap.eof and time.monotonic() < grace:
        cap.pump(grace - time.monotonic())
    proc.stdout.close()
    return AgentRun(code, b"".join(cap.chunks), cap.seen, cap.eof)


def _save_transcript(
    run_dir: Path, filename: str, run: AgentRun, started: str
) -> tuple[str, str | None]:
    """Транскрипт попытки — в НОВЫЙ каталог `run_dir/author-transcripts/<id>/`
    (существующее имя — отказ, прежние попытки не перезаписываются).
    Возвращает (путь каталога относительно `run_dir`, причина отказа или
    None). В evidence не публикуется: в выводе инструментов могут быть
    прочитанные секреты."""
    stem = filename.rsplit(".", 1)[0]
    rel = f"{TRANSCRIPTS}/{started.replace(':', '')}-{stem}-{uuid.uuid4().hex[:8]}"
    meta = {
        "node": filename,
        "started": started,
        "exit_code": run.code,
        "bytes_seen": run.seen,
        "bytes_kept": len(run.stdout),
        "truncated": run.truncated,
        "complete": run.complete,
        "limit": TRANSCRIPT_LIMIT,
    }
    try:
        safe_io.create_dir(run_dir, rel)
        safe_io.publish(run_dir, f"{rel}/stream.jsonl", run.stdout)
        safe_io.publish(
            run_dir, f"{rel}/meta.json", (json.dumps(meta, indent=2) + "\n").encode()
        )
    except (safe_io.SafeIOError, OSError) as exc:
        return rel, f"транскрипт вызова не записан ({rel}): {exc}"
    return rel, None


def _transfer(call: sandbox.CallDir, ready: _Ready, filename: str) -> LaunchOutcome:
    """Перенос §15: один файл узла из заготовки в бандл безопасной записью."""
    try:
        entries = safe_io.list_tree(call.root, "stage")
    except safe_io.SafeIOError as exc:
        return _refuse(f"заготовка не читается: {exc}")
    extra = tuple(f"{e.path} ({e.kind})" for e in entries if e.path != filename)
    if extra:
        return LaunchOutcome(
            REFUSED, "в заготовке, кроме файла узла, есть лишнее", extra
        )
    if not entries:
        return _refuse(f"агент не записал файл узла {filename}")
    try:
        data = safe_io.read_regular(
            call.root, f"stage/{filename}", limit=NODE_LIMIT, timeout=NODE_READ_TIMEOUT
        )
        safe_io.publish(ready.target, ready.bundle / filename, data)
    except safe_io.SafeIOError as exc:
        return _refuse(f"перенос файла узла: {exc}")
    return LaunchOutcome(0)


def launch_author(
    call: AuthorCall,
    make_prompt: Callable[[str], str],
    *,
    contract: Path | None = None,
    timeout: float | None = None,
) -> LaunchOutcome:
    """Авторский вызов claude под песочницей §12 — или отказ с причиной.

    `make_prompt(output_path)` получает абсолютный `<call>/stage/<file>` —
    единственное место записи агента. При отказе до пробы каталог вызова не
    создаётся; созданный каталог удаляется до возврата при любом исходе.
    """
    ready = _preflight(call, ADMITTED_CONTRACT if contract is None else contract)
    if isinstance(ready, str):
        return _refuse(ready)
    try:
        forbidden = sandbox.ForbiddenRoots(
            ready.target, ready.git_dir, ready.common_dir, DEVTOOLS_ROOT, ready.run_dir
        )
        safe_io.ensure_dirs(ready.run_dir, "edge-check")
        cdir = sandbox.create_call_dir(
            sandbox.calls_root(), call.run_id, forbidden=forbidden
        )
    except (sandbox.SandboxError, safe_io.SafeIOError) as exc:
        return _refuse(f"каталог вызова: {exc}")
    created = _missing_dirs(ready.target, ready.bundle)
    try:
        outcome = _launch_in(cdir, ready, call, make_prompt, timeout)
    except (sandbox.SandboxError, safe_io.SafeIOError, OSError) as exc:
        outcome = _refuse(f"подготовка вызова: {exc}")
    finally:
        shutil.rmtree(cdir.root, ignore_errors=True)
    if not outcome.ok:
        _remove_empty(created)
    return outcome


def _missing_dirs(target: Path, bundle: PurePosixPath) -> list[Path]:
    """Каталоги бандла, которых нет до вызова (их создаёт проба, N6)."""
    paths = [
        target.joinpath(*bundle.parts[:i]) for i in range(1, len(bundle.parts) + 1)
    ]
    return [p for p in paths if not os.path.lexists(p)]


def _remove_empty(created: list[Path]) -> None:
    """Неуспех не меняет цель (§19 п.1): созданные для пробы каталоги бандла
    снимаются, если пусты; непустой — оставляется как есть."""
    for path in reversed(created):
        try:
            os.rmdir(path)
        except OSError:
            return


def _launch_in(
    cdir: sandbox.CallDir,
    ready: _Ready,
    call: AuthorCall,
    make_prompt: Callable[[str], str],
    timeout: float | None,
) -> LaunchOutcome:
    try:
        profile = sandbox.render_profile(cdir, home=Path.home())
    except sandbox.SandboxError as exc:
        return _refuse(f"профиль: {exc}")
    env = call_env(cdir, ready.token)
    probe = sandbox.run_probe(cdir, profile, env, _probe_targets(ready))
    if not probe.ok:
        return _refuse("проба песочницы не прошла: " + "; ".join(probe.failures))
    safe_io.publish(
        cdir.root, "claude-config/settings.json", ISOLATED_CLAUDE_SETTINGS.encode()
    )
    prompt = make_prompt(str(cdir.stage / call.filename))
    argv = [
        sandbox.SANDBOX_EXEC,
        "-f",
        str(cdir.root / "profile.sb"),
        *author_argv(ready.binary, ready.model, prompt),
    ]
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = _run_agent(
        argv,
        env=env,
        cwd=ready.target,
        timeout=AUTHOR_TIMEOUT if timeout is None else timeout,
    )
    rel, unsaved = _save_transcript(ready.run_dir, call.filename, run, started)
    if unsaved is not None:
        return _refuse(unsaved + " — перенос не делается")
    print(f"транскрипт вызова: {ready.run_dir / rel}", file=sys.stderr)
    if run.code is None:
        outcome = _refuse("таймаут авторского вызова — перенос не делается")
    elif run.code != 0:
        outcome = LaunchOutcome(
            run.code, f"агент вернул {run.code} — перенос не делается"
        )
    else:
        outcome = _transfer(cdir, ready, call.filename)
    if outcome.ok:
        return outcome
    return dataclasses.replace(outcome, reason=f"{outcome.reason}; транскрипт: {rel}")
