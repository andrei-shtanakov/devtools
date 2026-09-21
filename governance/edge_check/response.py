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
    #: Фактически известный идентификатор модели из конверта ответа
    #: (находка I3, спека §4.3); `None` — честный признак, что конверт его
    #: не нёс, а не молчаливое умолчание.
    model_id: str | None = None


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

    model_id = envelope.get("model") if isinstance(envelope, dict) else None
    if not isinstance(model_id, str) or not model_id:
        model_id = None

    try:
        criteria = tuple(
            Criterion(
                str(c["id"]), str(c["status"]), str(c.get("reason", ""))
            )
            for c in criteria_raw
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise EdgeCheckError(
            "invalid_response",
            f"критерии негодны: {exc}",
        ) from exc

    declared = [it.id for it in ruleset.items]
    got = [c.id for c in criteria]
    missing = [rid for rid in declared if rid not in got]
    if missing:
        raise EdgeCheckError(
            "criteria_incomplete",
            f"модель не ответила по пунктам: {', '.join(missing)}",
        )

    extra = [rid for rid in got if rid not in declared]
    duplicates = [rid for rid in got if got.count(rid) > 1]
    malformed = extra + duplicates
    if malformed:
        raise EdgeCheckError(
            "criteria_malformed",
            f"неправильные пункты: {', '.join(set(malformed))}",
        )

    lines_by_path = {f.path: len(f.text.splitlines()) for f in prepared.files}
    findings: list[Finding] = []
    valid_rule_ids = {it.id for it in ruleset.items}

    try:
        for f in findings_raw:
            path = str(f["path"])
            if path not in lines_by_path:
                raise EdgeCheckError(
                    "finding_outside_input",
                    f"находка ссылается вне входа: {path}",
                )
            cls = str(f["class"])
            if cls not in ruleset.severity.known():
                raise EdgeCheckError(
                    "invalid_finding_class",
                    f"класс находки {cls!r} не объявлен",
                )
            rule_id = str(f["rule_id"])
            if rule_id not in valid_rule_ids:
                raise EdgeCheckError(
                    "invalid_finding_rule",
                    f"находка ссылается на неизвестное правило: {rule_id}",
                )

            lines = f.get("lines")
            if not isinstance(lines, (list, tuple)):
                raise EdgeCheckError(
                    "invalid_line_range",
                    f"диапазон должен быть списком, получен "
                    f"{type(lines).__name__}",
                )
            if len(lines) != 2:
                raise EdgeCheckError(
                    "invalid_line_range",
                    f"диапазон должен содержать 2 элемента, получено "
                    f"{len(lines)}",
                )

            for i, elem in enumerate(lines):
                if not isinstance(elem, int) or isinstance(elem, bool):
                    raise EdgeCheckError(
                        "invalid_line_range",
                        f"элемент диапазона [{i}] должен быть целым, получен "
                        f"{type(elem).__name__}",
                    )

            start, end = (int(lines[0]), int(lines[1]))
            if not 1 <= start <= end <= lines_by_path[path]:
                raise EdgeCheckError(
                    "invalid_line_range",
                    f"{path}: строки {start}–{end} вне файла "
                    f"({lines_by_path[path]} строк)",
                )
            findings.append(
                Finding(
                    rule_id,
                    cls,
                    path,
                    (start, end),
                    str(f["statement"]),
                )
            )
    except EdgeCheckError:
        raise
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
    ) as exc:
        raise EdgeCheckError(
            "invalid_response",
            f"находки негодны: {exc}",
        ) from exc

    return Response(criteria, tuple(findings), model_id)
