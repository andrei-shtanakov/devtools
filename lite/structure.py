"""Раскладка ответа интервью: агент выделяет пункты, скрипт проверяет и собирает.

Агент (`claude -p`, без инструментов) получает вопрос, ответ человека,
действующие пункты сессии и правила полей и возвращает JSON
`{"items": [...], "ask": str|null}`. Скрипт проверяет форму (битая форма —
один повтор агента с текстом ошибки), смысл по правилам брифа (дыра —
уточнение у человека), назначает id и собирает YAML для `discovery answer`.
Человек не видит ни JSON, ни YAML, ни id пунктов.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import yaml

from lite.interview_state import SessionView

Agent = Callable[[str], str]

AGENT_TIMEOUT = 300
PRIORITIES = ("Must", "Should", "Could", "Won't")
FAILED_TEXT = "не удалось разобрать ответ — переформулируйте"
TOPIC_LABELS = {
    "goals": "цели",
    "personas": "пользователи",
    "jobs": "задачи пользователей",
    "functions": "функции",
    "nfr": "нефункциональные требования",
    "constraints": "ограничения",
    "success_metrics": "метрики успеха",
    "out_of_scope": "что не входит",
    "risks": "риски",
    "systems": "системы",
    "interfaces": "интерфейсы",
    "arch_preferences": "архитектурные предпочтения",
    "feasibility_review": "оценка реализуемости",
}
_TEXT_FIELDS = ("priority", "acceptance", "target")
# поле пункта → поле записи discovery (порядок — порядок в YAML)
_ENTRY_FIELDS = (("priority", "Priority"), ("acceptance", "Acceptance"),
                 ("target", "Target"))  # fmt: skip
_ID_RE = re.compile(r"^[A-Z]+-\d+$")


class FormError(Exception):
    """Ответ агента не по схеме; текст — причина для повтора агента."""


class AgentUnavailable(Exception):
    """Агент не запустился, не уложился во время или упал; текст — причина.

    Не повторяется: переформулировка человеку тут не поможет.
    """


@dataclass(frozen=True)
class Question:
    """Текущий вопрос интервью."""

    id: str
    coverage_key: str
    text: str


@dataclass(frozen=True)
class Ready:
    """Ответ собран: YAML для `discovery answer`."""

    payload: str


@dataclass(frozen=True)
class Ask:
    """Нужно уточнение у человека."""

    question: str


@dataclass(frozen=True)
class Failed:
    """Ответ разобрать не удалось; текст — для человека."""

    message: str


def structure_answer(
    q: Question, text: str, view: SessionView, agent: Agent, *, may_ask: bool
) -> Ready | Ask | Failed:
    """Разложить ответ человека на пункты и собрать YAML ответа.

    Нарушение формы ответа агента — один повтор с текстом ошибки, затем
    `Failed`; агент недоступен (`AgentUnavailable`) — сразу `Failed` с
    причиной, без повтора. Уточнение агента или смысловая дыра — `Ask`, если `may_ask`;
    иначе отправляется то, что есть (без выдуманного).
    """
    try:
        items, ask = _ask_with_retry(agent, build_prompt(q, text, view), view)
    except FormError:
        return Failed(FAILED_TEXT)
    except AgentUnavailable as exc:
        return Failed(f"агент недоступен: {exc}")
    if may_ask:
        if ask:
            return Ask(ask)
        gaps = validate(items, q, view)
        if gaps:
            return Ask("\n".join(gaps))
    return Ready(to_payload(text, items, view))


def _ask_with_retry(
    agent: Agent, prompt: str, view: SessionView
) -> tuple[list[dict[str, Any]], str | None]:
    """Вызов агента; нарушение формы — один повтор с текстом ошибки."""
    try:
        return _ask_agent(agent, prompt, view)
    except FormError as exc:
        return _ask_agent(agent, f"{prompt}\n\n{_retry_note(str(exc))}", view)


def _ask_agent(
    agent: Agent, prompt: str, view: SessionView
) -> tuple[list[dict[str, Any]], str | None]:
    """Один вызов агента с проверкой формы; нарушение — `FormError`."""
    items, ask = parse_items(agent(prompt))
    errors = form_errors(items, view)
    if errors:
        raise FormError("; ".join(errors))
    return items, ask


def _retry_note(error: str) -> str:
    """Добавка к промпту повтора: почему прошлый ответ отклонён."""
    return (
        f"Твой предыдущий ответ отклонён: {error}. "
        "Верни исправленный JSON строго по схеме выше, без пояснений."
    )


def build_prompt(q: Question, text: str, view: SessionView) -> str:
    """Промпт агента: вопрос, ответ, действующие пункты, правила, схема."""
    topics = view.prefixes()
    prefix = _required_prefix(q, view)
    if not prefix:
        need = "Эта тема покрывается самим ответом: пункты не обязательны."
    elif q.coverage_key in view.covered():
        need = (
            f"Тема уже раскрыта: новый пункт с префиксом {prefix} — только если "
            "ответ добавляет что-то новое."
        )
    else:
        need = f"Для этой темы нужен хотя бы один пункт с префиксом {prefix}."
    allowed = "\n".join(f"- {p} — тема {k}" for p, k in topics.items())
    existing = "\n".join(
        f"- {e.id} (тема {topics.get(_prefix_of(e.id), '?')}): {e.body}"
        for e in view.entries
    )
    return f"""\
