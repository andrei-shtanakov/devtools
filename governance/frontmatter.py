"""Разбор и сборка YAML-frontmatter узла бандла — одно определение на всех.

Функции жили в `task_bridge` и оттуда же переехали БЕЗ изменения тела:
предикат честной одобренности (§I12, `node_approval`) обязан считать
каноническую проекцию узла ТЕМ ЖЕ рендером, которым узел записывается на
диск, а импортировать ради этого мост значило бы завести цикл — мост сам
зовёт проверку одобренности. `task_bridge` продолжает экспортировать оба
имени (`from governance.frontmatter import …`), поэтому вызывающие,
включая тесты, не знают о переезде.
"""

from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[dict, str]:
    """YAML-frontmatter → (meta, body); файл без frontmatter — ValueError.

    Перепиновка и штампы делаются ПАРСЕРОМ, не текстовой заменой
    (ретроспектива 2026-09-02, @id:spec-bridge-approve-conformance):
    sed по инлайн-форме `{requirements: "…"}` молча промахнулся и пустил
    stale-пин в коммит — «0 замен» у текстовых замен выглядит как успех.

    ЛЮБОЙ отказ разбора приходит одним типом `ValueError`, включая сбой
    самого YAML-парсера: `yaml.YAMLError` — не подкласс `ValueError`, и
    вызывающие, которые ловят «frontmatter не разобрать» (§I6/§I8), мимо
    него проваливались сырым трейсбеком вместо своего fail-closed.
    """
    if not text.startswith("---\n"):
        raise ValueError("нет YAML-frontmatter (файл не начинается с '---')")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ValueError("frontmatter не закрыт разделителем '---'")
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter — невалидный YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("frontmatter — не YAML-маппинг")
    return meta, body.lstrip("\n")


def join_frontmatter(meta: dict, body: str) -> str:
    """(meta, body) → текст файла; ключи в порядке вставки, без сортировки."""
    dumped = yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n\n{body}"
