"""`dfl report`: отчёт о последнем сбое `dfl` для разработчиков DarkFactory.

Отчёт собирается из записи сбоя (`lite.failure_log`) и состояния машины,
очищается (домашний каталог, токены) и уходит публичным issue в
репо devtools, из которого установлен `dfl` (его `origin`), — после показа и
подтверждения коллегой. Спека
`docs/superpowers/specs/2026-11-12-dfl-report-design.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from governance import df_config
from governance import run_state as rs
from governance.user_targets import TargetsError, slug_from_url
from lite.failure_log import FailureRecord
from lite.failure_log import load as load_record
from lite.install import InstallError, load_components
from lite.output import refusal

BODY_LIMIT = 65_536
TITLE_CAUSE = 80
DEVTOOLS = Path(__file__).resolve().parent.parent
HIDDEN = "<скрыто>"

_TOKENS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[abp]-[A-Za-z0-9-]+"),
)
# Значение после префикса: группа 1 остаётся, остальное — `<скрыто>`.
# После ключа и схемы авторизации скрывается всё до конца строки: значение
# бывает из нескольких слов (`Authorization: token abc`, `password=a b`);
# лишнее скрытие безопаснее утечки в публичное issue.
_PREFIXED = (
    re.compile(r"(?i)(\b(?:Bearer|Basic)\s+).*"),
    re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s]*@"),  # userinfo URL до последнего @
    re.compile(
        r"(?i)([\"']?\w*(?:token|secret|passw(?:or)?d|api[_-]?key|auth)\w*[\"']?"
        r"\s*[=:]\s*).*"
    ),
)

_NORMALIZE = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<q>"),
    (re.compile(r"(?:~|(?<![\w.])/)[^\s:'\"]*"), "<path>"),
    # SHA и суффиксы run_id: hex-слово от 6 знаков, в котором есть цифра
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{6,40}\b"), "<sha>"),
    (re.compile(r"#?\d+"), "<n>"),
)
_TOOLS = ("claude", "gh", "uv", "git")


@dataclass(frozen=True)
class Facts:
    """Состояние машины для отчёта: прогоны, версии, конфиг."""

    runs: list[dict[str, str]] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    config: str = ""


@dataclass(frozen=True)
class Report:
    """Готовый к отправке отчёт: заголовок, тело и отпечаток."""

    title: str
    body: str
    fp: str


def normalize(text: str) -> str:
    """Причина без изменчивых частей (URL, кавычки, пути, SHA, числа)."""
    for pattern, placeholder in _NORMALIZE:
        text = pattern.sub(placeholder, text)
    return " ".join(text.split())


def fingerprint(rec: FailureRecord, last_op: str) -> str:
    """12 hex sha256 от команды, последней операции прогона и причины."""
    key = "\n".join([rec.command, last_op or "dfl", normalize(rec.cause)])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def redact(text: str, home: Path) -> str:
    """Домашний каталог → `~`, токены → `<скрыто>`."""
    home_str = str(home).rstrip("/")
    if home_str:
        text = re.sub(re.escape(home_str) + r"(?=/|\b|$)", "~", text)
    for pattern in _TOKENS:
        text = pattern.sub(HIDDEN, text)
    for pattern in _PREFIXED:
        text = pattern.sub(lambda m: m.group(1) + HIDDEN + _at(m), text)
    return text


def _at(match: re.Match[str]) -> str:
    """Для userinfo URL `@` входит в совпадение — вернуть его."""
    return "@" if match.group(0).endswith("@") else ""


def collect_facts(rec: FailureRecord) -> Facts:
    """Прогоны из записи, версии и конфиг; недоступное — строкой, не исключением."""
    return Facts(
        runs=[_run_facts(r, rec.runs_note) for r in rec.run_ids],
        versions=_versions(),
        config=_config_line(),
    )


def build(rec: FailureRecord, facts: Facts) -> Report:
    """Заголовок и Markdown-тело отчёта (очищенные), с отпечатком."""
    # Операция — только прогона, изменённого вызовом: у прогона «вызов его не
    # менял» она к сбою не относится и ломала бы склейку повторов.
    changed = facts.runs and not rec.runs_note
    last_op = facts.runs[0].get("last_op_name", "") if changed else ""
    fp = fingerprint(rec, last_op)
    home = Path.home()
    cause = _inert(redact(rec.cause, home))
    title = f"[dfl-report] {rec.command}: {cause[:TITLE_CAUSE]} (fp {fp})"
    tail = [redact(line, home) for line in rec.output_tail]
    head = redact(_head(rec, facts), home)
    foot = redact(_foot(facts), home) + f"\n\nfp {fp}\n"
    body = _fit(head, tail, foot)
    return Report(title=title, body=body, fp=fp)


def _head(rec: FailureRecord, facts: Facts) -> str:
    lines = [
        (
            f"**Команда:** `dfl {' '.join(rec.argv)}` — код {rec.exit_code}, "
            f"{rec.time}, репо `{rec.cwd_repo or 'неизвестно'}`"
        ),
        f"**Причина:** {_code(rec.cause)}",
        "",
        "## Где остановилось",
        "",
    ]
    if not facts.runs:
        lines.append("прогонов нет")
    for run in facts.runs:
        lines.append(
            f"- `{run['run_id']}`: status `{run['status']}`, wave {run['wave']}, "
            f"последняя операция `{run['last_op']}`, PR {run['pr']}"
            + (f" ({run['note']})" if run["note"] else "")
        )
        if run["stop_reason"]:
            lines.append(f"  - stop-reason: {_code(run['stop_reason'])}")
    return "\n".join(lines)


def _foot(facts: Facts) -> str:
    lines = ["## Версии", ""]
    lines += [f"- {k}: {v}" for k, v in facts.versions.items()]
    lines += ["", "## Конфиг", "", facts.config]
    return "\n".join(lines)


_ZWSP = "\u200b"


def _inert(text: str) -> str:
    """Заголовок: `@имя` и `#N` не упоминают и не ссылаются (нулевой пробел)."""
    return re.sub(r"([@#])(?=\w)", lambda m: m.group(1) + _ZWSP, text)


def _code(text: str) -> str:
    """Встроенный код Markdown: внутри `@`/`#N` инертны; обратные кавычки → `'`."""
    return f"`{text.replace('`', "'")}`" if text else "—"


def _longest_backticks(lines: list[str]) -> int:
    runs = (len(m) for line in lines for m in re.findall(r"`+", line))
    return max(runs, default=0)


def _fit(head: str, tail: list[str], foot: str) -> str:
    """Собрать тело; не влезает в `BODY_LIMIT` — срезать начало хвоста."""
    fence = "`" * max(3, _longest_backticks(tail) + 1)
    dropped = 0
    while True:
        note = f"… (обрезано {dropped} строк)\n" if dropped else ""
        out = (
            f"{head}\n\n## Вывод\n\n{fence}\n{note}"
            + "\n".join(tail[dropped:])
            + f"\n{fence}\n\n{foot}"
        )
        if len(out) <= BODY_LIMIT or dropped >= len(tail):
            return out[:BODY_LIMIT]
        dropped += max(1, (len(out) - BODY_LIMIT) // 200)


def _run_facts(run_id: str, note: str) -> dict[str, str]:
    path = rs.run_dir(run_id)
    try:
        raw = json.loads((path / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    ops = raw.get("ops") or {}
    last = list(ops)[-1] if ops else ""
    last_status = (ops.get(last) or {}).get("status", "") if last else ""
    try:
        stop = (path / "stop-reason.txt").read_text(encoding="utf-8").strip()
    except OSError:
        stop = ""
    return {
        "run_id": run_id,
        "status": str(raw.get("status", "не читается")),
        "wave": str(raw.get("wave")),
        "pr": str(raw.get("pr")),
        "last_op_name": last,
        "last_op": f"{last} {last_status}".strip() or "нет",
        "stop_reason": stop,
        "note": note,
    }


def _versions() -> dict[str, str]:
    out = {"devtools": _cmd(["git", "-C", str(DEVTOOLS), "rev-parse", "HEAD"])}
    try:
        components = load_components()
    except (OSError, ValueError, InstallError) as exc:
        out["components"] = f"нет ({exc})"
        components = []
    for comp in components:
        head = _cmd(
            ["git", "-C", str(DEVTOOLS.parent / comp.name), "rev-parse", "HEAD"]
        )
        out[comp.name] = f"пин {comp.sha[:12]}, HEAD {head[:12]}"
    for tool in _TOOLS:
        out[tool] = _cmd([tool, "--version"])
    out["os"] = f"{platform.platform()} {platform.machine()}"
    out["python"] = sys.version.split()[0]
    return out


def _cmd(argv: list[str]) -> str:
    """Первая строка вывода команды; сбой — «нет (<причина>)»."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"нет ({exc})"
    text = (done.stdout or done.stderr).strip().splitlines()
    first = text[0] if text else ""
    return first if done.returncode == 0 else f"нет (код {done.returncode}: {first})"


