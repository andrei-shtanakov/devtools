"""Продолжение прогона после остановки посреди реализации (devtools#767).

Чистые функции, общие для enterprise-пути (`governance.auto_tasks`) и
личного (`lite.personal.flow`): найти частичный integration-PR прошлой
попытки, посчитать сделанные задачи на его ветке и сказать человеку, что
будет продолжено. spec-runner пока не умеет продолжать начатую ветку
(spec-runner#678), поэтому оба пути после плашки останавливаются, а не
создают новую ветку и не теряют сделанное.
"""

from __future__ import annotations

from typing import Protocol

from governance.task_bridge import _TASK_HEAD_RE, _TASK_META_RE

#: префикс ветки прогона spec-runner (`--integration-pr`)
RUN_BRANCH_PREFIX = "spec-runner/run-"

#: код выхода, записываемый вместо 0, когда spec-runner вышел штатно, но
#: задачи на ветке PR сделаны не все: его штатная остановка по SIGINT/SIGTERM
#: без упавших задач возвращает 0, и PR без этой сверки приняли бы за готовый
INCOMPLETE_EXIT = -1

#: тексты экрана по языку (личный путь — язык прогона, #769; enterprise — ru)
_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "unread": (
            "счётчик задач ветки {branch} не прочитан — integration-PR #{pr} "
            "не принят как готовый; повторите dfl run (сбой сети пройдёт), "
            "а если tasks.md на ветке нет — разберите PR вручную"
        ),
        "unread_tail": "счётчик задач не прочитан",
        "done_tail": "сделано {done} из {total} задач",
        "banner": "▶ продолжаю проект «{subject}» · {repo}\n  ветка {branch} · {tail}",
        "resume": (
            "продолжить ветку {branch} пока нельзя: spec-runner не умеет "
            "продолжать начатую ветку (spec-runner#678), а новую dfl не создаёт, "
            "чтобы не потерять сделанное. Продолжить — влейте PR #{pr} в main и "
            "повторите dfl run; начать заново — закройте PR #{pr} и повторите "
            "dfl run"
        ),
        "several": (
            "открыто несколько частичных integration-PR прогона: {listed} — "
            "закройте лишние и повторите dfl run"
        ),
    },
    "en": {
        "unread": (
            "the task counter of branch {branch} was not read — integration PR "
            "#{pr} is not accepted as ready; repeat dfl run (a network failure "
            "will pass), and if the branch has no tasks.md — sort the PR out by "
            "hand"
        ),
        "unread_tail": "task counter not read",
        "done_tail": "{done} of {total} tasks done",
        "banner": "▶ continuing project “{subject}” · {repo}\n  branch {branch} · {tail}",
        "resume": (
            "branch {branch} cannot be continued yet: spec-runner cannot continue "
            "a started branch (spec-runner#678), and dfl does not create a new "
            "one so as not to lose the work done. To continue — merge PR #{pr} "
            "into main and repeat dfl run; to start over — close PR #{pr} and "
            "repeat dfl run"
        ),
        "several": (
            "several partial integration PRs of the run are open: {listed} — "
            "close the extra ones and repeat dfl run"
        ),
    },
}


class _BranchReader(Protocol):
    def fetch_branch(self, target_dir: str, branch: str) -> bool: ...

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None: ...


def _head_ref(pr: dict) -> str:
    """Ветка записи `Ops.prs_by_head_prefix` (`{"number", "head": {"ref"}, ...}`).

    Нечитаемая ветка — нарушение контракта Ops: стоп, а не «частичного PR
    нет» (иначе dfl молча создал бы новую ветку прогона).
    """
    head = pr.get("head")
    ref = head.get("ref") if isinstance(head, dict) else None
    if not isinstance(ref, str):
        raise RuntimeError(f"PR без head.ref: {pr!r}")
    return ref


def resume_candidates(prs: list[dict], before: list[int]) -> list[tuple[int, str]]:
    """Открытые PR ветки прогона, появившиеся после `before`: `(номер, ветка)`."""
    old = set(before)
    found = [
        (int(p["number"]), _head_ref(p))
        for p in prs
        if p.get("state") == "OPEN" and int(p["number"]) not in old
    ]
    return sorted((n, head) for n, head in found if head.startswith(RUN_BRANCH_PREFIX))


def task_progress(text: str) -> tuple[int, int] | None:
    """`(сделано, всего)` по tasks.md; статус — первая мета-строка блока."""
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if _TASK_HEAD_RE.match(ln)]
    if not heads:
        return None
    done = 0
    for start, end in zip(heads, [*heads[1:], len(lines)], strict=True):
        for line in lines[start + 1 : end]:
            meta = _TASK_META_RE.match(line)
            if meta:
                done += meta.group("status").upper().endswith("DONE")
                break
    return done, len(heads)


def incomplete(progress: tuple[int, int] | None) -> bool:
    """Счётчик прочитан и задачи сделаны не все (`None` решает вызывающий)."""
    return progress is not None and progress[0] < progress[1]


def unread_stop(branch: str, pr: int, *, lang: str = "ru") -> str:
    """Причина остановки: готовность PR прогона не установлена.

    Счётчик здесь — вход решения «PR готов», не украшение плашки:
    неизвестность — стоп, а не «всё сделано» (fail-closed, ревью #805).
    `lang` — язык экрана (личный путь — язык прогона, #769).
    """
    return _TEXTS[lang]["unread"].format(branch=branch, pr=pr)


def pr_branch(prs: list[dict], pr: int) -> str | None:
    """Ветка ОТКРЫТОГО PR прогона `pr`; нет такого — `None`."""
    found = [head for n, head in resume_candidates(prs, []) if n == pr]
    return found[0] if found else None


def read_progress(
    ops: _BranchReader, target_dir: str, branch: str, tasks_rel: str
) -> tuple[int, int] | None:
    """Счётчик задач на `origin/<branch>`; не прочитан — `None`.

    Что значит `None`, решает вызывающий: для плашки упавшей попытки — лишь
    «счётчик не прочитан», для приёмки PR после кода 0 — стоп (`unread_stop`).
    """
    try:
        if not ops.fetch_branch(target_dir, branch):
            return None
        text = ops.show_file(target_dir, f"origin/{branch}", tasks_rel)
    except Exception:  # noqa: BLE001 - любой сбой = «не прочитан»
        return None
    return None if text is None else task_progress(text)


def banner(
    subject: str,
    repo_slug: str,
    branch: str,
    progress: tuple[int, int] | None,
    *,
    lang: str = "ru",
) -> str:
    """Плашка проекта, который продолжается (форма — решение владельца)."""
    texts = _TEXTS[lang]
    tail = (
        texts["unread_tail"]
        if progress is None
        else texts["done_tail"].format(done=progress[0], total=progress[1])
    )
    return texts["banner"].format(
        subject=subject, repo=repo_slug, branch=branch, tail=tail
    )


def resume_stop(branch: str, pr: int, *, lang: str = "ru") -> str:
    """Причина остановки: продолжить ветку нельзя, новую dfl не создаёт."""
    return _TEXTS[lang]["resume"].format(branch=branch, pr=pr)


def several_stop(prs: list[int], *, lang: str = "ru") -> str:
    """Причина остановки: частичных PR больше одного — выбирает человек."""
    listed = ", ".join(f"#{n}" for n in prs)
    return _TEXTS[lang]["several"].format(listed=listed)
