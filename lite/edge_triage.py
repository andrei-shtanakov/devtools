"""Сортировка блокирующих замечаний edge-check агентом.

Агент без инструментов (текст на входе — JSON на выходе) разбирает каждую
находку: `fix` — агент правит сам, не спрашивая; `decision` — вопрос
человеку с 2–3 вариантами, один рекомендован. Ничего не правит и не
показывает — только сортирует.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lite.edge_journal import Decision, Finding
from lite.structure import Agent, FormError, first_json_object

CACHE_DIR = "edge-triage"
KINDS = ("fix", "decision")
MIN_OPTIONS, MAX_OPTIONS = 2, 3


@dataclass(frozen=True)
class Option:
    """Вариант решения; `removes`/`downgrades` — id, которым он грозит."""

    label: str
    recommended: bool
    removes: list[str]
    downgrades: list[str]


@dataclass(frozen=True)
class Item:
    """Пункт разбора: правка без вопроса или вопрос с вариантами."""

    finding: Finding
    kind: str  # "fix" | "decision"
    question: str
    options: list[Option]


def triage(
    findings: list[Finding],
    nodes: dict[str, str],
    bases: dict[str, str],
    prior: list[Decision],
    agent: Agent,
    *,
    cache_dir: Path | None = None,
) -> list[Item]:
    """Отсортировать находки на `fix`/`decision`.

    `nodes`/`bases` — имя файла → текст. `cache_dir` — каталог прогона:
    результат кладётся в `edge-triage/<sha256>.json` и при том же хэше входа
    читается вместо вызова агента. Ответ не по схеме — `FormError`,
    агент недоступен — `AgentUnavailable`.
    """
    cache = _cache_path(cache_dir, findings, nodes, bases, prior)
    if cache is not None and cache.is_file():
        try:
            return _from_json(findings, json.loads(cache.read_text("utf-8")))
        except (ValueError, FormError):
            pass  # битый кэш — спросим агента заново
    raw = first_json_object(agent(_prompt(findings, nodes, bases, prior)))
    items = _parse(findings, raw, {d.key for d in prior})
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(_to_json(items), encoding="utf-8")
    return items


def _cache_path(
    cache_dir: Path | None,
    findings: list[Finding],
    nodes: dict[str, str],
    bases: dict[str, str],
    prior: list[Decision],
) -> Path | None:
    """Путь кэша по хэшу входа; без каталога — кэша нет."""
    if cache_dir is None:
        return None
    payload = {
        "findings": [asdict(f) for f in findings],
        "nodes": nodes,
        "bases": bases,
        "prior": [asdict(d) for d in prior],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return cache_dir / CACHE_DIR / f"{digest}.json"


def _parse(
    findings: list[Finding], raw: dict[str, Any], decided: set[tuple[str, str, str]]
) -> list[Item]:
    """Ответ агента → пункты по порядку находок; находка с прошлым решением
    всегда `fix` (код, а не промпт, гарантирует «не спрашивать дважды»)."""
    entries = raw.get("items")
    if not isinstance(entries, list):
        raise FormError('нет списка "items"')
    by_no: dict[int, Item] = {}
    for entry in entries:
        no, item = _parse_item(findings, entry)
        if no in by_no:
            raise FormError(f"находка {no} разобрана дважды")
        by_no[no] = item
    missing = [n for n in range(1, len(findings) + 1) if n not in by_no]
    if missing:
        raise FormError(f"не разобраны находки: {missing}")
    return [
        _forced_fix(by_no[n]) if by_no[n].finding.key in decided else by_no[n]
        for n in range(1, len(findings) + 1)
    ]


def _forced_fix(item: Item) -> Item:
    """Пункт без вопроса: решение человека уже есть."""
    return Item(item.finding, "fix", "", [])


def _parse_item(findings: list[Finding], entry: Any) -> tuple[int, Item]:
    """Один элемент `items` → (номер находки, пункт)."""
    if not isinstance(entry, dict):
        raise FormError("элемент items не объект")
    no = entry.get("finding")
    if isinstance(no, bool) or not isinstance(no, int) or not 1 <= no <= len(findings):
        raise FormError(f"неизвестная находка: {no!r}")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise FormError(f'находка {no}: kind должен быть "fix" или "decision"')
    finding = findings[no - 1]
    if kind == "fix":
        return no, Item(finding, "fix", "", [])
    question = entry.get("question")
    if not isinstance(question, str) or not question.strip():
        raise FormError(f"находка {no}: у decision нет вопроса")
    return no, Item(finding, "decision", question, _parse_options(no, entry))


def _parse_options(no: int, entry: dict[str, Any]) -> list[Option]:
    """Варианты decision: 2–3 штуки, ровно один рекомендован."""
    raw = entry.get("options")
    if not isinstance(raw, list) or not MIN_OPTIONS <= len(raw) <= MAX_OPTIONS:
        raise FormError(f"находка {no}: нужно {MIN_OPTIONS}–{MAX_OPTIONS} варианта")
    options = [_parse_option(no, o) for o in raw]
    if sum(o.recommended for o in options) != 1:
        raise FormError(f"находка {no}: рекомендован должен быть ровно один вариант")
    return options


def _parse_option(no: int, raw: Any) -> Option:
    """Один вариант; `removes`/`downgrades` — списки строк (по умолчанию [])."""
    if not isinstance(raw, dict):
        raise FormError(f"находка {no}: вариант не объект")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise FormError(f"находка {no}: у варианта нет label")
    return Option(
        label,
        raw.get("recommended") is True,
        _strings(no, raw, "removes"),
        _strings(no, raw, "downgrades"),
    )


def _strings(no: int, raw: dict[str, Any], field: str) -> list[str]:
    """Поле-список строк варианта."""
    value = raw.get(field, [])
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise FormError(f"находка {no}: {field} должен быть списком строк")
    return list(value)


def _to_json(items: list[Item]) -> str:
    """Пункты → JSON кэша (формат ответа агента, номера по порядку)."""
    rows = [
        {
            "finding": n,
            "kind": i.kind,
            "question": i.question,
            "options": [asdict(o) for o in i.options],
        }
        for n, i in enumerate(items, 1)
    ]
    return json.dumps({"items": rows}, ensure_ascii=False)


def _from_json(findings: list[Finding], raw: dict[str, Any]) -> list[Item]:
    """Кэш → пункты. Прошлые решения уже учтены при записи."""
    return _parse(findings, raw, set())


def _prompt(
    findings: list[Finding],
    nodes: dict[str, str],
    bases: dict[str, str],
    prior: list[Decision],
) -> str:
    """Промпт агента: пронумерованные находки, тексты, прошлые решения."""
    listed = "\n".join(
        f"{n}. [{f.cls}] узел {f.node}, ребро {f.edge}, правило {f.rule_id}, "
        f"{f.path or '-'}:{f.lines}\n   {f.statement}"
        for n, f in enumerate(findings, 1)
    )
    decided = "\n".join(
        f"- {' / '.join(d.key)}: вопрос «{d.question}», ответ «{d.answer}»"
        for d in prior
    )
    return f"""\
