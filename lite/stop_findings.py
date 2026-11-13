"""Находки проверок, на которых встал прогон: смысловой (edge-check) и гейта.

Раннер пишет их в каталог прогона (`edge-findings.txt`, `gate-findings.txt`)
и встаёт в `stopped_review`/`stopped_gate`; `dfl` показывает их человеку с
тем, что править и где (devtools#757). Файл читается только при своём
статусе: гейт пишет предупреждения и при успехе, а resume снимает файлы
сброшенных шагов (`runner._drop_findings_of`).

В `edge-findings.txt` рядом с находками (`R1 major: … @ файл:строки`) лежат
отказы самой проверки (`EdgeCheckError`, ребро с вердиктом `ERROR`): это не
замечания к узлу, править бандл по ним нечего. `gate-findings.txt` — сырой
вывод gate-check (с `not_evaluated` и прочим текстом): он не считается
построчно и печатается только подробнее уровня 0.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from governance import run_state as rs

EDGE = "stopped_review"
GATE = "stopped_gate"
FILE_OF_STATUS = {EDGE: "edge-findings.txt", GATE: "gate-findings.txt"}
EDGE_PREFIX = "error EDGE-CHECK("
# `error EDGE-CHECK(<узел>/<ребро>): R1 major: <текст> @ <файл>:[a, b]`
FINDING_RE = re.compile(
    r"^error EDGE-CHECK\([^)]*\): (R\d+) (\w+): (.*?)(?: @ ([^:@]+):(.*))?$",
    re.DOTALL,
)
FAILURE_RE = re.compile(r"^error EDGE-CHECK\([^)]*\): (.*)$", re.DOTALL)
EDIT_HINT = (
    "поправьте {files} в {target} (ветка {branch}) — коммитить не нужно, "
    "раннер закоммитит сам; затем dfl run (повтор — новый платный вызов проверки)"
)


@dataclass(frozen=True)
class Stop:
    """Стоп на проверке: находки к узлам и отказы самой проверки."""

    status: str
    path: Path
    findings: list[str]  # edge: находки с правилом и серьёзностью
    failures: list[str]  # edge: отказы прибора; гейт: весь вывод


def load(run_id: str, status: str) -> Stop | None:
    """Стоп прогона на проверке; не стоп проверки или файла нет — `None`."""
    name = FILE_OF_STATUS.get(status)
    if name is None:
        return None
    path = rs.run_dir(run_id) / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if status == GATE:
        rows = [line for line in text.splitlines() if line.strip()]
        return Stop(status, path, [], rows) if rows else None
    entries = _entries(text)
    if not entries:
        return None
    findings = [e for e in entries if FINDING_RE.match(e)]
    failures = [e for e in entries if not FINDING_RE.match(e)]
    return Stop(status, path, findings, failures)


def _entries(text: str) -> list[str]:
    """Записи файла edge-check: строка без префикса — продолжение
    многострочного текста предыдущей записи, а не новая находка."""
    out: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith(EDGE_PREFIX) or not out:
            out.append(line.strip())
        else:
            out[-1] += " " + line.strip()
    return out


def summary(stop: Stop) -> str:
    """Причина стопа одной строкой."""
    if stop.status == GATE:
        return f"проверка бандла не прошла (вывод: {stop.path})"
    if not stop.findings:
        first = FAILURE_RE.match(stop.failures[0])
        what = first.group(1) if first else stop.failures[0]
        return f"смысловая проверка узлов не состоялась: {what}"
    levels = Counter(m.group(2) for m in map(FINDING_RE.match, stop.findings) if m)
    tail = ", ".join(f"{k} {n}" for k, n in levels.items())
    return f"смысловая проверка узлов нашла замечаний: {len(stop.findings)} ({tail})"


def shown(stop: Stop) -> list[tuple[str, int]]:
    """Строки для экрана с уровнем: находки edge — 0; вывод гейта — 1."""
    if stop.status == GATE:
        return [(f"  {row}", 1) for row in stop.failures]
    rows = [(f"  - {readable(f)}", 0) for f in stop.findings]
    return rows + [(f"  - {readable(f)}", 0) for f in stop.failures]


def readable(entry: str) -> str:
    """Запись для человека: `R1 major (файл:строки): текст`; отказ — текст."""
    m = FINDING_RE.match(entry)
    if m is None:
        failure = FAILURE_RE.match(entry)
        return f"отказ проверки: {failure.group(1)}" if failure else entry
    rule, level, text, path, where = m.groups()
    place = f" ({path}:{where})" if path else ""
    return f"{rule} {level}{place}: {text}"


def files(findings: list[str]) -> list[str]:
    """Файлы узлов, на которые указывают находки, без повторов."""
    out = (m.group(4) for m in map(FINDING_RE.match, findings) if m and m.group(4))
    return list(dict.fromkeys(out))


def edit_hint(state: rs.RunState, stop: Stop) -> str | None:
    """Что править и где; стоп без находок к бандлу (отказ edge-check) —
    `None`: править нечего, подсказка — общая."""
    if stop.status == EDGE and not stop.findings:
        return None
    named = [str(Path(state.bundle_dir) / f) for f in files(stop.findings)]
    return EDIT_HINT.format(
        files=", ".join(named) if named else f"файлы {state.bundle_dir}",
        target=state.target_dir,
        branch=state.branch,
    )
