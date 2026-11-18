"""Вопрос владельцу по ручным проверкам в конце прогона dfl (#771).

Общий для enterprise- и личного пути проход «ответить и опубликовать»:
`settle` собирает проверки из бандла (один раз, в запись), спрашивает по
одной, сохраняет ответ сразу и публикует (`governance.manual_checks.publish`).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

from governance import manual_checks as mc
from governance.auto_tasks import notice
from lite import pending
from lite.interview_chat import Read, read_answer

NO_TTY = "ждут ответа {n} ручных проверок — запустите dfl run в терминале"
PAUSED = "ответы на ручные проверки — потом: dfl run"
NOTE_PROMPT = "что видно на деле (Enter — без комментария):"
CAVEAT_PROMPT = "текст оговорки:"
WAIT_PREFIX = "ждёт ответа: "
#: код PR живёт в ветке прогона, рабочий каталог dfl не трогает (#806)
CHECKOUT_HINT = "проверяйте код из PR #{pr}: gh pr checkout {pr} (назад: git switch -)"
STALE_WARNING = (
    "⚠ в рабочем каталоге нет кода PR #{pr} — проверка пройдёт на старом коде; "
    "сначала: gh pr checkout {pr}"
)


def is_wait(reason: str) -> bool:
    """Стоп «ждём человека» (код 0), а не сбой (код 1)."""
    return (
        reason == PAUSED
        or reason.startswith(NO_TTY.split("{", 1)[0])
        or reason.startswith(WAIT_PREFIX)
    )


def ask_one(check: mc.ManualCheck, read: Read) -> dict | None:
    """Ответ на одну проверку: `{"verdict", "note"}`; `None` — стоп/EOF."""
    while True:
        notice(mc.question(check))
        answer = read_answer(read, quick=lambda _s: True)
        if answer is None:
            return None
        verdict = pending.VERDICT_WORDS.get(answer.strip().casefold())
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


def question_of(check: mc.ManualCheck, pr: int) -> pending.Question:
    """Вопрос для `dfl pending`: проверка и перед вариантами ответа —
    на каком коде проверять (#806); первая строка — сама проверка."""
    head, options = mc.question(check).rsplit("\n", 1)
    text = f"{head}\n{CHECKOUT_HINT.format(pr=pr)}\n{options}"
    return pending.Question(pending.MANUAL, check.beh_id, text)


def _git_ok(workdir: Path, *args: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), *args], capture_output=True, check=False
        )
    except OSError:
        return False
    return proc.returncode == 0


def _pr_head(ops, repo_slug: str, pr: int) -> str | None:
    try:
        head = ops.pr_facts(repo_slug, pr).get("headRefOid")
    except Exception:  # noqa: BLE001 - предупреждение необязательное
        return None
    return head if isinstance(head, str) and head else None


def stale_warning(ops, repo_slug: str, pr: int, workdir: Path | None) -> str | None:
    """Предупреждение, если голова PR не входит в историю `HEAD` рабочего
    каталога (#806); не узнать (не репо, форджа молчит) — `None`.

    Предок, а не равенство: после мержа `HEAD` — merge-коммит, код PR в нём
    есть. Объекта головы локально нет — кода тоже нет, предупреждение."""
    if workdir is None or not _git_ok(workdir, "rev-parse", "--verify", "-q", "HEAD"):
        return None
    head = _pr_head(ops, repo_slug, pr)
    if head is None or _git_ok(workdir, "merge-base", "--is-ancestor", head, "HEAD"):
        return None
    return STALE_WARNING.format(pr=pr)


def _parsed(text: str | None) -> dict | None:
    """Ответ `{"verdict", "note"}` из записанного текста; негодный — `None`."""
    if text is None or pending.check(pending.MANUAL, text) is not None:
        return None
    return pending.parse_manual(text)


def _answer(
    check: mc.ManualCheck, read: Read, tty: bool, run_dir: Path | None, pr: int
) -> dict | str | None:
    """Ответ на проверку: записанный `dfl reply`, из терминала или без него.

    Строка — стоп «ждёт ответа»; `None` — человек остановил ввод.
    """
    q = question_of(check, pr)
    if run_dir is None:
        return ask_one(check, read) if tty else None
    if tty:
        recorded = _parsed(pending.take(run_dir, q))
        if recorded:
            return recorded
        live = ask_one(check, read)
        if live is not None:
            pending.discard(run_dir)  # терминал побеждает: вопрос снят
        return live
    ask = pending.asker(run_dir)
    while True:
        text = ask(q)
        if text is None:
            return pending.wait_line(q)
        if (parsed := _parsed(text)) is not None:
            return parsed


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
    run_dir: Path | None = None,
    workdir: Path | None = None,
) -> str | None:
    """Пройти ручные проверки прогона: `None` — пройдено (запись
    `completed`), строка — причина стопа (вызывающий печатает, код 0).

    `run_dir` — каталог прогона для `dfl reply` (#766): в терминале ответ,
    записанный заранее, берётся вместо чтения; без терминала вопрос
    записывается в `pending.json` и прогон ждёт ответа. Без `run_dir` —
    прежнее поведение (без терминала — `NO_TTY`).

    `workdir` — рабочий каталог проекта: в терминале перед вопросами
    печатается, как переключиться на код PR, и предупреждение, если кода PR
    в нём нет (#806)."""
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
    if todo and not tty and run_dir is None:
        return NO_TTY.format(n=len(todo))
    if todo and tty:
        notice(CHECKOUT_HINT.format(pr=pr))
        if warning := stale_warning(ops, repo_slug, pr, workdir):
            notice(warning)
    for check in todo:
        answer = _answer(check, read, tty, run_dir, pr)
        if isinstance(answer, str):
            return answer
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
