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

import re
from collections.abc import Mapping

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


#: Строка верхнего уровня frontmatter, открывающая ключ: без отступа,
#: не комментарий, `ключ:` с пробелом или концом строки после двоеточия.
_TOP_KEY_RE = re.compile(r"^([^\s#\-][^:]*?):(?:\s|$)")


def update_frontmatter(text: str, updates: Mapping[str, object]) -> str:
    """Переписать ТОЛЬКО названные ключи frontmatter; остальные байты — как были.

    Штампы конвейера (`approve_node`: candidate, stale-каскад, finalize)
    меняют по смыслу две-четыре величины, а `join_frontmatter` переписывал
    весь блок в каноне `yaml.safe_dump`: flow-список становился блочным, с
    хэшей слетали кавычки, и человек перед подписью читал шум вместо
    содержательных строк (@id:approval-stamp-frontmatter-roundtrip, S7
    2026-09-21). Здесь заменяется диапазон строк изменившегося ключа —
    от его строки до следующего ключа верхнего уровня либо комментария в
    первой колонке; ключ, значение которого совпадает с уже записанным,
    не трогается вовсе; отсутствующий — дописывается в конец блока.

    Изменённый ключ рендерится каноном `safe_dump` — авторская форма
    именно этого ключа не сохраняется, и это честно: значение сменилось.

    Fail-closed против класса «0 замен выглядит как успех» (ретроспектива
    2026-09-02): результат ПЕРЕЧИТЫВАЕТСЯ парсером и обязан дать ровно
    `meta | updates`, иначе `ValueError` без записи. Дубль ключа,
    многострочная авторская форма, которую регекс не увидел, — всё ловится
    этой сверкой, а не доверием к замене.
    """
    meta, _body = split_frontmatter(text)
    head, sep, rest = text[4:].partition("\n---\n")
    assert sep, "split_frontmatter уже проверил разделитель"
    lines = head.split("\n")
    starts = [
        (i, m.group(1).strip()) for i, line in enumerate(lines)
        if (m := _TOP_KEY_RE.match(line))
    ]
    boundaries = sorted(
        {i for i, _ in starts}
        | {i for i, line in enumerate(lines) if line.startswith("#")}
        | {len(lines)}
    )

    def span(start: int) -> int:
        return next(b for b in boundaries if b > start)

    def render(key: str) -> list[str]:
        dumped = yaml.safe_dump(
            {key: updates[key]}, sort_keys=False, allow_unicode=True,
            default_flow_style=False,
        )
        return dumped.rstrip("\n").split("\n")

    replaced: list[tuple[int, int, list[str]]] = []
    for key in updates:
        if key in meta and meta[key] == updates[key]:
            continue
        found = [i for i, k in starts if k == key]
        if len(found) > 1:
            raise ValueError(f"frontmatter: ключ {key!r} объявлен дважды")
        if found:
            replaced.append((found[0], span(found[0]), render(key)))
        else:
            replaced.append((len(lines), len(lines), render(key)))
    for start, end, new_lines in sorted(replaced, reverse=True):
        lines[start:end] = new_lines
    result = "---\n" + "\n".join(lines) + "\n---\n" + rest

    check, _ = split_frontmatter(result)
    expected = dict(meta)
    expected.update(updates)
    if check != expected:
        raise ValueError(
            "правка frontmatter не сошлась с ожидаемой: перечитано "
            f"{check!r}, ожидалось {expected!r} — запись не выполняется"
        )
    return result