def _config_line() -> str:
    try:
        cfg = df_config.load()
    except df_config.ConfigError as exc:
        return f"конфиг не читается: {exc}"
    acc = cfg.accounts
    return f"mode = {cfg.mode}; human = {acc.human}; agent = {acc.agent}"


# --- отправка ---------------------------------------------------------------

Gh = Callable[[list[str], Path | None, str | None], subprocess.CompletedProcess]
YES = ("y", "yes", "да", "д")
PUBLIC_WARNING = (
    "отчёт уйдёт ПУБЛИЧНО; токены и домашний каталог скрыты автоматически, "
    "но это страховка — перечитайте отчёт выше"
)
PRINT_HINT = "используйте dfl report --print и пришлите вывод сами"


class ReportError(Exception):
    """Отчёт не отправлен — причина и что сделать (код 2)."""


def real_gh(
    argv: list[str], profile: Path | None, body: str | None = None
) -> subprocess.CompletedProcess:
    """`gh <argv>` с `GH_CONFIG_DIR=<profile>` (или по умолчанию), тело в stdin."""
    env = dict(os.environ)
    if profile is not None:
        env["GH_CONFIG_DIR"] = str(profile)
    try:
        return subprocess.run(
            ["gh", *argv],
            input=body,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 1, "", str(exc))


