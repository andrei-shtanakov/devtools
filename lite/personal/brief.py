"""Light brief of the personal path: data, JSON parsing, rendering, own check.

`brief_from_json` accepts exactly this shape (the interviewer agent returns it):

    {"goals": [{"id": "G-01", "title": "...", "text": "..."}],
     "functions": [{"id": "FR-01", "title": "...", "goals": ["G-01"],
                    "criteria": ["..."], "words": "..."}],
     "context": {"users": "...", "auth": "...", "network": "...",
                 "local": "...", "platform": "...", "other": ""},
     "open_items": ["..."]}

`open_items` and `context.other` are optional. A criterion is referenced later
as `FR-NN/k`, k = 1-based index in `criteria` (order is preserved).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from lite.structure import FormError

CONTEXT_LABELS: tuple[tuple[str, str], ...] = (
    ("users", "Пользователи"),
    ("auth", "Авторизация"),
    ("network", "Сеть"),
    ("local", "Локальная система"),
    ("platform", "Платформа"),
)
OTHER_LABEL = "Прочее"


@dataclass(frozen=True)
class Goal:
    """A goal: an outcome, not a feature."""

    id: str
    title: str
    text: str


@dataclass(frozen=True)
class Function:
    """A function (FR) with acceptance criteria and the owner's own words."""

    id: str
    title: str
    goals: tuple[str, ...]
    criteria: tuple[str, ...]
    words: str


@dataclass(frozen=True)
class Context:
    """Five mandatory context lines plus optional free-form facts."""

    users: str
    auth: str
    network: str
    local: str
    platform: str
    other: str = ""


@dataclass(frozen=True)
class Brief:
    """The light brief (`00-brief.md`)."""

    goals: tuple[Goal, ...]
    functions: tuple[Function, ...]
    context: Context
    open_items: tuple[str, ...] = ()


def _str(obj: object, key: str, where: str) -> str:
    if not isinstance(obj, dict) or not isinstance(obj.get(key), str):
        raise FormError(f"{where}: поле «{key}» должно быть строкой")
    return obj[key]


def _str_list(obj: object, key: str, where: str) -> tuple[str, ...]:
    items = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise FormError(f"{where}: поле «{key}» должно быть списком строк")
    return tuple(items)


def _items(data: dict, key: str) -> list:
    items = data.get(key)
    if not isinstance(items, list):
        raise FormError(f"бриф: поле «{key}» должно быть списком")
    return items


def _goal(obj: object) -> Goal:
    return Goal(
        _str(obj, "id", "цель"), _str(obj, "title", "цель"), _str(obj, "text", "цель")
    )


def _function(obj: object) -> Function:
    where = "функция"
    return Function(
        _str(obj, "id", where),
        _str(obj, "title", where),
        _str_list(obj, "goals", where),
        _str_list(obj, "criteria", where),
        _str(obj, "words", where),
    )


def _context(obj: object) -> Context:
    if not isinstance(obj, dict):
        raise FormError("бриф: поле «context» должно быть объектом")
    values = {k: _str(obj, k, "context") for k, _ in CONTEXT_LABELS}
    other = _str(obj, "other", "context") if "other" in obj else ""
    return Context(**values, other=other)


def brief_from_json(data: object) -> Brief:
    """Parse the interviewer's JSON into a `Brief`; `FormError` on bad shape."""
    if not isinstance(data, dict):
        raise FormError("бриф: ожидается JSON-объект")
    open_items = _str_list(data, "open_items", "бриф") if "open_items" in data else ()
    return Brief(
        goals=tuple(_goal(g) for g in _items(data, "goals")),
        functions=tuple(_function(f) for f in _items(data, "functions")),
        context=_context(data.get("context")),
        open_items=open_items,
    )


def _render_goal(g: Goal) -> str:
    return f"### {g.id}: {g.title}\n{g.text}\n"


def _render_function(f: Function) -> str:
    criteria = "\n".join(f"- {c}" for c in f.criteria)
    return (
        f"### {f.id}: {f.title}\n"
        f"**Цели:** {', '.join(f.goals)}\n"
        f"**Критерии приёмки:**\n{criteria}\n"
        f"**Слова владельца:** {f.words}\n"
    )


def _render_context(c: Context) -> str:
    lines = [f"- {label}: {getattr(c, key)}" for key, label in CONTEXT_LABELS]
    if c.other:
        lines.append(f"- {OTHER_LABEL}: {c.other}")
    return "\n".join(lines) + "\n"


def render_brief(b: Brief) -> str:
    """Render the brief in the exact Markdown format of spec §4.3."""
    parts = [
        "## Цели\n\n" + "\n".join(_render_goal(g) for g in b.goals),
        "## Функции\n\n" + "\n".join(_render_function(f) for f in b.functions),
        "## Контекст\n\n" + _render_context(b.context),
    ]
    if b.open_items:
        parts.append(
            "## Уточнить\n\n" + "\n".join(f"- {i}" for i in b.open_items) + "\n"
        )
    return "\n".join(parts)


def _id_problems(ids: list[str], prefix: str) -> list[str]:
    problems = []
    for pos, item in enumerate(ids, start=1):
        expected = f"{prefix}-{pos:02d}"
        if ids.count(item) > 1:
            problems.append(f"{item}: id не уникален")
        elif item != expected:
            problems.append(f"{item}: id не по порядку, ожидался {expected}")
    return problems


def _has_criteria(f: Function) -> bool:
    return any(c.strip() for c in f.criteria)


def _function_problems(f: Function, goal_ids: set[str]) -> list[str]:
    problems = []
    if not _has_criteria(f):
        problems.append(f"{f.id}: нет критерия приёмки")
    if not f.title.strip():
        problems.append(f"{f.id}: пустое название функции")
    problems += [
        f"{f.id}: критерий {k} пуст"
        for k, c in enumerate(f.criteria, start=1)
        if not c.strip()
    ]
    if not f.goals:
        problems.append(f"{f.id}: нет ссылки на цель")
    problems += [
        f"{f.id}: цель {g} не существует" for g in f.goals if g not in goal_ids
    ]
    return problems


def brief_problems(b: Brief) -> list[str]:
    """Own check of spec §4.3; each problem text names the offending id."""
    problems = []
    if not b.goals:
        problems.append("бриф: нет ни одной цели (G-01)")
    if not b.functions:
        problems.append("бриф: нет ни одной функции (FR-01)")
    problems += _id_problems([g.id for g in b.goals], "G")
    problems += _id_problems([f.id for f in b.functions], "FR")
    for g in b.goals:
        if not g.title.strip():
            problems.append(f"{g.id}: пустое название цели")
        if not g.text.strip():
            problems.append(f"{g.id}: пустой текст цели")
    goal_ids = {g.id for g in b.goals}
    for f in b.functions:
        problems += _function_problems(f, goal_ids)
    problems += [
        f"контекст: пустое поле «{label}»"
        for key, label in CONTEXT_LABELS
        if not getattr(b.context, key).strip()
    ]
    return problems


def demote_uncovered(b: Brief) -> Brief:
    """Move FRs without criteria into `open_items` (they stay for the owner).

    The note names the function by title only: kept FRs are renumbered, so
    the old id may now denote another function."""
    bare = [f for f in b.functions if not _has_criteria(f)]
    if not bare:
        return b
    notes = tuple(f"{f.title}: нет критерия приёмки" for f in bare)
    kept = [f for f in b.functions if _has_criteria(f)]
    return replace(
        b,
        functions=tuple(
            replace(f, id=f"FR-{pos:02d}") for pos, f in enumerate(kept, start=1)
        ),
        open_items=b.open_items + notes,
    )
