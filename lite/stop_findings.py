"""Находки проверок, на которых встал прогон: смысловой (edge-check) и гейта.

Раннер пишет их в каталог прогона (`edge-findings.txt`, `gate-findings.txt`)
и встаёт в `stopped_review`/`stopped_gate`; `dfl` показывает их человеку с
тем, что править и где (devtools#757). Файл читается только при своём
статусе: гейт пишет предупреждения и при успехе, а resume снимает файлы
сброшенных шагов (`runner._drop_findings_of`).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from governance import run_state as rs

FILE_OF_STATUS = {
    "stopped_review": "edge-findings.txt",
    "stopped_gate": "gate-findings.txt",
}
CHECK_OF_STATUS = {
    "stopped_review": "смысловая проверка узлов",
    "stopped_gate": "проверка бандла",
}
# `error EDGE-CHECK(<узел>/<ребро>): R1 major: <текст> @ <файл>:[a, b]`
EDGE_RE = re.compile(
    r"^error EDGE-CHECK\([^)]*\): (?:(R\d+) (\w+): )?(.*?)(?: @ ([^:@]+):(.*))?$"
)
EDIT_HINT = (
    "поправьте {files} в {target} (ветка {branch}) — коммитить не нужно, "
    "раннер закоммитит сам; затем dfl run (повтор — новый платный вызов проверки)"
)


def lines(run_id: str, status: str) -> list[str]:
    """Непустые строки файла находок стопа `status`; нет файла — пусто."""
    name = FILE_OF_STATUS.get(status)
    if name is None:
        return []
    try:
        text = (rs.run_dir(run_id) / name).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def summary(found: list[str], status: str) -> str:
    """Причина стопа одной строкой: какая проверка и сколько замечаний."""
    check = CHECK_OF_STATUS.get(status, "проверка")
    levels = Counter(m.group(2) for m in map(EDGE_RE.match, found) if m and m[2])
    tail = ", ".join(f"{k} {n}" for k, n in levels.items())
    return f"{check} нашла замечаний: {len(found)}" + (f" ({tail})" if tail else "")


def readable(line: str) -> str:
    """Строка находки для человека: `R1 major (файл:строки): текст`."""
    m = EDGE_RE.match(line)
    if m is None:
        return line
    rule, level, text, path, where = m.groups()
    head = " ".join(p for p in (rule, level) if p)
    place = f"{path}:{where}" if path else ""
    label = f"{head} ({place})" if head and place else head or place
    return f"{label}: {text}" if label else text


def files(found: list[str]) -> list[str]:
    """Файлы узлов, на которые указывают находки, без повторов."""
    out = (m.group(4) for m in map(EDGE_RE.match, found) if m and m.group(4))
    return list(dict.fromkeys(out))


def edit_hint(state: rs.RunState, found: list[str]) -> str:
    """Что править и где: файлы узлов в бандле чекаута цели."""
    named = [str(Path(state.bundle_dir) / f) for f in files(found)]
    return EDIT_HINT.format(
        files=", ".join(named) if named else f"файлы {state.bundle_dir}",
        target=state.target_dir,
        branch=state.branch,
    )