def run_report(
    cwd: Path,
    *,
    print_only: bool,
    gh: Gh = real_gh,
    ask: Callable[[str], str] = input,
    is_tty: Callable[[], bool] = sys.stdin.isatty,
) -> int:
    """`dfl report`: показать отчёт; после «y» в терминале — issue или комментарий.

    Отказ (нет терминала, ошибка `gh`) — блок `refusal` в stderr, код 2."""
    try:
        return _report(cwd, print_only, gh, ask, is_tty)
    except ReportError as exc:
        print(refusal("dfl report", str(exc)), file=sys.stderr)
        return 2


def _report(
    cwd: Path,
    print_only: bool,
    gh: Gh,
    ask: Callable[[str], str],
    is_tty: Callable[[], bool],
) -> int:
    rec = load_record(cwd)
    if rec is None:
        print("записанного сбоя нет — отправлять нечего")
        return 0
    rep = build(rec, collect_facts(rec))
    print(f"{rep.title}\n\n{rep.body}")
    if print_only:
        return 0
    if not is_tty():
        raise ReportError(
            "отчёт не отправлен: нет терминала для подтверждения — "
            "подтвердите отправку в терминале или используйте dfl report --print"
        )
    repo = report_repo()
    profile = _profile()
    duplicate = _find_duplicate(rep, repo, gh, profile)
    print(f"\n{PUBLIC_WARNING}")
    question = (
        f"Добавить комментарий к {duplicate[1]}? [y/N] "
        if duplicate
        else f"Отправить в {repo} как публичное issue? [y/N] "
    )
    try:
        answer = ask(question)
    except (EOFError, KeyboardInterrupt):
        answer = ""
        print()
    if answer.strip().lower() not in YES:
        print("не отправлено")
        return 0
    if duplicate:
        argv = ["issue", "comment", str(duplicate[0]), "-R", repo]
        url = _gh_ok(gh, [*argv, "--body-file", "-"], profile, rep.body)
    else:
        argv = ["issue", "create", "-R", repo, "--title", rep.title]
        url = _gh_ok(gh, [*argv, "--body-file", "-"], profile, rep.body)
    print(f"отправлено: {url}")
    return 0


def _find_duplicate(
    rep: Report, repo: str, gh: Gh, profile: Path | None
) -> tuple[int, str] | None:
    """Открытое issue с тем же отпечатком в заголовке: (номер, URL)."""
    out = _gh_ok(
        gh,
        [
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--search",
            f'"fp {rep.fp}" in:title',
            "--json",
            "number,url,title",
        ],
        profile,
        None,
    )
    try:
        items = json.loads(out or "[]")
    except ValueError as exc:
        raise ReportError(
            f"ответ gh issue list не разобран: {exc} — {PRINT_HINT}"
        ) from exc
    marker = f"(fp {rep.fp})"
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and marker in str(item.get("title", "")):
            return int(item["number"]), str(item["url"])
    return None


def _gh_ok(gh: Gh, argv: list[str], profile: Path | None, body: str | None) -> str:
    done = gh(argv, profile, body)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else f"код {done.returncode}"
        raise ReportError(f"gh {' '.join(argv[:2])}: {reason} — {PRINT_HINT}")
    return (done.stdout or "").strip()


def report_repo() -> str:
    """`owner/name` репо devtools, из которого установлен `dfl` (его `origin`).

    Отчёт уходит туда, откуда пришёл код: у установки по памятке это репо
    разработчиков DarkFactory, у форка — сам форк."""
    done = subprocess.run(
        ["git", "-C", str(DEVTOOLS), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        if done.returncode != 0:
            raise TargetsError(done.stderr.strip() or "нет origin")
        return slug_from_url(done.stdout.strip())
    except TargetsError as exc:
        raise ReportError(
            f"репо для отчётов не определено: {exc} — {PRINT_HINT}"
        ) from exc


def _profile() -> Path | None:
    """Профиль `gh` человека из конфига; конфиг не читается — `gh` по умолчанию."""
    try:
        return df_config.load().accounts.human_gh_config_dir
    except df_config.ConfigError:
        return None
