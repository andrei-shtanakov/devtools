"""Авторинг узлов личного пути: дизайн и декомпозиция (спека §4.4).

Цепочка: `00-brief.md` → `20-design.md` → `30-decomposition.md`; edge-check
только между соседями (`EDGES`). Автор идёт через ту же границу
`launch_author`, что и в полной цепочке, но со своими промптами
(`prompts/design.md`, `prompts/decomposition.md`) и правилами своего ребра.
Виды узлов — `personal-design` / `personal-decomposition`: под этими
`subject_role` лежат правила, поэтому автор полной цепочки
(`rules_for("design")`) их не получает.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from governance.author_launch import AuthorCall, LaunchOutcome, launch_author
from governance.edge_check.rules import load_rules
from lite.personal.state import PersonalRun, run_dir

PROMPTS = Path(__file__).parent / "prompts"

#: Роль/вид узла → файл в бандле. `brief`/`design` — роли оснований рёбер
#: (`lite.edge_resolve._bases` читает их через эту же таблицу).
FILENAMES = {
    "brief": "00-brief.md",
    "design": "20-design.md",
    "personal-design": "20-design.md",
    "personal-decomposition": "30-decomposition.md",
}

#: Вид узла → (ребро edge-check, основания `(роль, файл)`).
EDGES = {
    "personal-design": ("personal-design-vs-brief", [("brief", "00-brief.md")]),
    "personal-decomposition": (
        "personal-decomposition-vs-design-brief",
        [("design", "20-design.md"), ("brief", "00-brief.md")],
    ),
}

_PROMPT_FILES = {
    "personal-design": "design.md",
    "personal-decomposition": "decomposition.md",
}

Launch = Callable[..., LaunchOutcome]

#: Тексты промптов автора, которые добавляет код, по языку прогона.
_TEXTS = {
    "ru": {
        "subject": "## Тема проекта (слова человека)\n\n{subject}",
        "bases": (
            "## Основания (читать из репозитория, пути от рабочего каталога)\n\n{paths}"
        ),
        "where": (
            "## Куда писать\n\n"
            "Запиши РОВНО один файл: {output} (этот абсолютный путь; "
            "он станет {target}). В репозиторий "
            "писать нельзя, больше ничего нигде не пиши."
        ),
        "revise": (
            "## Режим правки\n\n"
            "Перепиши файл, исправив ТОЛЬКО замечания ниже. Решения человека "
            "применяй дословно. Где человек не согласен с замечанием, оставь текст "
            "и запиши рядом строку «Решение заказчика: … Причина: …». Не удаляй и "
            "не ослабляй проверки, задачи и решения сверх того, что прямо "
            "разрешают решения человека.\n"
            "\n--- ТЕКУЩИЙ УЗЕЛ ---\n{current}\n"
            "\n--- ЗАМЕЧАНИЯ ---\n{findings}\n"
            "\n--- РЕШЕНИЯ ЧЕЛОВЕКА ---\n{decisions}\n"
        ),
        "rules": (
            "## Правила проверки (ребро {edge})\n\nУзел проверят по ним:\n{items}"
        ),
    },
    "en": {
        "subject": "## Project topic (the person's words)\n\n{subject}",
        "bases": (
            "## Bases (read from the repository, paths from the working directory)"
            "\n\n{paths}"
        ),
        "where": (
            "## Where to write\n\n"
            "Write EXACTLY one file: {output} (this absolute path; "
            "it will become {target}). Do not write into the repository, "
            "do not write anything else anywhere."
        ),
        "revise": (
            "## Revision mode\n\n"
            "Rewrite the file, fixing ONLY the findings below. Apply the person's "
            "decisions verbatim. Where the person disagrees with a finding, keep "
            "the text and write next to it a line \"Owner's decision: … Reason: "
            '…". Do not remove or weaken checks, tasks and decisions beyond '
            "what the person's decisions explicitly allow.\n"
            "\n--- CURRENT NODE ---\n{current}\n"
            "\n--- FINDINGS ---\n{findings}\n"
            "\n--- PERSON'S DECISIONS ---\n{decisions}\n"
        ),
        "rules": (
            "## Check rules (edge {edge})\n\n"
            "The node will be checked against them:\n{items}"
        ),
    },
}


def _prompts_dir(lang: str) -> Path:
    """Каталог инструкций авторов: русские — `prompts/`, английские — `en/`."""
    return PROMPTS / "en" if lang == "en" else PROMPTS


def author_node(
    run: PersonalRun, kind: str, *, launch: Launch = launch_author, lang: str = "ru"
) -> LaunchOutcome:
    """Написать узел `kind` личного прогона через границу автора (промпт на
    языке `lang`)."""
    call = AuthorCall(
        run_id=run.run_id,
        run_dir=os.path.realpath(run_dir(run.run_id)),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        filename=_filename(kind),
    )
    return launch(
        call,
        lambda out: build_prompt(kind, run.subject, run.bundle_dir, out, lang=lang),
    )


def revise_node(
    call: AuthorCall,
    kind: str,
    subject: str,
    current: str,
    findings: str,
    decisions: str,
    *,
    launch: Launch = launch_author,
    lang: str = "ru",
) -> LaunchOutcome:
    """Переписать узел по замечаниям edge-check (сигнатура `edge_resolve.Revise`;
    `lang` — язык промпта)."""
    _filename(kind)
    return launch(
        call,
        lambda out: revise_prompt(
            kind, subject, call.bundle_dir, out, current, findings, decisions, lang=lang
        ),
    )


def build_prompt(
    kind: str, subject: str, bundle_dir: str, output_path: str, *, lang: str = "ru"
) -> str:
    """Промпт автора узла: инструкция вида, основания, правила ребра, куда писать.

    `lang` — язык прогона: инструкция, заголовки и каталог правил."""
    edge_id, bases = EDGES[kind]
    texts = _TEXTS[lang]
    paths = "\n".join(f"- {role}: {bundle_dir}/{name}" for role, name in bases)
    instruction = _prompts_dir(lang) / _PROMPT_FILES[kind]
    target = f"{bundle_dir}/{FILENAMES[kind]}"
    return "\n\n".join(
        [
            instruction.read_text(encoding="utf-8"),
            texts["subject"].format(subject=subject),
            texts["bases"].format(paths=paths),
            _rules_block(edge_id, lang),
            texts["where"].format(output=output_path, target=target),
        ]
    )


def revise_prompt(
    kind: str,
    subject: str,
    bundle_dir: str,
    output_path: str,
    current: str,
    findings: str,
    decisions: str,
    *,
    lang: str = "ru",
) -> str:
    """Промпт переписывания узла по замечаниям и решениям человека."""
    base = build_prompt(kind, subject, bundle_dir, output_path, lang=lang)
    revise = _TEXTS[lang]["revise"].format(
        current=current, findings=findings, decisions=decisions
    )
    return f"{base}\n\n{revise}"


def _filename(kind: str) -> str:
    """Файл узла личного пути; чужой вид — отказ (ошибка вызывающего)."""
    if kind not in EDGES:
        raise ValueError(f"вид узла {kind!r} не из личного пути: {sorted(EDGES)}")
    return FILENAMES[kind]


def _rules_block(edge_id: str, lang: str = "ru") -> str:
    """Пункты правил ребра — те же, по которым узел потом проверит edge-check."""
    from lite.personal.check import contracts_dir  # check импортирует nodes

    rules = load_rules(edge_id, contracts_dir(lang))
    items = "\n".join(f"- {it.id}: {it.text}" for it in rules.items)
    return _TEXTS[lang]["rules"].format(edge=edge_id, items=items)
