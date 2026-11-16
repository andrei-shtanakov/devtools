"""Вопрос владельцу по ручным проверкам в конце прогона dfl (#771).

Общий для enterprise- и личного пути проход «ответить и опубликовать»:
`settle` собирает проверки из бандла (один раз, в запись), спрашивает по
одной, сохраняет ответ сразу и публикует (`governance.manual_checks.publish`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from governance import manual_checks as mc
from governance.auto_tasks import notice
from lite.interview_chat import Read, read_answer

NO_TTY = "ждут ответа {n} ручных проверок — запустите dfl run в терминале"
PAUSED = "ответы на ручные проверки — потом: dfl run"
NOTE_PROMPT = "что видно на деле (Enter — без комментария):"
CAVEAT_PROMPT = "текст оговорки:"


def is_wait(reason: str) -> bool:
    """Стоп «ждём человека» (код 0), а не сбой (код 1)."""
    return reason == PAUSED or reason.startswith(NO_TTY.split("{", 1)[0])


_WORDS = {
    "1": mc.DONE,
    "выполнено": mc.DONE,
    "2": mc.NOT_DONE,
    "не выполнено": mc.NOT_DONE,
    "3": mc.CAVEAT,
    "с оговоркой": mc.CAVEAT,
    "принять с оговоркой": mc.CAVEAT,
}


def ask_one(check: mc.ManualCheck, read: Read) -> dict | None:
    """Ответ на одну проверку: `{"verdict", "note"}`; `None` — стоп/EOF."""
    while True:
        notice(mc.question(check))
        answer = read_answer(read, quick=lambda _s: True)
        if answer is None:
            return None
        verdict = _WORDS.get(answer.strip().casefold())
        if verdict is None:
            continue
        if verdict == mc.DONE:
            return {"verdict": verdict, "note": ""}
        if verdict == mc.NOT_DONE:
            notice(NOTE_PROMPT)
            note = read_answer(read)
            return None if note is None else {"verdict": verdict, "note": note}
        while True:
            notice(CAVEAT_PROMPT)
            note = read_answer(read)
            if note is None:
                return None
            if note:
                return {"verdict": verdict, "note": note}


def settle(
    record: dict,
    *,
    load_texts: Callable[[], tuple[str, str]],
    tty: bool,
    read: Read,
    ops,
    repo_slug: str,
    pr: int,
    pr_url: str,
    run_id: str,
    save: Callable[[], None],
) -> str | None:
    """Пройти ручные проверки прогона: `None` — пройдено (запись
    `completed`), строка — причина стопа (вызывающий печатает, код 0)."""
    if "checks" not in record:
        try:
            checks = mc.collect(*load_texts())
        except (OSError, ValueError) as exc:
            return f"бандл прогона не прочитан: {exc} — повторите dfl run"
        record.update(
            status="started",
            checks=[mc.as_dict(c) for c in checks],
            answers={},
            commented=False,
        )
        save()
    if not record["checks"]:
        record["status"] = "completed"
        save()
        return None
    todo = mc.unanswered(record)
    if todo and not tty:
        return NO_TTY.format(n=len(todo))
    for check in todo:
        answer = ask_one(check, read)
        if answer is None:
            return PAUSED
        answer.update(at=date.today().isoformat(), issue=None)
        record["answers"][check.beh_id] = answer
        save()
    try:
        mc.publish(
            ops,
            record,
            repo_slug=repo_slug,
            pr=pr,
            pr_url=pr_url,
            run_id=run_id,
            save=save,
        )
    except Exception as exc:  # noqa: BLE001 - любой сбой форджи — стоп с причиной
        return f"публикация ответов не удалась: {exc} — повторите dfl run"
    return None
