"""Чистый парсер грамматики Q requirements/design и покрытие (спека §4/Task 4).

Никакого git/файловой системы — только строки. Отдельный модуль (не
`bundle_state.py`/`runner.py`): `governance.task_bridge` (Task 5) переиспользует
эти же три функции, не тянет за собой S4-специфичный код раннера.

Грамматика Q (обе стороны, дословно из спеки/Global Constraints):

- requirements: ``- **Q-NN · owner_role: <role> · blocking: true|false.**
  <текст>``;
- design: ``#### Q-NN · owner_role: architects · resolution:
  resolved|deferred`` (+ строка ``reason: <…>`` при ``deferred``);
- пустой входной набор архитектурных вопросов покрыт только явной строкой
  ``Открытых архитектурных вопросов нет (входной набор пуст)`` в design.
"""

from __future__ import annotations

import re

_ARCHITECTS_ROLE = "architects"
_EMPTY_DECLARATION = "Открытых архитектурных вопросов нет (входной набор пуст)"

_REQ_Q_RE = re.compile(
    r"^-\s+\*\*(Q-\d+)\s*·\s*owner_role:\s*([\w-]+)\s*·\s*"
    r"blocking:\s*(?:true|false)\.\*\*",
    re.MULTILINE,
)
_DESIGN_Q_RE = re.compile(
    r"^####\s+(Q-\d+)\s*·\s*owner_role:\s*[\w-]+\s*·\s*"
    r"resolution:\s*(resolved|deferred)\s*$",
    re.MULTILINE,
)
_REASON_RE = re.compile(r"^reason:\s*(.+)$", re.MULTILINE)


def parse_requirements_questions(text: str) -> dict[str, str]:
    """`Q-NN` → `owner_role`, из requirements-DSL (все роли, не только architects).

    Фильтрация по роли — дело вызывающего (`coverage_findings` ниже): этот
    парсер — чистый срез текста, ничего не решает про то, кто должен
    отвечать.
    """
    return {m.group(1): m.group(2) for m in _REQ_Q_RE.finditer(text)}


def parse_design_resolutions(text: str) -> dict[str, tuple[str, str | None]]:
    """`Q-NN` → `(resolution, reason)`, из design-DSL.

    `reason` — первая строка `reason: <…>` внутри блока вопроса (от
    заголовка `#### Q-NN …` до следующего такого заголовка или конца
    текста); `None`, если её нет (типично для `resolved`, но синтаксически
    допустимо и для «голого» `deferred` — коллер решает, находка это или
    нет).
    """
    matches = list(_DESIGN_Q_RE.finditer(text))
    result: dict[str, tuple[str, str | None]] = {}
    for idx, match in enumerate(matches):
        qid, resolution = match.group(1), match.group(2)
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[match.end() : block_end]
        reason_match = _REASON_RE.search(block)
        reason = reason_match.group(1).strip() if reason_match else None
        result[qid] = (resolution, reason)
    return result


def coverage_findings(req_text: str, design_text: str) -> list[str]:
    """Непокрытые architects-вопросы requirements в design — список находок.

    Только вопросы с `owner_role: architects` входят во входной набор
    (product-Q — забота другого узла, не design). Пустой входной набор
    покрыт единственным способом — явной строкой-декларацией в design;
    её отсутствие при пустом наборе — тоже находка (иначе тихое
    «нечего проверять» неотличимо от «автор забыл написать раздел
    вопросов вовсе»).
    """
    questions = parse_requirements_questions(req_text)
    resolutions = parse_design_resolutions(design_text)
    architects_qs = [
        qid for qid, role in questions.items() if role == _ARCHITECTS_ROLE
    ]

    if not architects_qs:
        if re.search(rf"^{re.escape(_EMPTY_DECLARATION)}", design_text, re.M) is not None:
            return []
        message = (
            "design: входной набор архитектурных вопросов пуст, но "
            f"строка-декларация «{_EMPTY_DECLARATION}» отсутствует"
        )
        return [message]

    findings: list[str] = []
    for qid in architects_qs:
        if qid not in resolutions:
            findings.append(
                f"{qid}: не покрыт резолюцией в design "
                "(owner_role: architects в requirements)"
            )
            continue
        state, reason = resolutions[qid]
        if state == "deferred" and not reason:
            findings.append(f"{qid}: resolution: deferred без строки reason:")
    return findings
