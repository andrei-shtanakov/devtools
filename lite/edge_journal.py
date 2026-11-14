"""Находки волны edge-check и журнал решений человека по ним.

`dfl` разбирает блокирующие находки разговором; каждое решение дописывается
строкой JSON в `edge-decisions.jsonl` каталога прогона (поле `wave` отделяет
волны). Override-решения читает и раннер — через
`governance.edge_check.override` (lite → governance, не наоборот); решение
привязано к самой находке её digest'ом (`override.finding_digest`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from governance.edge_check import coordinator, override
from lite.stop_findings import BLOCKING

JOURNAL = override.JOURNAL


@dataclass(frozen=True)
class Finding:
    """Блокирующая находка ребра edge-check."""

    node: str
    edge: str
    rule_id: str
    cls: str
    statement: str
    path: str | None
    lines: str
    digest: str = ""  # `override.finding_digest`: привязка решения к находке
    subject: tuple[str, ...] = ()  # хэши входа subject ребра

    @property
    def key(self) -> tuple[str, str, str]:
        """Ключ находки: (узел, ребро, правило)."""
        return (self.node, self.edge, self.rule_id)


@dataclass(frozen=True)
class Decision:
    """Решение человека по находке."""

    key: tuple[str, str, str]
    question: str
    answer: str
    kind: str  # "option" | "own" | "disagree" | "override"
    round: int
    allows: dict[str, list[str]] = field(default_factory=dict)
    digest: str = ""  # находка, к которой относится решение (override сверяет)
    subject: list[str] = field(default_factory=list)  # хэши subject ребра


def _finding(node: str, edge: str, raw: dict, subject: list[str]) -> Finding:
    loc = raw.get("location") or {}
    return Finding(
        node=node,
        edge=edge,
        rule_id=str(raw.get("rule_id")),
        cls=str(raw.get("class")),
        statement=str(raw.get("statement")),
        path=loc.get("path"),
        lines=str(loc.get("lines")),
        digest=override.finding_digest(node, edge, raw, subject),
        subject=tuple(subject),
    )


def blocking_findings(run_dir: Path, wave: int) -> list[Finding]:
    """Блокирующие находки (`blocker`/`major`) рёбер волны."""
    records = coordinator.load_level_records(run_dir, wave)
    return [
        f
        for (node, edge), record in records.items()
        for f in (
            _finding(node, edge, r, override.subject_shas(record.get("subject")))
            for r in record.get("findings", [])
        )
        if f.cls in BLOCKING
    ]


def append(run_dir: Path, wave: int, d: Decision) -> None:
    """Дописать решение строкой JSON в журнал прогона."""
    row = {**asdict(d), "wave": wave, "key": list(d.key)}
    with (run_dir / JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _decision(row: object, wave: int) -> Decision | None:
    if not isinstance(row, dict) or row.get("wave") != wave:
        return None
    try:
        data = {k: v for k, v in row.items() if k != "wave"}
        data["key"] = tuple(data["key"])
        return Decision(**data)
    except (KeyError, TypeError):
        return None


def load(run_dir: Path, wave: int) -> list[Decision]:
    """Решения волны по порядку записи; битые строки пропускаются."""
    path = run_dir / JOURNAL
    if not path.is_file():
        return []
    out: list[Decision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = _decision(json.loads(line), wave)
        except json.JSONDecodeError:
            continue
        if d is not None:
            out.append(d)
    return out


def overridden(run_dir: Path, wave: int) -> set[str]:
    """Digest'ы находок с решением `override` (делегирует governance)."""
    return override.overridden(run_dir, wave)
