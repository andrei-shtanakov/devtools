"""`dfl report`: отчёт о последнем сбое `dfl` для разработчиков DarkFactory.

Отчёт собирается из записи сбоя (`lite.failure_log`) и состояния машины,
очищается (домашний каталог, токены) и уходит публичным issue в
`REPORT_REPO` — после показа и подтверждения коллегой. Спека
`docs/superpowers/specs/2026-11-12-dfl-report-design.md`.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from governance import df_config
from governance import run_state as rs
from lite.failure_log import FailureRecord
from lite.install import load_components

REPORT_REPO = "andrei-shtanakov/devtools"
BODY_LIMIT = 65_536
TITLE_CAUSE = 80
DEVTOOLS = Path(__file__).resolve().parent.parent
HIDDEN = "<скрыто>"

_TOKENS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"xox[abp]-[A-Za-z0-9-]+"),
)
_BEARER = re.compile(r"(Bearer\s+)\S+")
_ASSIGNED = re.compile(r"(?i)\b(\w*(?:token|secret|password)\w*\s*[=:]\s*)\S+")

_NORMALIZE = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<q>"),
    (re.compile(r"(?:~|(?<![\w.])/)[^\s:'\"]*"), "<path>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<sha>"),
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
    text = _BEARER.sub(lambda m: m.group(1) + HIDDEN, text)
    return _ASSIGNED.sub(lambda m: m.group(1) + HIDDEN, text)


def collect_facts(rec: FailureRecord) -> Facts:
    """Прогоны из записи, версии и конфиг; недоступное — строкой, не исключением."""
    return Facts(
        runs=[_run_facts(r, rec.runs_note) for r in rec.run_ids],
        versions=_versions(),
        config=_config_line(),
    )


def build(rec: FailureRecord, facts: Facts) -> Report:
    """Заголовок и Markdown-тело отчёта (очищенные), с отпечатком."""
    last_op = facts.runs[0].get("last_op_name", "") if facts.runs else ""
    fp = fingerprint(rec, last_op)
    home = Path.home()
    cause = redact(rec.cause, home)
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
        f"**Причина:** {rec.cause}",
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
            lines.append(f"  - stop-reason: {run['stop_reason']}")
    return "\n".join(lines)


def _foot(facts: Facts) -> str:
    lines = ["## Версии", ""]
    lines += [f"- {k}: {v}" for k, v in facts.versions.items()]
    lines += ["", "## Конфиг", "", facts.config]
    return "\n".join(lines)


def _fit(head: str, tail: list[str], foot: str) -> str:
    """Собрать тело; не влезает в `BODY_LIMIT` — срезать начало хвоста."""
    dropped = 0
    while True:
        note = f"… (обрезано {dropped} строк)\n" if dropped else ""
        out = (
            f"{head}\n\n## Вывод\n\n```\n{note}"
            + "\n".join(tail[dropped:])
            + f"\n```\n\n{foot}"
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
    except (OSError, ValueError) as exc:
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
