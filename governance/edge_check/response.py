"""Разбор и валидация ответа модели (спека §4.2, D5).

Инструмент не верит ответу: отсутствующий пункт, ссылка вне входа, номер
строки за пределами файла и неизвестный класс находки — `ERROR`, а не
молчаливое сужение проверки.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from governance.edge_check.inputs import PreparedInput
from governance.edge_check.rules import EdgeCheckError, RuleSet


@dataclass(frozen=True)
class Criterion:
    id: str
    status: str
    reason: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    cls: str
    path: str
    lines: tuple[int, int]
    statement: str


@dataclass(frozen=True)
class Response:
    criteria: tuple[Criterion, ...]
    findings: tuple[Finding, ...]


def parse_response(
    raw: str, ruleset: RuleSet, prepared: PreparedInput
) -> Response:
    try:
        envelope = json.loads(raw)
        payload = envelope.get("structured_output", envelope)
        criteria_raw = payload["criteria"]
        findings_raw = payload["findings"]
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError) as exc:
        raise EdgeCheckError("invalid_response", f"ответ негоден: {exc}") from exc

    criteria = tuple(
        Criterion(str(c["id"]), str(c["status"]), str(c.get("reason", "")))
        for c in criteria_raw
    )
    declared = [it.id for it in ruleset.items]
    got = [c.id for c in criteria]
    missing = [rid for rid in declared if rid not in got]
    if missing:
        raise EdgeCheckError(
            "criteria_incomplete",
            f"модель не ответила по пунктам: {', '.join(missing)}",
        )

    lines_by_path = {f.path: f.text.count("\n") + 1 for f in prepared.files}
    findings: list[Finding] = []
    for f in findings_raw:
        path = str(f["path"])
        if path not in lines_by_path:
            raise EdgeCheckError(
                "finding_outside_input", f"находка ссылается вне входа: {path}"
            )
        cls = str(f["class"])
        if cls not in ruleset.severity.known():
            raise EdgeCheckError(
                "invalid_finding_class",
                f"класс находки {cls!r} не объявлен",
            )
        start, end = (int(f["lines"][0]), int(f["lines"][1]))
        if not 1 <= start <= end <= lines_by_path[path]:
            raise EdgeCheckError(
                "invalid_line_range",
                f"{path}: строки {start}–{end} вне файла "
                f"({lines_by_path[path]} строк)",
            )
        findings.append(
            Finding(
                str(f["rule_id"]),
                cls,
                path,
                (start, end),
                str(f["statement"]),
            )
        )
    return Response(criteria, tuple(findings))
