"""Правка узла по замечаниям владельца на паузе просмотра (#768).

Общее для личного и enterprise-путей: один круг «переписать → гвард
ослабления → вопрос об удалениях → не более одного повтора». Вызов автора
и запись журнала замечаний — у вызывающего (`revise`, `review_pause`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from lite import node_guard
from lite import review_pause as rp
from lite.interview_chat import Read

#: исходы круга правки (`revise_by_owner`)
#: указание автору на повтор по пункту, который владелец оставил как был
KEEP_NOTE = {
    rp.REMOVE: "- {item} не удалять",
    rp.DOWNGRADE: "- {item}: сохранить Priority Must",
    rp.CHANGE: "- {item}: не менять, оставить как было",
}
ACCEPTED, PAUSED, UNCLEAR, FAILED = "accepted", "paused", "unclear", "failed"
UNCLEAR_REMARK = (
    "замечание не применено: правка удаляла или ослабляла {ids} — "
    "сформулируйте иначе (dfl run)"
)
NO_TTY = (
    "ждёт просмотра этапа «{label}» — откройте {links}; замечания — dfl run в терминале"
)

#: `revise(before, decisions)` — один вызов автора; сбой — причина, иначе `None`
Revise = Callable[[str, str], str | None]


def decisions(batch: Iterable[rp.Remark]) -> str:
    """Замечания владельца как решения для промпта переписывания."""
    return "\n".join(f"- замечание владельца: {r.text}" for r in batch)


def links(paths: Iterable[Path]) -> str:
    """Ссылки `file://` на файлы этапа через запятую."""
    return ", ".join(f"file://{p.resolve()}" for p in paths)


def revise_by_owner(
    path: Path, text: str, *, revise: Revise, read: Read
) -> tuple[str, str]:
    """Правка файла `path` по решениям `text`; не принята — файл возвращён.

    Итог `(исход, подробность)`: `ACCEPTED`; `PAUSED` (`/стоп` на вопросе
    удаления); `UNCLEAR` (повтор снова ослабляет; подробность — id);
    `FAILED` (сбой автора; подробность — причина)."""
    before = path.read_text(encoding="utf-8")
    try:
        outcome, detail = _round(path, before, text, revise, read)
    except BaseException:
        path.write_text(before, encoding="utf-8")
        raise
    if outcome != ACCEPTED:
        path.write_text(before, encoding="utf-8")
    return outcome, detail


def _round(
    path: Path, before: str, text: str, revise: Revise, read: Read
) -> tuple[str, str]:
    """RF-2: вопрос «удалить <id>?» — только об удалённых/пониженных id; «нет»
    — файл возвращается и один повтор с «<id> не удалять». Сломанные id
    (дубли, новые находки гейта, `<grammar>`) — правка отклоняется без
    вопроса, один повтор с названной проблемой."""
    failed = revise(before, text)
    if failed:
        return FAILED, failed
    after = path.read_text(encoding="utf-8")
    bad = node_guard.weakened(before, after, set(), set())
    if not bad:
        return ACCEPTED, ""
    olds = set(node_guard.node_items(before))
    broken = node_guard.weakened(before, after, olds, olds)
    allowed: set[str] = set()
    if broken:
        extra = [
            (
                f"- не ломать пункты {', '.join(broken)}: без дублей id и в "
                "формате узла (прошлая правка их сломала)"
            )
        ]
    else:
        kinds = node_guard.change_kinds(before, after, bad)
        answer = rp.confirm_removals(
            bad, read=read, titles=node_guard.item_titles(before), kinds=kinds
        )
        if answer is None:
            return PAUSED, ""
        allowed = answer
        extra = [
            KEEP_NOTE[kinds[item]].format(item=item)
            for item in bad
            if item not in allowed
        ]
        if not extra:
            return ACCEPTED, ""
    path.write_text(before, encoding="utf-8")
    failed = revise(before, "\n".join([text, *extra]))
    if failed:
        return FAILED, failed
    after = path.read_text(encoding="utf-8")
    still = node_guard.weakened(before, after, allowed, allowed)
    if still:
        return UNCLEAR, ", ".join(still)
    return ACCEPTED, ""
