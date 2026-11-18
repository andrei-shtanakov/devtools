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
CONTEXT_LABELS_EN: tuple[tuple[str, str], ...] = (
    ("users", "Users"),
    ("auth", "Authorization"),
    ("network", "Network"),
    ("local", "Local system"),
    ("platform", "Platform"),
)
OTHER_LABEL_EN = "Other"

#: Заголовки и метки `render_brief` по языку прогона.
_HEADINGS = {
    "ru": {
        "goals": "Цели",
        "functions": "Функции",
        "context": "Контекст",
        "open": "Уточнить",
        "fn_goals": "Цели",
        "criteria": "Критерии приёмки",
        "words": "Слова владельца",
    },
    "en": {
        "goals": "Goals",
        "functions": "Functions",
        "context": "Context",
        "open": "To clarify",
        "fn_goals": "Goals",
        "criteria": "Acceptance criteria",
        "words": "Owner's words",
    },
}

#: Тексты `brief_problems`/`demote_uncovered` по языку: уходят агенту
#: (реплика `check`) и в раздел «Уточнить» брифа.
_PROBLEMS = {
    "ru": {
        "no_goal": "бриф: нет ни одной цели (G-01)",
        "no_function": "бриф: нет ни одной функции (FR-01)",
        "not_unique": "{item}: id не уникален",
        "out_of_order": "{item}: id не по порядку, ожидался {expected}",
        "empty_goal_title": "{id}: пустое название цели",
        "empty_goal_text": "{id}: пустой текст цели",
        "no_criterion": "{id}: нет критерия приёмки",
        "empty_fn_title": "{id}: пустое название функции",
        "empty_criterion": "{id}: критерий {k} пуст",
        "no_goal_ref": "{id}: нет ссылки на цель",
        "unknown_goal": "{id}: цель {goal} не существует",
        "empty_context": "контекст: пустое поле «{label}»",
        "untitled": "функция без названия",
        "demoted": "{title}: нет критерия приёмки",
    },
    "en": {
        "no_goal": "brief: no goal at all (G-01)",
        "no_function": "brief: no function at all (FR-01)",
        "not_unique": "{item}: id is not unique",
        "out_of_order": "{item}: id out of order, expected {expected}",
        "empty_goal_title": "{id}: empty goal title",
        "empty_goal_text": "{id}: empty goal text",
        "no_criterion": "{id}: no acceptance criterion",
        "empty_fn_title": "{id}: empty function title",
        "empty_criterion": "{id}: criterion {k} is empty",
        "no_goal_ref": "{id}: no reference to a goal",
        "unknown_goal": "{id}: goal {goal} does not exist",
        "empty_context": 'context: empty field "{label}"',
        "untitled": "untitled function",
        "demoted": "{title}: no acceptance criterion",
    },
}


def _context_labels(lang: str) -> tuple[tuple[str, str], ...]:
    return CONTEXT_LABELS_EN if lang == "en" else CONTEXT_LABELS


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


def _render_function(f: Function, h: dict[str, str]) -> str:
    criteria = "\n".join(f"- {c}" for c in f.criteria)
    return (
        f"### {f.id}: {f.title}\n"
        f"**{h['fn_goals']}:** {', '.join(f.goals)}\n"
        f"**{h['criteria']}:**\n{criteria}\n"
        f"**{h['words']}:** {f.words}\n"
    )


def _render_context(c: Context, lang: str) -> str:
    lines = [f"- {label}: {getattr(c, key)}" for key, label in _context_labels(lang)]
    if c.other:
        other = OTHER_LABEL_EN if lang == "en" else OTHER_LABEL
        lines.append(f"- {other}: {c.other}")
    return "\n".join(lines) + "\n"


def render_brief(b: Brief, *, lang: str = "ru") -> str:
    """Render the brief in the exact Markdown format of spec §4.3.

    `lang` — run language: headings and labels (`ru` by default)."""
    h = _HEADINGS[lang]
    functions = "\n".join(_render_function(f, h) for f in b.functions)
    parts = [
        f"## {h['goals']}\n\n" + "\n".join(_render_goal(g) for g in b.goals),
        f"## {h['functions']}\n\n" + functions,
        f"## {h['context']}\n\n" + _render_context(b.context, lang),
    ]
    if b.open_items:
        items = "\n".join(f"- {i}" for i in b.open_items)
        parts.append(f"## {h['open']}\n\n" + items + "\n")
    return "\n".join(parts)


def _id_problems(ids: list[str], prefix: str, p: dict[str, str]) -> list[str]:
    problems = []
    for pos, item in enumerate(ids, start=1):
        expected = f"{prefix}-{pos:02d}"
        if ids.count(item) > 1:
            problems.append(p["not_unique"].format(item=item))
        elif item != expected:
            problems.append(p["out_of_order"].format(item=item, expected=expected))
    return problems


def _has_criteria(f: Function) -> bool:
    return any(c.strip() for c in f.criteria)


def _function_problems(f: Function, goal_ids: set[str], p: dict[str, str]) -> list[str]:
    problems = []
    if not _has_criteria(f):
        problems.append(p["no_criterion"].format(id=f.id))
    if not f.title.strip():
        problems.append(p["empty_fn_title"].format(id=f.id))
    problems += [
        p["empty_criterion"].format(id=f.id, k=k)
        for k, c in enumerate(f.criteria, start=1)
        if not c.strip()
    ]
    if not f.goals:
        problems.append(p["no_goal_ref"].format(id=f.id))
    problems += [
        p["unknown_goal"].format(id=f.id, goal=g) for g in f.goals if g not in goal_ids
    ]
    return problems


def brief_problems(b: Brief, *, lang: str = "ru") -> list[str]:
    """Own check of spec §4.3; each problem text names the offending id.

    `lang` — run language of the texts (they go to the interviewer agent)."""
    p = _PROBLEMS[lang]
    problems = []
    if not b.goals:
        problems.append(p["no_goal"])
    if not b.functions:
        problems.append(p["no_function"])
    problems += _id_problems([g.id for g in b.goals], "G", p)
    problems += _id_problems([f.id for f in b.functions], "FR", p)
    for g in b.goals:
        if not g.title.strip():
            problems.append(p["empty_goal_title"].format(id=g.id))
        if not g.text.strip():
            problems.append(p["empty_goal_text"].format(id=g.id))
    goal_ids = {g.id for g in b.goals}
    for f in b.functions:
        problems += _function_problems(f, goal_ids, p)
    problems += [
        p["empty_context"].format(label=label)
        for key, label in _context_labels(lang)
        if not getattr(b.context, key).strip()
    ]
    return problems


def demote_uncovered(b: Brief, *, lang: str = "ru") -> Brief:
    """Move FRs without criteria into `open_items` (they stay for the owner).

    The note names the function by title only: kept FRs are renumbered, so
    the old id may now denote another function. `lang` — language of the note."""
    bare = [f for f in b.functions if not _has_criteria(f)]
    if not bare:
        return b
    p = _PROBLEMS[lang]
    notes = tuple(
        p["demoted"].format(title=f.title.strip() or p["untitled"]) for f in bare
    )
    kept = [f for f in b.functions if _has_criteria(f)]
    return replace(
        b,
        functions=tuple(
            replace(f, id=f"FR-{pos:02d}") for pos, f in enumerate(kept, start=1)
        ),
        open_items=b.open_items + notes,
    )
