"""Ответы владельца и производный вопрос подтверждения (§6.2; О §5.10).

Ответ — комментарий владельца `Q-<id>: <имя | номер>`; учитывается текст на
момент чтения. Идентичность ответа — (comment-id, значение); `id'`
производного вопроса — функция текущего набора ответов.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

ANSWER_RE = re.compile(r"^\s*Q-([0-9a-f]{8}):\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE)
PROTOCOL = "v1"


@dataclass(frozen=True)
class Answer:
    """Действительный ответ на вопрос."""

    comment_id: int
    question_id: str
    value: str


@dataclass(frozen=True)
class Resolution:
    """Итог по вопросу: выбранное значение, производный вопрос, блокировка."""

    question_id: str
    chosen: str | None
    derived: dict[str, Any] | None
    blocked: bool


def _value(raw: str, options: list[str]) -> str | None:
    if raw in options:
        return raw
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return None


def parse_answers(
    comments: list[dict[str, Any]],
    owner_login: str,
    questions: dict[str, dict[str, Any]],
) -> dict[str, list[Answer]]:
    """Ответы владельца на известные вопросы; чужие и устаревшие — нет."""
    out: dict[str, list[Answer]] = {}
    for c in sorted(comments, key=lambda c: c.get("id") or 0):
        if c.get("author") != owner_login:
            continue
        found: dict[str, str] = {}
        for m in ANSWER_RE.finditer(c.get("body") or ""):
            q = questions.get(m.group(1))
            if (
                q is not None
                and (value := _value(m.group(2), q["options"])) is not None
            ):
                found[m.group(1)] = value
        for qid, value in found.items():
            out.setdefault(qid, []).append(Answer(int(c["id"]), qid, value))
    return out


def canonical(answers: list[Answer]) -> list[str]:
    """Канонический набор: comment-id:значение по возрастанию comment-id."""
    return [
        f"{a.comment_id}:{a.value}" for a in sorted(answers, key=lambda a: a.comment_id)
    ]


def derived_question(question: dict[str, Any], answers: list[Answer]) -> dict[str, Any]:
    """Производный вопрос подтверждения Q-<id'> (§6.2)."""
    chosen = {a.value for a in answers}
    options = [o for o in question["options"] if o in chosen]
    if "keep" not in options:
        options.append("keep")
    raw = "\x1f".join(
        ("confirm", question["question_id"], *canonical(answers), PROTOCOL, *options)
    )
    qid = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return {
        "question_id": qid,
        "subject": question["subject"],
        "reason": "confirm",
        "evidence": question["question_id"],
        "question": (
            f"{question['subject']}: на Q-{question['question_id']} разные "
            "ответы — какой исполнить?"
        ),
        "options": options,
        "default": "keep",
    }


def resolve(
    question: dict[str, Any], by_question: dict[str, list[Answer]]
) -> Resolution:
    """Итог по вопросу с учётом подтверждения."""
    qid = question["question_id"]
    mine = by_question.get(qid, [])
    values = {a.value for a in mine}
    if not mine:
        return Resolution(qid, None, None, False)
    if len(values) == 1:
        return Resolution(qid, values.pop(), None, False)
    derived = derived_question(question, mine)
    confirms = {a.value for a in by_question.get(derived["question_id"], [])}
    if not confirms:
        return Resolution(qid, None, derived, False)
    if len(confirms) > 1:  # конфликт на подтверждении: новых вопросов нет
        return Resolution(qid, None, derived, True)
    value = confirms.pop()
    return Resolution(qid, None if value == "keep" else value, derived, False)


def answers_and_resolutions(
    comments: list[dict[str, Any]], owner_login: str, questions: list[dict[str, Any]]
) -> tuple[dict[str, Resolution], list[dict[str, Any]]]:
    """Итоги по вопросам и открытые производные вопросы (два прохода)."""
    known = {q["question_id"]: q for q in questions}
    first = parse_answers(comments, owner_login, known)
    derived = [
        r.derived for q in questions if (r := resolve(q, first)).derived is not None
    ]
    known.update({d["question_id"]: d for d in derived if d is not None})
    by_q = parse_answers(comments, owner_login, known)
    results = {q["question_id"]: resolve(q, by_q) for q in questions}
    open_derived = [
        r.derived
        for r in results.values()
        if r.derived is not None and r.derived["question_id"] not in by_q
    ]
    return results, open_derived
