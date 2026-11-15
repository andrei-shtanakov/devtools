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
from governance.edge_check.author_rules import DEFAULT_CONTRACTS
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


def author_node(
    run: PersonalRun, kind: str, *, launch: Launch = launch_author
) -> LaunchOutcome:
    """Написать узел `kind` личного прогона через границу автора."""
    call = AuthorCall(
        run_id=run.run_id,
        run_dir=os.path.realpath(run_dir(run.run_id)),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        filename=_filename(kind),
    )
    return launch(
        call, lambda out: build_prompt(kind, run.subject, run.bundle_dir, out)
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
) -> LaunchOutcome:
    """Переписать узел по замечаниям edge-check (сигнатура `edge_resolve.Revise`)."""
    _filename(kind)
    return launch(
        call,
        lambda out: revise_prompt(
            kind, subject, call.bundle_dir, out, current, findings, decisions
        ),
    )


def build_prompt(kind: str, subject: str, bundle_dir: str, output_path: str) -> str:
    """Промпт автора узла: инструкция вида, основания, правила ребра, куда писать."""
    edge_id, bases = EDGES[kind]
    paths = "\n".join(f"- {role}: {bundle_dir}/{name}" for role, name in bases)
    return "\n\n".join(
        [
            (PROMPTS / _PROMPT_FILES[kind]).read_text(encoding="utf-8"),
            f"## Тема проекта (слова человека)\n\n{subject}",
            (
                "## Основания (читать из репозитория, пути от рабочего каталога)"
                f"\n\n{paths}"
            ),
            _rules_block(edge_id),
            (
                "## Куда писать\n\n"
                f"Запиши РОВНО один файл: {output_path} (этот абсолютный путь; "
                f"он станет {bundle_dir}/{FILENAMES[kind]}). В репозиторий "
                "писать нельзя, больше ничего нигде не пиши."
            ),
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
) -> str:
    """Промпт переписывания узла по замечаниям и решениям человека."""
    base = build_prompt(kind, subject, bundle_dir, output_path)
    return (
        f"{base}\n\n"
        "## Режим правки\n\n"
        "Перепиши файл, исправив ТОЛЬКО замечания ниже. Решения человека "
        "применяй дословно. Где человек не согласен с замечанием, оставь текст "
        "и запиши рядом строку «Решение заказчика: … Причина: …». Не удаляй и "
        "не ослабляй проверки, задачи и решения сверх того, что прямо "
        "разрешают решения человека.\n"
        f"\n--- ТЕКУЩИЙ УЗЕЛ ---\n{current}\n"
        f"\n--- ЗАМЕЧАНИЯ ---\n{findings}\n"
        f"\n--- РЕШЕНИЯ ЧЕЛОВЕКА ---\n{decisions}\n"
    )


def _filename(kind: str) -> str:
    """Файл узла личного пути; чужой вид — отказ (ошибка вызывающего)."""
    if kind not in EDGES:
        raise ValueError(f"вид узла {kind!r} не из личного пути: {sorted(EDGES)}")
    return FILENAMES[kind]


def _rules_block(edge_id: str) -> str:
    """Пункты правил ребра — те же, по которым узел потом проверит edge-check."""
    rules = load_rules(edge_id, DEFAULT_CONTRACTS)
    items = "\n".join(f"- {it.id}: {it.text}" for it in rules.items)
    return f"## Правила проверки (ребро {edge_id})\n\nУзел проверят по ним:\n{items}"
