"""Находки проверок, на которых встал прогон: смысловой (edge-check) и гейта.

Раннер пишет их в каталог прогона (`edge-findings.txt`, `gate-findings.txt`)
и встаёт в `stopped_review`/`stopped_gate`; `dfl` показывает их человеку с
тем, что править и где (devtools#757). Файл читается только при своём
статусе: гейт пишет предупреждения и при успехе, а resume снимает файлы
сброшенных шагов (`runner._drop_findings_of`).

В `edge-findings.txt` рядом с находками (`R1 major: … @ файл:строки`) лежат
отказы самой проверки (`EdgeCheckError`, ребро с вердиктом `ERROR`): это не
замечания к узлу, править бандл по ним нечего. `gate-findings.txt` — сырой
вывод гейта: ошибки (гард раннера `error GC-…:` или сам gate-check
`GC-NN error […]`) считаются и печатаются на уровне 0 (не больше
`GATE_SHOWN`), прочее (`not_evaluated`, предупреждения) — подробнее уровня 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from governance import run_state as rs
from governance import runner

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
# Классы, валящие проверку: `severity.blocking` правил ребра
# (`contracts/edge-check/v1/rules/*.yaml`, у всех рёбер одинаков — сверяет
# тест); остальные — рекомендации, итог не валят (`edge_check.check.decide`).
BLOCKING = frozenset({"blocker", "major"})
ADVISORY_HEAD = "  рекомендации (проверку не валят, можно оставить):"
# FAIL без блокирующих находок: пункт `insufficient`, `fail` без причины,
# отказ ребра (`edge_check.check.decide`) — по находкам на экране не чинится
# строка раннера (`_step_edge`) при FAIL ребра без находок: пункт правил
# `insufficient` или `fail` без причины — проверка штатная, не сбой прибора
UNRATED = runner.EDGE_UNRATED
UNRATED_TEXT = "ревьюер не оценил пункт правил — замечаний к файлу нет"
GATE_SHOWN = 10  # строк ошибок гейта на уровне 0
# ошибка гейта: гард раннера (`error GC-ORPHAN: …`) или gate-check
# (`GC-08 error [FR-01] …`)
GATE_ERROR_RE = re.compile(r"^(?:error GC-[\w-]+|GC-[\w-]+ error\b)")
FAILED_CHECK = (
    "смысловая проверка узлов не состоялась — сбой ревьюера, ваш файл ни при "
    "чём; повторите dfl run, повторится — dfl report (подробно: dfl run -v)"
)
UNBLOCKED_STOP = (
    "смысловая проверка узлов не прошла без блокирующих замечаний (пункт "
    "правил не оценён) — повторите dfl run; повторится — dfl report"
)
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
    unrated: list[str] = field(default_factory=list)  # edge: FAIL без находок


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
    unrated = [e for e in entries if not FINDING_RE.match(e) and UNRATED in e]
    failures = [e for e in entries if e not in findings and e not in unrated]
    return Stop(status, path, findings, failures, unrated)


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
        errors = [r for r in stop.failures if GATE_ERROR_RE.match(r)]
        head = (
            f"проверка бандла не прошла: ошибок {len(errors)}"
            if errors
            else ("проверка бандла не прошла")
        )
        return f"{head} (вывод: {stop.path})"
    blocking, advisory = split(stop.findings)
    if not blocking:
        return FAILED_CHECK if stop.failures else UNBLOCKED_STOP
    out = f"смысловая проверка узлов: блокирующих замечаний {len(blocking)}"
    return out + (f", рекомендаций {len(advisory)}" if advisory else "")


def split(findings: list[str]) -> tuple[list[str], list[str]]:
    """Находки → (блокирующие, рекомендации) по классу серьёзности."""
    levels = [m.group(2) if (m := FINDING_RE.match(f)) else "" for f in findings]
    blocking = [f for f, lv in zip(findings, levels, strict=True) if lv in BLOCKING]
    return blocking, [f for f in findings if f not in blocking]


def shown(stop: Stop) -> list[tuple[str, int]]:
    """Строки для экрана с уровнем: находки edge — 0; вывод гейта — 1."""
    if stop.status == GATE:
        # ошибки гейта — то, что править (уровень 0, не больше GATE_SHOWN);
        # прочий вывод (`not_evaluated`, предупреждения) — подробность
        errors = [r for r in stop.failures if GATE_ERROR_RE.match(r)]
        shown_errors = set(errors[:GATE_SHOWN])
        return [
            (f"  - {row.removeprefix('error ')}", 0)
            if row in shown_errors
            else (f"  {row}", 1)
            for row in stop.failures
        ]
    blocking, advisory = split(stop.findings)
    # отказ прибора — подробность для разработчиков, не задание человеку
    failed = [(f"  - {readable(f)}", 1) for f in stop.failures]
    rows = [(f"  - {readable(f)}", 0) for f in blocking + stop.unrated]
    if not blocking:  # стоп не из-за них: «можно оставить» было бы неправдой
        return rows + failed + [(f"  - {readable(f)}", 0) for f in advisory]
    rows += failed
    if advisory:
        rows.append((ADVISORY_HEAD, 0))
        rows += [(f"  - {readable(f)}", 0) for f in advisory]
    return rows


def readable(entry: str) -> str:
    """Запись для человека: `R1 major (файл:строки): текст`; отказ — текст."""
    m = FINDING_RE.match(entry)
    if m is None and UNRATED in entry:
        return UNRATED_TEXT
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
    """Что править и где — по блокирующим находкам; стоп edge-check без них
    (отказ прибора, провал пункта по статусу) — `None`: править по экрану
    нечего, подсказка — общая."""
    blocking, _advisory = split(stop.findings)
    if stop.status == EDGE and not blocking:
        return None
    named = [str(Path(state.bundle_dir) / f) for f in files(blocking)]
    return EDIT_HINT.format(
        files=", ".join(named) if named else f"файлы {state.bundle_dir}",
        target=state.target_dir,
        branch=state.branch,
    )
