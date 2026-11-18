"""Правка узла по замечаниям владельца на паузе просмотра (#768).

Общее для личного и enterprise-путей: один круг «переписать → гвард
ослабления → вопрос об удалениях → не более одного повтора». Вызов автора
и запись журнала замечаний — у вызывающего (`revise`, `review_pause`).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from lite import node_guard, pending
from lite import review_pause as rp
from lite.interview_chat import Read

#: указание автору на повтор по пункту, который владелец оставил как был
KEEP_NOTE = {
    rp.REMOVE: "- {item} не удалять",
    rp.DOWNGRADE: "- {item}: сохранить Priority Must",
    rp.CHANGE: "- {item}: не менять, оставить как было",
}
#: исходы круга правки (`revise_by_owner`)
ACCEPTED, PAUSED, UNCLEAR, FAILED = "accepted", "paused", "unclear", "failed"
#: исход без терминала: вопрос об удалении записан (`dfl pending`), прогон ждёт
WAITING = "waiting"
#: первая правка, ждущая ответов (`store / CANDIDATE_FILE`): платный вызов
#: автора не повторяется, пока файл этапа и замечания те же
CANDIDATE_FILE = "owner-revise.json"
NO_STORE = "ask без store: кандидата правки негде сохранить"
_FINAL = frozenset({ACCEPTED, UNCLEAR, FAILED})
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


@dataclass
class _Ctx:
    """Куда спрашивать подтверждение удалений в одном круге правки."""

    read: Read
    ask: pending.Ask | None
    store: Path | None
    stage: str
    candidate: dict | None = None


@dataclass(frozen=True)
class _Draft:
    """Первая правка автора — содержимое кандидата без ответов."""

    name: str
    before: str
    decisions: str
    after: str


def revise_by_owner(
    path: Path,
    text: str,
    *,
    revise: Revise,
    read: Read,
    ask: pending.Ask | None = None,
    store: Path | None = None,
    stage: str = "",
) -> tuple[str, str]:
    """Правка файла `path` по решениям `text`; не принята — файл возвращён.

    Итог `(исход, подробность)`: `ACCEPTED`; `PAUSED` (`/стоп` на вопросе
    удаления); `UNCLEAR` (повтор снова ослабляет; подробность — id);
    `FAILED` (сбой автора; подробность — причина); `WAITING` (без
    терминала: вопрос записан, подробность — `pending.wait_line`).

    `ask` — спрашивалка без терминала; `store` — каталог прогона, где живёт
    кандидат первой правки (`CANDIDATE_FILE`) и `pending.json`; `stage` —
    этап в ключе вопроса. Без `ask` и `store` — прежнее поведение.
    `ask` без `store` — `ValueError`: без кандидата платная правка
    повторялась бы молча."""
    if ask is not None and store is None:
        raise ValueError(NO_STORE)
    before = path.read_text(encoding="utf-8")
    ctx = _Ctx(read, ask, store, stage)
    try:
        outcome, detail = _round(path, before, text, revise, ctx)
    except BaseException:
        path.write_text(before, encoding="utf-8")
        raise
    if outcome != ACCEPTED:
        path.write_text(before, encoding="utf-8")
    if store is not None and outcome in _FINAL:
        (store / CANDIDATE_FILE).unlink(missing_ok=True)
    return outcome, detail


def _round(
    path: Path, before: str, text: str, revise: Revise, ctx: _Ctx
) -> tuple[str, str]:
    """RF-2: вопрос по каждому ослабленному id — по виду правки
    (`node_guard.change_kinds`): «Удалить …?», «Снять Must с …?» или
    «Принять правку …?»; отказ — файл возвращается и один повтор с
    указанием своего вида (`KEEP_NOTE`). Сломанные id (дубли, новые находки
    гейта, `<grammar>`) — правка отклоняется без вопроса, один повтор с
    названной проблемой."""
    failed = _first_revision(path, before, text, revise, ctx)
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
        draft = _Draft(path.name, before, text, after)
        answer = _confirm(bad, kinds, node_guard.item_titles(before), ctx, draft)
        if isinstance(answer, tuple):
            return answer
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


def _first_revision(
    path: Path, before: str, text: str, revise: Revise, ctx: _Ctx
) -> str | None:
    """Первая правка: из совпавшего кандидата (автор не зовётся) или вызов
    автора. Сбой автора — причина, иначе `None`."""
    if ctx.store is not None:
        ctx.candidate = _matching(ctx.store, path.name, before, text)
    if ctx.candidate is None:
        return revise(before, text)
    path.write_text(ctx.candidate["after"], encoding="utf-8")
    return None


def _matching(store: Path, name: str, before: str, text: str) -> dict | None:
    """Кандидат той же правки (файл, его текст и замечания совпали) или
    `None`; несовпавший снимается вместе с вопросом о нём."""
    file = store / CANDIDATE_FILE
    if not file.exists():
        return None
    data = _load(file)
    if (
        data is not None
        and data.get("path") == name
        and data.get("before") == _digest(before)
        and data.get("decisions") == text
        and isinstance(data.get("after"), str)
        and isinstance(data.get("answers"), dict)
    ):
        return data
    file.unlink(missing_ok=True)
    pending.discard(store)
    return None


def _confirm(
    bad: list[str],
    kinds: dict[str, str],
    titles: dict[str, str],
    ctx: _Ctx,
    draft: _Draft,
) -> set[str] | tuple[str, str]:
    """Разрешённые id или окончательный исход круга (`PAUSED`, `WAITING`)."""
    if ctx.ask is not None:
        return _confirm_remote(bad, kinds, titles, ctx, draft)
    if ctx.candidate is not None and ctx.store is not None:
        return _confirm_resume(bad, kinds, titles, ctx, draft)
    answer = rp.confirm_removals(bad, read=ctx.read, titles=titles, kinds=kinds)
    return (PAUSED, "") if answer is None else answer


def _confirm_remote(
    bad: list[str],
    kinds: dict[str, str],
    titles: dict[str, str],
    ctx: _Ctx,
    draft: _Draft,
) -> set[str] | tuple[str, str]:
    """Без терминала: ответы копятся в кандидате; нет ответа — `WAITING`."""
    assert ctx.ask is not None
    answers = _answers(ctx.candidate)
    allowed = rp.confirm_remote(
        bad, stage=ctx.stage, titles=titles, kinds=kinds, ask=ctx.ask, answers=answers
    )
    if ctx.store is not None:
        _save(ctx.store, draft, answers)
    if allowed is not None:
        return allowed
    item = next(i for i in bad if i not in answers)
    q = rp.confirm_question(
        ctx.stage, item, kinds.get(item, rp.REMOVE), titles.get(item, "")
    )
    return WAITING, pending.wait_line(q)


def _confirm_resume(
    bad: list[str],
    kinds: dict[str, str],
    titles: dict[str, str],
    ctx: _Ctx,
    draft: _Draft,
) -> set[str] | tuple[str, str]:
    """Терминал продолжает кандидата: ответ из `dfl reply` или живой;
    `/стоп` — `PAUSED`, кандидат с полученными ответами остаётся."""
    assert ctx.store is not None
    answers = _answers(ctx.candidate)
    for item in bad:
        if item in answers:
            continue
        got = _resume_one(item, kinds, titles, ctx.store, ctx)
        if got is None:
            break
        answers[item] = got
    _save(ctx.store, draft, answers)
    if any(item not in answers for item in bad):
        return PAUSED, ""
    return {item for item in bad if answers[item]}


def _resume_one(
    item: str, kinds: dict[str, str], titles: dict[str, str], store: Path, ctx: _Ctx
) -> bool | None:
    """Ответ по одному id: записанный `dfl reply`, иначе живой (тогда
    записанный вопрос снимается); `None` — `/стоп`."""
    kind = kinds.get(item, rp.REMOVE)
    q = rp.confirm_question(ctx.stage, item, kind, titles.get(item, ""))
    recorded = pending.take(store, q)
    parsed = None if recorded is None else rp.confirm_answer(recorded, kind)
    if parsed is not None:
        return parsed
    live = rp.confirm_removals([item], read=ctx.read, titles=titles, kinds=kinds)
    if live is None:
        return None
    pending.discard(store)
    return item in live


def _answers(candidate: dict | None) -> dict[str, bool]:
    """Ответы, уже сохранённые в кандидате."""
    raw = (candidate or {}).get("answers") or {}
    return {str(k): v for k, v in raw.items() if isinstance(v, bool)}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(file: Path) -> dict | None:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(store: Path, draft: _Draft, answers: dict[str, bool]) -> None:
    """Записать кандидата атомарно (tmp + rename)."""
    data = {
        "path": draft.name,
        "before": _digest(draft.before),
        "decisions": draft.decisions,
        "after": draft.after,
        "answers": answers,
    }
    file = store / CANDIDATE_FILE
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file)
