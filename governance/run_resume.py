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


class _BranchReader(Protocol):
    def fetch_branch(self, target_dir: str, branch: str) -> bool: ...

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None: ...


def resume_candidates(prs: list[dict], before: list[int]) -> list[tuple[int, str]]:
    """Открытые PR ветки прогона, появившиеся после `before`: `(номер, ветка)`."""
    old = set(before)
    found = [
        (int(p["number"]), str(p.get("headRefName") or ""))
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


def read_progress(
    ops: _BranchReader, target_dir: str, branch: str, tasks_rel: str
) -> tuple[int, int] | None:
    """Счётчик задач на `origin/<branch>`; не прочитан — `None` (не стоп)."""
    try:
        if not ops.fetch_branch(target_dir, branch):
            return None
        text = ops.show_file(target_dir, f"origin/{branch}", tasks_rel)
    except Exception:  # noqa: BLE001 - счётчик лишь украшает плашку
        return None
    return None if text is None else task_progress(text)


def banner(
    subject: str, repo_slug: str, branch: str, progress: tuple[int, int] | None
) -> str:
    """Плашка проекта, который продолжается (форма — решение владельца)."""
    tail = (
        "счётчик задач не прочитан"
        if progress is None
        else f"сделано {progress[0]} из {progress[1]} задач"
    )
    return f"▶ продолжаю проект «{subject}» · {repo_slug}\n  ветка {branch} · {tail}"


def resume_stop(branch: str, pr: int) -> str:
    """Причина остановки: продолжить ветку нельзя, новую dfl не создаёт."""
    return (
        f"продолжить ветку {branch} пока нельзя: spec-runner не умеет "
        "продолжать начатую ветку (spec-runner#678), а новую dfl не создаёт, "
        f"чтобы не потерять сделанное. Продолжить — влейте PR #{pr} в main и "
        f"повторите dfl run; начать заново — закройте PR #{pr} и повторите "
        "dfl run"
    )


def several_stop(prs: list[int]) -> str:
    """Причина остановки: частичных PR больше одного — выбирает человек."""
    listed = ", ".join(f"#{n}" for n in prs)
    return (
        f"открыто несколько частичных integration-PR прогона: {listed} — "
        "закройте лишние и повторите dfl run"
    )