Ты раскладываешь ответ человека в интервью о требованиях на пункты.

Вопрос (тема {q.coverage_key}): {q.text}
{need}

Ответ человека (вместе с уточнениями):
<<<
{text}
>>>

Уже записанные пункты (на них можно ссылаться в traces, не дублируй их):
{existing or "- (пока нет)"}

Допустимые префиксы пунктов (пункт может относиться к любой теме, не только
к теме вопроса):
{allowed}
Служебные префиксы Q и X и любые другие не используй.

Правила полей:
- body — суть пункта одной-двумя фразами, словами человека.
- FR (функция): traces — список id целей G или задач J, к которым она
  относится; priority — одно из Must, Should, Could, Won't; acceptance —
  как проверить, что функция работает.
- NFR: acceptance (критерий) или target (целевое значение).
- M (метрика): traces — список id целей G, которые она измеряет.
- Остальные префиксы: достаточно body.
- traces ссылаются только на id из списка выше или на ref нового пункта
  этого же ответа: дай новому пункту поле "ref" (например "new-1") и укажи
  этот ref в traces другого пункта. Id новым пунктам не придумывай —
  их назначит скрипт.

Главное правило: не выдумывай. Пиши пункты и значения полей только из
сказанного человеком. Чего в ответе нет (критерия приёмки, важности, цели) —
оставь поле пустым (null) и задай человеку один короткий уточняющий вопрос в
"ask" — простыми словами, без id, префиксов и терминов. Если всего хватает —
"ask": null. Если человек говорит «не знаю» или «пропустить» — не спрашивай.

