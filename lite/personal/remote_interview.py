"""Интервью личного пути без терминала (#766 срез 4).

Разговор ведёт скил (Claude сессии), не агент `dfl`: шаг ставит вопрос
`interview-personal` («нужен бриф»), скил пишет реплики `note` в тот же журнал
`interview.jsonl`, что и терминальный интервьюер, и сдаёт бриф `submit`. Бриф
проходит общую с терминалом `interviewer.settle` (те же проверки и лимиты);
принятый — черновик `brief-draft.json`, сводка `render_brief` в журнал и вопрос
`brief-confirm` («Верно?», ответ — `dfl reply`). Следующий шаг: «да» — бриф из
черновика; иное — поправка человека в журнал и снова «нужен бриф».
Агент интервьюера здесь не зовётся (платных вызовов нет). Блокировку прогона
держит вызывающий (`run_lock`), как у `dfl reply`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lite import i18n, pending
from lite.personal import interviewer
from lite.personal.brief import Brief, brief_from_json, render_brief

DRAFT = "brief-draft.json"
NEED_BRIEF = (
    "нужен бриф по теме «{subject}»: разговор ведёт скил — dfl note, затем dfl brief"
)
CAP_REFUSAL = "лимит вопросов исчерпан — соберите бриф: dfl brief"
NOTHING_TO_BUILD_REMOTE = (
    "строить пока нечего: нет ни одной функции с критерием приёмки — "
    "спросите человека, что должно работать (dfl note), и сдайте бриф снова"
)
BRIEF_PROBLEMS = "бриф не принят — поправьте и сдайте снова:"
EMPTY_NOTE_REFUSAL = "пустая реплика не записывается"
ROLE_REFUSAL = "роль реплики: agent или human"
HUMAN_OPTIONS_REFUSAL = "у реплики человека вариантов нет — --option только у agent"
#: ключ вопросов удалённого интервью
KEY = "brief"
_ROLES = ("agent", "human")


@dataclass(frozen=True)
class Submitted:
    """Итог сдачи брифа: принят (`summary` — сводка) или проблемы."""

    ok: bool
    problems: list[str] = field(default_factory=list)
    summary: str = ""


def step(run_dir: Path, subject: str, *, lang: str = "ru") -> Brief | None:
    """Шаг интервью без терминала: подтверждённый бриф или `None` (вопрос
    поставлен; строку ожидания печатает вызывающий через `pending.wait_text`)."""
    rec = pending.load(run_dir)
    if rec is not None and rec.get("kind") == pending.BRIEF_CONFIRM:
        q = pending.Question(pending.BRIEF_CONFIRM, KEY, str(rec.get("text", "")))
        reply = pending.take(run_dir, q)
        if reply is None:
            return None
        brief = _confirmed(run_dir, reply, lang)
        if brief is not None:
            return brief
    _post_need_brief(run_dir, subject, lang)
    return None


def note(
    run_dir: Path,
    qid: str,
    role: str,
    text: str,
    options: tuple[str, ...] = (),
    *,
    lang: str = "ru",
) -> None:
    """Реплика разговора в журнал: `PendingError` — нет/чужой вопрос,
    `ValueError` — реплика не годится (пустая, роль, варианты, лимит)."""
    _require(run_dir, qid, (pending.INTERVIEW_PERSONAL,))
    body = text.strip()
    if not body:
        raise ValueError(i18n.t(EMPTY_NOTE_REFUSAL))
    if role not in _ROLES:
        raise ValueError(i18n.t(ROLE_REFUSAL))
    log = _log(run_dir)
    if role == "human":
        if options:
            raise ValueError(i18n.t(HUMAN_OPTIONS_REFUSAL))
        log.append({"role": "human", "text": body})
        return
    if interviewer._questions(log.entries) >= interviewer.MAX_QUESTIONS:
        raise ValueError(i18n.t(CAP_REFUSAL))
    kept = [o.strip() for o in options if o.strip()]
    log.append({"role": "agent", "kind": "ask", "text": body, "options": kept})


def submit(
    run_dir: Path,
    qid: str,
    data: object,
    *,
    subject: str,
    accept_uncovered: bool = False,
    lang: str = "ru",
) -> Submitted:
    """Сдать бриф (объект `brief_from_json`): `PendingError` — нет/чужой
    вопрос, `FormError` — форма не та. Сдать можно и поверх «Верно?» —
    новая сводка меняет id вопроса, ответ на прежнюю не переживает её."""
    _require(run_dir, qid, (pending.INTERVIEW_PERSONAL, pending.BRIEF_CONFIRM))
    brief = brief_from_json(data, lang=lang)
    log = _log(run_dir)
    settled, problems = interviewer.settle(
        brief, log.entries, force=accept_uncovered, lang=lang
    )
    brief_text = json.dumps(data, ensure_ascii=False)
    if settled is None or not settled.goals or not settled.functions:
        (run_dir / DRAFT).unlink(missing_ok=True)  # сдан другой бриф
    if settled is None:
        log.append({"role": "agent", "kind": "brief", "text": brief_text})
        log.append({"role": "check", "text": "\n".join(problems)})
        _post_need_brief(run_dir, subject, lang)
        return Submitted(False, problems)
    if not settled.goals or not settled.functions:
        log.append({"role": "agent", "kind": "brief", "text": brief_text})
        _post_need_brief(run_dir, subject, lang)
        return Submitted(False, [i18n.t(NOTHING_TO_BUILD_REMOTE)])
    _write_draft(run_dir, settled)
    summary = render_brief(settled, lang=lang)
    log.append({"role": "agent", "kind": "summary", "text": summary})
    text = f"{summary}\n\n{i18n.t(interviewer.CONFIRM)}"
    pending.post(run_dir, pending.Question(pending.BRIEF_CONFIRM, KEY, text))
    return Submitted(True, [], summary)


def reset(run_dir: Path) -> None:
    """Снять вопрос, записанные ответы и черновик (терминал побеждает, R5);
    журнал разговора остаётся."""
    pending.discard(run_dir)
    (run_dir / DRAFT).unlink(missing_ok=True)


def status(run_dir: Path, subject: str) -> dict:
    """Состояние интервью для `dfl interview --json` (R7)."""
    entries = _log(run_dir).entries
    draft = _read_draft(run_dir)
    functions = (draft or {}).get("functions")
    uncovered = [
        str(f.get("id", ""))
        for f in (functions if isinstance(functions, list) else [])
        if isinstance(f, dict) and not _covered(f)
    ]
    return {
        "track": "personal",
        "subject": subject,
        "questions": interviewer._questions(entries),
        "max_questions": interviewer.MAX_QUESTIONS,
        "checks_in_row": interviewer._checks_in_row(entries),
        "draft": draft,
        "uncovered": uncovered,
    }


def _covered(function: dict) -> bool:
    criteria = function.get("criteria")
    if not isinstance(criteria, list):
        return False
    return any(isinstance(c, str) and c.strip() for c in criteria)


def _confirmed(run_dir: Path, reply: str, lang: str) -> Brief | None:
    """Ответ на «Верно?»: «1»/слово согласия — бриф из черновика; иное —
    поправка человека в журнал (`None`). Черновик снимается в обоих случаях."""
    draft = _read_draft(run_dir)
    (run_dir / DRAFT).unlink(missing_ok=True)
    word = reply.strip().casefold()
    if word == "1" or word in interviewer.YES_WORDS:
        if draft is None:
            return None  # черновик пропал — бриф заново
        return brief_from_json(draft, lang=lang)
    fix = interviewer.fix_text(reply)
    _log(run_dir).append(
        {"role": "human", "text": interviewer.FIX_PREFIXES[lang] + fix}
    )
    return None


def _post_need_brief(run_dir: Path, subject: str, lang: str) -> None:
    entries = _log(run_dir).entries
    text = i18n.t(NEED_BRIEF).format(subject=subject)
    data = {
        "subject": subject,
        "transcript": str(run_dir / interviewer.TRANSCRIPT),
        "prompt": str(interviewer.PROMPT_PATHS[lang]),
        "lang": lang,
        "questions": interviewer._questions(entries),
        "max_questions": interviewer.MAX_QUESTIONS,
        "checks_in_row": interviewer._checks_in_row(entries),
        "max_checks_in_row": interviewer.MAX_CHECKS_IN_ROW,
    }
    pending.post(run_dir, pending.Question(pending.INTERVIEW_PERSONAL, KEY, text), data)


def _require(run_dir: Path, qid: str, kinds: tuple[str, ...]) -> None:
    """Ждёт вопрос `qid` одного из видов `kinds`, иначе `PendingError`."""
    rec = pending.load(run_dir)
    if rec is None:
        raise pending.PendingError(i18n.t(pending.NO_PENDING))
    if rec.get("id") != qid or rec.get("kind") not in kinds:
        raise pending.PendingError(i18n.t(pending.STALE))


def _log(run_dir: Path) -> interviewer._Log:
    return interviewer._Log(run_dir / interviewer.TRANSCRIPT)


def _write_draft(run_dir: Path, brief: Brief) -> None:
    path = run_dir / DRAFT
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(brief), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def _read_draft(run_dir: Path) -> dict | None:
    try:
        data = json.loads((run_dir / DRAFT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