Ты сортируешь замечания смыслового ревью (edge-check) к узлу спецификации.
Каждое замечание — ровно один пункт: `fix` или `decision`.

- `fix` — исправление очевидно из текста и оснований; агент правит сам, не
  спрашивая человека. `question` пустой, `options` пустой.
- `decision` — нужен выбор человека: вопрос и 2–3 варианта, ровно один из
  них `recommended: true`. У варианта `removes` — id удаляемых требований,
  `downgrades` — id требований, у которых понижается Must.

Правила:
1. Удаление требования, понижение Must и снятие сценария допустимы только
   как `decision`, где вариант заявляет это в `removes`/`downgrades`.
   Никогда не `fix`.
2. Если причина замечания лежит в утверждённом узле выше по цепочке (чужой
   id, цель вне основания), менять его нельзя: это `decision` с вариантами
   внутри текущего узла.
3. Для замечания с прошлым решением человека вопрос не нужен — `fix`.

Ответь одним JSON-объектом без прозы:
{{"items": [{{"finding": <номер>, "kind": "fix"|"decision",
"question": "...", "options": [{{"label": "...", "recommended": true,
"removes": [], "downgrades": []}}]}}]}}

Замечания:
{listed}

Прошлые решения человека:
{decided or "нет"}

Тексты узлов:
{_files(nodes)}

Тексты оснований:
{_files(bases)}
"""


def _files(files: dict[str, str]) -> str:
    """Файлы блоками «=== имя ===»."""
    return "\n".join(f"=== {name} ===\n{text}" for name, text in files.items())