Верни только JSON, без пояснений:
{{"items": [{{"prefix": "FR", "body": "...", "ref": "new-1", "traces": ["G-01"],
"priority": "Must", "acceptance": "...", "target": null}}], "ask": null}}
"""


def parse_items(raw: str) -> tuple[list[dict[str, Any]], str | None]:
    """Первый JSON-объект из вывода агента → (пункты, уточнение).

    Пункт нормализуется: строки обрезаются, пустые поля выбрасываются,
    `traces` всегда список, `priority` приводится к написанию брифа;
    необязательный `id` (поправки) и `ref` сохраняются, прочие ключи
    отбрасываются. Нарушение схемы — `FormError`.
    """
    doc = _first_json_object(raw)
    items = doc.get("items")
    if not isinstance(items, list):
        raise FormError('поле "items" должно быть списком')
    ask = doc.get("ask")
    if ask is not None and not isinstance(ask, str):
        raise FormError('поле "ask" должно быть строкой или null')
    parsed = [_parse_item(item, n) for n, item in enumerate(items, 1)]
    return parsed, (ask or "").strip() or None


def _first_json_object(raw: str) -> dict[str, Any]:
    """Первый JSON-объект в тексте (ограды ``` и проза вокруг допустимы)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            obj, _ = decoder.raw_decode(raw, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise FormError("в ответе нет JSON-объекта")


def _parse_item(item: Any, n: int) -> dict[str, Any]:
    """Один пункт агента → нормализованный словарь."""
    if not isinstance(item, dict):
        raise FormError(f"пункт {n} не объект")
    out: dict[str, Any] = {}
    for key in ("prefix", "body"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FormError(f'пункт {n}: нужно непустое строковое поле "{key}"')
        out[key] = value.strip()
    traces = item.get("traces") or []
    if not isinstance(traces, list) or not all(isinstance(t, str) for t in traces):
        raise FormError(f'пункт {n}: "traces" должен быть списком строк')
    out["traces"] = [t.strip() for t in traces]
    for key in (*_TEXT_FIELDS, "ref", "id"):
        value = item.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise FormError(f'пункт {n}: поле "{key}" должно быть строкой или null')
        if value.strip():
            out[key] = value.strip()
    if "priority" in out:
        out["priority"] = _canonical_priority(out["priority"], n)
    return out


def _canonical_priority(value: str, n: int) -> str:
    """Приоритет без учёта регистра → написание брифа."""
    for canon in PRIORITIES:
        if value.lower() == canon.lower():
            return canon
    raise FormError(f"пункт {n}: priority «{value}» не из {', '.join(PRIORITIES)}")


def form_errors(items: list[dict[str, Any]], view: SessionView) -> list[str]:
    """Нарушения формы, которые исправляет агент: префикс, ref, traces."""
    allowed = view.prefixes()
    known = {e.id for e in view.entries}
    refs = [i["ref"] for i in items if "ref" in i]
    errors = [f"ref «{r}» повторяется или похож на id" for r in _bad_refs(refs, known)]
    for n, item in enumerate(items, 1):
        if item["prefix"] not in allowed:
            errors.append(
                f"пункт {n}: префикс {item['prefix']} недопустим "
                f"(можно: {', '.join(allowed)})"
            )
        errors += [
            f"пункт {n}: traces → {t}, такого пункта нет"
            for t in item.get("traces", [])
            if t not in known and t not in refs
        ]
    return errors


def _bad_refs(refs: list[str], known: set[str]) -> list[str]:
    """Повторяющиеся ref и ref в форме id (их не отличить от ссылки на пункт)."""
    seen: set[str] = set()
    bad = []
    for ref in refs:
        if ref in seen or ref in known or _ID_RE.match(ref):
            bad.append(ref)
        seen.add(ref)
    return bad


def validate(items: list[dict[str, Any]], q: Question, view: SessionView) -> list[str]:
    """Смысловые дыры по правилам брифа — уточнения для человека (без id).

    Пункт с префиксом темы требуется, только пока тема не покрыта: на
    следующие вопросы покрытой темы ответ может и не дать нового пункта."""
    gaps = []
    prefix = _required_prefix(q, view)
    uncovered = q.coverage_key not in view.covered()
    if prefix and uncovered and not any(i["prefix"] == prefix for i in items):
        label = TOPIC_LABELS.get(q.coverage_key, q.coverage_key)
        gaps.append(f"Не понял из ответа {label}. {q.text}")
    targets = _trace_prefixes(items)
    for item in items:
        gaps += _item_gaps(item, targets(item))
    return gaps


def _item_gaps(item: dict[str, Any], traced: set[str]) -> list[str]:
    """Недостающие обязательные поля пункта (GC-06…GC-08)."""
    body, prefix = item["body"], item["prefix"]
    gaps = []
    if prefix == "FR":
        if not traced & {"G", "J"}:
            gaps.append(f"Какой цели или задаче служит «{body}»?")
        if "priority" not in item:
            gaps.append(
                f"Насколько важно «{body}»: обязательно, желательно, "
                "можно позже или не делаем?"
            )
        if "acceptance" not in item:
            gaps.append(f"Как проверить, что «{body}» работает?")
    elif prefix == "NFR" and not ({"acceptance", "target"} & item.keys()):
        gaps.append(f"Как проверить «{body}» — какой критерий или целевое значение?")
    elif prefix == "M" and "G" not in traced:
        gaps.append(f"Какую цель измеряет «{body}»?")
    return gaps


def _trace_prefixes(
    items: list[dict[str, Any]],
) -> Callable[[dict[str, Any]], set[str]]:
    """Префиксы, на которые ведут traces пункта (ref → префикс нового пункта)."""
    by_ref = {i["ref"]: i["prefix"] for i in items if "ref" in i}

    def prefixes(item: dict[str, Any]) -> set[str]:
        return {by_ref.get(t) or _prefix_of(t) for t in item.get("traces", [])}

    return prefixes


def to_payload(text: str, items: list[dict[str, Any]], view: SessionView) -> str:
    """YAML ответа: `text` дословно и `entries` с назначенными id.

    id — следующий номер префикса после всех объявленных в сессии, по
    порядку пунктов; присланный агентом `id` игнорируется; `ref` в traces
    заменяется назначенным id.
    """
    ids = _assign_ids(items, view)
    by_ref = {i["ref"]: ids[n] for n, i in enumerate(items) if "ref" in i}
    entries = [_entry(item, ids[n], by_ref) for n, item in enumerate(items)]
    doc: dict[str, Any] = {"text": text}
    if entries:
        doc["entries"] = entries
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _assign_ids(items: list[dict[str, Any]], view: SessionView) -> list[str]:
    """id по порядку пунктов; внутри ответа номера префикса растут."""
    taken: dict[str, int] = {}
    ids = []
    for item in items:
        prefix = item["prefix"]
        if prefix not in taken:
            taken[prefix] = int(view.next_id(prefix).rsplit("-", 1)[1])
        ids.append(f"{prefix}-{taken[prefix]:02d}")
        taken[prefix] += 1
    return ids


def _entry(item: dict[str, Any], eid: str, by_ref: dict[str, str]) -> dict[str, Any]:
    """Пункт → запись `entries` (пустые поля опущены)."""
    entry: dict[str, Any] = {"id": eid, "body": item["body"]}
    if item.get("traces"):
        entry["traces"] = [by_ref.get(t, t) for t in item["traces"]]
    for key, name in _ENTRY_FIELDS:
        if key in item:
            entry[name] = item[key]
    return entry


def _required_prefix(q: Question, view: SessionView) -> str | None:
    """Префикс, покрывающий тему вопроса (None — тема без префикса)."""
    return {**view.required, **view.optional}.get(q.coverage_key)


def _prefix_of(eid: str) -> str:
    """Префикс id (`FR-01` → `FR`)."""
    return eid.rsplit("-", 1)[0]


def claude_agent(model: str) -> Agent:
    """Агент на `claude -p`: без инструментов, `--safe-mode`, промпт в stdin.

    Сбой запуска, таймаут или ненулевой код — `AgentUnavailable` с причиной
    (первая строка stderr или ошибки), не повтор.
    """
    argv = ["claude", "-p", "--safe-mode", "--tools", "",
            "--model", model, "--output-format", "text"]  # fmt: skip

    def agent(prompt: str) -> str:
        try:
            done = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=AGENT_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentUnavailable(f"нет ответа за {AGENT_TIMEOUT} с") from exc
        except OSError as exc:
            raise AgentUnavailable(_first_line(str(exc)) or repr(exc)) from exc
        if done.returncode != 0:
            reason = _first_line(done.stderr or "")
            raise AgentUnavailable(reason or f"код {done.returncode}")
        return done.stdout

    return agent


def _first_line(text: str) -> str:
    """Первая непустая строка текста (причина для человека)."""
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
