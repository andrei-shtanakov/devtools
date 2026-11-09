"""Вердикт ревью в журнале прогона (план 3c-2, Task 1).

В `mode = "test"` одна учётка — автор и ревьюер, GitHub не даёт одобрить
свой PR, и `review-pr.sh` публикует вердикт `--comment` с полем ` verdict=`
в маркере (`verdict_marker`). Состояние такого обзора (`COMMENTED`) вердикта
не несёт, поэтому раннер читает его из маркера и записывает в операцию
журнала: автоматический шаг принимает только записанный положительный
вердикт на текущей голове. Отрицательный, отсутствующий или чужой головы
вердикт — не успех.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from governance.run_state import RunState, save

MARKER_PREFIX = "<!-- codex-terminal-review "
# Зеркало `verdict_marker` в review-pr.sh: `fp=` опущен у старого кита.
_MARKER_RE = re.compile(
    r"<!-- codex-terminal-review head=(?P<head>[0-9a-f]{40})"
    r"(?: fp=(?P<fp>[0-9a-f]+))?"
    r" verdict=(?P<verdict>approve|request-changes) -->"
)


@dataclass(frozen=True)
class ReviewVerdict:
    """Вердикт ревью: голова, отпечаток кита (может быть пуст) и действие."""

    head: str
    fp: str
    verdict: str


def parse_marker(body: str | None) -> ReviewVerdict | None:
    """Вердикт из тела ревью либо `None`.

    Принимается ровно один маркер `codex-terminal-review` с полем
    `verdict=`, и он — последняя строка тела; ноль или несколько маркеров,
    маркер не в конце, strict-маркер без `verdict=` — `None`.
    """
    if not body or body.count(MARKER_PREFIX) != 1:
        return None
    last = body.rstrip().rsplit("\n", 1)[-1].strip()
    match = _MARKER_RE.fullmatch(last)
    if match is None:
        return None
    return ReviewVerdict(match["head"], match["fp"] or "", match["verdict"])


def record_review(state: RunState, key: str, verdict: ReviewVerdict) -> None:
    """Записать вердикт в операцию `key` журнала и сохранить прогон."""
    state.ops[key]["verdict"] = asdict(verdict)
    save(state)


def positive_on(state: RunState, key: str, head: str) -> bool:
    """Записан ли в операции `key` вердикт `approve` именно на `head`."""
    recorded = (state.ops.get(key) or {}).get("verdict")
    return (
        isinstance(recorded, dict)
        and recorded.get("verdict") == "approve"
        and recorded.get("head") == head
    )
