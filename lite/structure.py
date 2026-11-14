"""Раскладка ответа интервью: агент выделяет пункты, скрипт проверяет и собирает.

Агент (`claude -p`, без инструментов) получает вопрос, ответ человека,
действующие пункты сессии и правила полей и возвращает JSON
`{"items": [...], "ask": str|null}`. Скрипт проверяет форму (битая форма —
один повтор агента с текстом ошибки), смысл по правилам брифа (дыра —
уточнение у человека), назначает id и собирает YAML для `discovery answer`.
Контракт брифа v1.2: слова человека — `Preference` дословно, критерий —
гипотеза агента, недостающее число — открытый `Q` исследования того же ответа
(числа у человека не спрашиваются).
Пункт агента с `id` действующего пункта — обновление: его поля сливаются с
прежней записью, id сохраняется, запись переобъявляется в текущем ответе
(discovery записывает это как `replaces_entries`). Человек не видит ни JSON,
ни YAML, ни id пунктов.
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
_TEXT_FIELDS = ("priority", "preference", "acceptance", "target", "owner_role")
# поле пункта → поле записи discovery (порядок — порядок в YAML)
_ENTRY_FIELDS = (("priority", "Priority"), ("preference", "Preference"),
                 ("acceptance", "Acceptance"), ("target", "Target"),
                 ("owner_role", "owner_role"), ("blocking", "blocking"))  # fmt: skip
_ID_RE = re.compile(r"^[A-Z]+-\d+$")
_Q_REF_RE = re.compile(r"\bQ-\d+\b")  # как GC-17 в vendored gate_check
# открытый вопрос исследования (v1.2): число ищет аналитик или инженер
RESEARCH_PREFIX = "Q"
RESEARCH_ROLES = ("analyst", "engineer")
_REFERRING = ("FR", "NFR", "M")  # GC-17: чьи ссылки на Q-NN проверяются
_REF_TEXT = ("body", "acceptance", "target")  # где пишет агент (не preference)
_REF_SHAPE = r"q-\d+"  # грамматика ref нового Q из промпта: «q-1»


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
    items = [_merged(item, view) if "id" in item else item for item in items]
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
        return _ask_agent(agent, f"{prompt}\n\n{retry_note(str(exc))}", view)


def _ask_agent(
    agent: Agent, prompt: str, view: SessionView
) -> tuple[list[dict[str, Any]], str | None]:
    """Один вызов агента с проверкой формы; нарушение — `FormError`."""
    items, ask = parse_items(agent(prompt))
    errors = form_errors(items, view) or criterion_errors(items, view)
    if errors:
        raise FormError("; ".join(errors))
    return items, ask


def retry_note(error: str) -> str:
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
        f"- {e.id} ({_topic_note(_prefix_of(e.id), topics)}): {e.body}"
        + _fields_note(e.fields)
        for e in view.entries
    )
    return f"""\
Ты аналитик-переводчик: раскладываешь ответ человека в интервью о требованиях
на пункты. Человек говорит на простом языке (предпочтения), ты переводишь это
на технический язык (ограничения). Числа у человека не спрашивай: это
исследование, а не вопрос одному человеку.

Вопрос (тема {q.coverage_key}): {q.text}
{need}

Ответ человека (вместе с уточнениями):
<<<
{text}
>>>

Уже записанные пункты (на них можно ссылаться в traces, не дублируй их):
{existing or "- (пока нет)"}
Если ответ дополняет или уточняет уже записанный пункт (например, его
важность, критерий проверки, целевое значение) — не создавай новый: верни
этот пункт с его "id" и тем же "prefix", его body (прежним или уточнённым) и
только теми полями, которые ответ заполняет или меняет. Новым пунктам "id" не
давай.

Допустимые префиксы пунктов (пункт может относиться к любой теме, не только
к теме вопроса):
{allowed}
Префикс Q — только открытый вопрос исследования (см. ниже). Служебный
префикс X и любые другие не используй.

Правила полей:
- body — суть пункта одной-двумя фразами, словами человека.
- FR (функция): traces — список id целей G или задач J, к которым она
  относится; priority — одно из Must, Should, Could, Won't; acceptance —
  как проверить, что функция работает.
- NFR: acceptance (критерий) или target (целевое значение).
- M (метрика): traces — список id целей G, которые она измеряет; target —
  целевое значение.
- FR, NFR, M: preference — слова человека дословно (цитата из ответа, без
  пересказа), если он выразил предпочтение («чтобы было бесшовно»);
  acceptance/target — твоя гипотеза-перевод этого предпочтения в проверяемое
  ограничение: пиши её сам, у человека не спрашивай (у FR и NFR критерий
  обязателен).
- Если для гипотезы нужно число (порог, baseline, целевое значение), а
  человек его не назвал, — не выдумывай и не спрашивай: заведи в этом же
  ответе пункт Q (открытый вопрос исследования): body — какое значение
  найти, owner_role — analyst (данные, бизнес) или engineer (техника), свой
  "ref" (например "q-1"). В критерии сошлись на него этим ref:
  «… не хуже значения из q-1» — скрипт подставит id. Ссылаться на Q-NN
  можно только на уже записанные пункты Q из списка выше.
- IF (интерфейс): traces — список id систем S, к которым он относится.
- AP (архитектурное предпочтение): traces — список id систем S или
  ограничений CON, которые оно затрагивает.
- Остальные префиксы: достаточно body.
- traces ссылаются только на id из списка выше или на ref нового пункта
  этого же ответа: дай новому пункту поле "ref" (например "new-1") и укажи
  этот ref в traces другого пункта. Id новым пунктам не придумывай —
  их назначит скрипт.

Главное правило: не выдумывай. body и preference — только из сказанного
человеком; acceptance/target — перевод сказанного, неизвестное число —
ссылка на Q. Чего в ответе нет по смыслу (важности, цели, кому это нужно) —
оставь поле пустым (null) и задай человеку один короткий уточняющий вопрос в
"ask" — простыми словами, без id, префиксов и терминов. Не спрашивай чисел,
порогов и того, как проверять: это твой перевод. Если всего хватает —
"ask": null. Если человек говорит «не знаю» или «пропустить» — не спрашивай.

Верни только JSON, без пояснений:
{{"items": [{{"prefix": "FR", "body": "...", "ref": "new-1", "traces": ["G-01"],
"priority": "Must", "preference": "...", "acceptance": "...", "target": null}},
{{"prefix": "Q", "body": "...", "ref": "q-1", "owner_role": "analyst"}}],
"ask": null}}
"""


def parse_items(raw: str) -> tuple[list[dict[str, Any]], str | None]:
    """Первый JSON-объект из вывода агента → (пункты, уточнение).

    Пункт нормализуется: строки обрезаются, пустые поля выбрасываются,
    `traces` всегда список, `priority` приводится к написанию брифа;
    необязательный `id` (обновление действующего пункта) и `ref` сохраняются,
    прочие ключи
    отбрасываются. Нарушение схемы — `FormError`.
    """
    doc = first_json_object(raw)
    items = doc.get("items")
    if not isinstance(items, list):
        raise FormError('поле "items" должно быть списком')
    ask = doc.get("ask")
    if ask is not None and not isinstance(ask, str):
        raise FormError('поле "ask" должно быть строкой или null')
    parsed = [_parse_item(item, n) for n, item in enumerate(items, 1)]
    return parsed, (ask or "").strip() or None


def first_json_object(raw: str) -> dict[str, Any]:
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
    if out["prefix"] != RESEARCH_PREFIX:
        out.pop("owner_role", None)  # роль исследователя — только у Q
    elif "id" not in out:
        out["blocking"] = False  # v1.2: Q исследования гейт не держит
    return out


def _canonical_priority(value: str, n: int) -> str:
    """Приоритет без учёта регистра → написание брифа."""
    for canon in PRIORITIES:
        if value.lower() == canon.lower():
            return canon
    raise FormError(f"пункт {n}: priority «{value}» не из {', '.join(PRIORITIES)}")


def form_errors(items: list[dict[str, Any]], view: SessionView) -> list[str]:
    """Нарушения формы, которые исправляет агент: префикс, ref, traces, id,
    роль и ссылки на `Q` (GC-09, GC-17)."""
    allowed = {**view.prefixes(), RESEARCH_PREFIX: "open_questions"}
    known = {e.id for e in view.entries}
    refs = [i["ref"] for i in items if "ref" in i]
    errors = [f"ref «{r}» повторяется или похож на id" for r in _bad_refs(refs, known)]
    errors += _id_errors(items, known)
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
    q_refs = {i["ref"] for i in items if "ref" in i and i["prefix"] == RESEARCH_PREFIX}
    for n, item in enumerate(items, 1):
        errors += _research_errors(item, n, known, q_refs, set(refs))
    return errors


def _research_errors(
    item: dict[str, Any], n: int, known: set[str], q_refs: set[str], refs: set[str]
) -> list[str]:
    """Q — с ролью исследователя; ссылки FR/NFR/M (GC-17): Q-NN — на
    записанный Q, ref — на Q этого же ответа; в preference id не бывает."""
    if item["prefix"] == RESEARCH_PREFIX:
        if item.get("owner_role") in RESEARCH_ROLES:
            return []
        return [f"пункт {n}: у Q owner_role — одно из {', '.join(RESEARCH_ROLES)}"]
    if item["prefix"] not in _REFERRING:
        return []
    text = " ".join(str(item.get(key, "")) for key in _REF_TEXT)
    errors = [
        f"пункт {n}: ссылка на {ref}, но такого Q нет — заведи Q в этом ответе "
        "и сошлись на его ref"
        for ref in dict.fromkeys(_Q_REF_RE.findall(text))
        if ref not in known
    ]
    errors += [
        f"пункт {n}: «{ref}» — не ref пункта Q этого ответа; ссылайся только "
        "на ref заведённого здесь Q"
        for ref in dict.fromkeys(_ref_pattern(refs).findall(text))
        if ref not in q_refs
    ]
    errors += [
        f"пункт {n}: preference — дословные слова человека, id {ref} в них "
        "быть не может; ссылку пиши в критерии"
        for ref in dict.fromkeys(_Q_REF_RE.findall(item.get("preference", "")))
    ]
    return errors


def _ref_pattern(refs: set[str]) -> re.Pattern[str]:
    """Отдельно стоящий ref: форма ref Q (`q-1`) или ровно ref пункта этого
    ответа; прочие «слово-число» (utf-8, iso-8601) — обычный текст."""
    alts = [_REF_SHAPE, *map(re.escape, sorted(refs, key=len, reverse=True))]
    return re.compile(r"(?<![\w-])(" + "|".join(alts) + r")(?![\w-])")


def criterion_errors(items: list[dict[str, Any]], view: SessionView) -> list[str]:
    """FR без acceptance, NFR без acceptance/target (итог с прежней записью
    для обновления) — повтор агента: критерий — его гипотеза (GC-08, v1.2).
    Вызывать после `form_errors` без ошибок (id обновлений действующие)."""
    errors = []
    for n, item in enumerate(items, 1):
        whole = _merged(item, view) if "id" in item else item
        if _criterion_missing(whole):
            errors.append(
                f"пункт {n}: у {item['prefix']} нет критерия — напиши сам "
                "гипотезу acceptance/target по словам человека (не спрашивай "
                "его); не хватает числа — заведи Q и сошлись на его ref"
            )
    return errors


def _criterion_missing(item: dict[str, Any]) -> bool:
    """FR без acceptance; NFR без acceptance и target."""
    if item["prefix"] == "FR":
        return "acceptance" not in item
    return item["prefix"] == "NFR" and not ({"acceptance", "target"} & item.keys())


def _id_errors(items: list[dict[str, Any]], known: set[str]) -> list[str]:
    """`id` пункта — только действующий пункт того же префикса, без повторов."""
    ids = [i["id"] for i in items if "id" in i]
    errors = [f"id «{i}» повторяется" for i in sorted(set(ids)) if ids.count(i) > 1]
    for n, item in enumerate(items, 1):
        eid = item.get("id")
        if eid is None:
            continue
        if eid not in known:
            errors.append(f"пункт {n}: id «{eid}» нет среди записанных пунктов")
        elif _prefix_of(eid) != item["prefix"]:
            errors.append(f"пункт {n}: префикс {item['prefix']} не совпадает с {eid}")
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
    """Недостающие поля пункта (GC-06…GC-08, GC-13).

    Критерий и значение (GC-08) у человека не спрашиваются: это перевод
    агента, недостающее число — `Q` исследования (контракт v1.2). Его
    отсутствие названо без вопроса — для `dfl correct`; в интервью оно
    раньше ловится `criterion_errors`."""
    body, prefix = item["body"], item["prefix"]
    gaps = []
    if _criterion_missing(item):
        gaps.append(f"Не задано, как проверить «{body}» — критерий предложит агент.")
    if prefix == "FR":
        if not traced & {"G", "J"}:
            gaps.append(f"Какой цели или задаче служит «{body}»?")
        if "priority" not in item:
            gaps.append(
                f"Насколько важно «{body}»: обязательно, желательно, "
                "можно позже или не делаем?"
            )
    elif prefix == "M" and "G" not in traced:
        gaps.append(f"Какую цель измеряет «{body}»?")
    elif prefix == "IF" and "S" not in traced:
        gaps.append(f"К какой системе относится «{body}»?")
    elif prefix == "AP" and not traced & {"S", "CON"}:
        gaps.append(f"Какую систему или какое ограничение затрагивает «{body}»?")
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

    Новому пункту — следующий номер префикса после всех объявленных в сессии,
    по порядку пунктов; пункт с `id` (обновление) — прежняя запись держателя
    с полями пункта, id прежний; `ref` в traces заменяется назначенным id.
    """
    ids = _assign_ids(items, view)
    by_ref = {i["ref"]: ids[n] for n, i in enumerate(items) if "ref" in i}
    q_refs = {r: eid for r, eid in by_ref.items() if _prefix_of(eid) == RESEARCH_PREFIX}
    entries = [
        _entry(
            _with_q_ids(item, q_refs),
            ids[n],
            by_ref,
            view.entry_record(ids[n]) if "id" in item else {},
        )
        for n, item in enumerate(items)
    ]
    doc: dict[str, Any] = {"text": text}
    if entries:
        doc["entries"] = entries
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _with_q_ids(item: dict[str, Any], q_refs: dict[str, str]) -> dict[str, Any]:
    """ref нового Q в тексте пункта → назначенный id (`q-1` → `Q-01`)."""
    if not q_refs:
        return item
    pattern = re.compile(
        r"(?<![\w-])(" + "|".join(map(re.escape, q_refs)) + r")(?![\w-])"
    )
    out = dict(item)
    for key in _REF_TEXT:
        if key in out:
            out[key] = pattern.sub(lambda m: q_refs[m.group(1)], out[key])
    return out


def _assign_ids(items: list[dict[str, Any]], view: SessionView) -> list[str]:
    """id по порядку пунктов; внутри ответа номера префикса растут;
    обновление сохраняет свой id."""
    taken: dict[str, int] = {}
    ids = []
    for item in items:
        if "id" in item:
            ids.append(item["id"])
            continue
        prefix = item["prefix"]
        if prefix not in taken:
            taken[prefix] = int(view.next_id(prefix).rsplit("-", 1)[1])
        ids.append(f"{prefix}-{taken[prefix]:02d}")
        taken[prefix] += 1
    return ids


def _entry(
    item: dict[str, Any], eid: str, by_ref: dict[str, str], old: dict[str, Any]
) -> dict[str, Any]:
    """Пункт → запись `entries` поверх прежней записи `old` (пустые поля
    опущены)."""
    entry = merge_entry({**old, "id": eid}, item)
    if "traces" in entry:
        entry["traces"] = [by_ref.get(t, t) for t in entry["traces"]]
    return entry


def merge_entry(old: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Прежняя запись с полями пункта: body; traces и поля — если есть
    (id и неизвестные dfl поля прежней записи сохраняются)."""
    new = dict(old)
    new["body"] = item["body"]
    if item.get("traces"):
        new["traces"] = item["traces"]
    for key, name in _ENTRY_FIELDS:
        if key in item:
            new[name] = item[key]
    return new


def entry_as_item(entry: dict[str, Any]) -> dict[str, Any]:
    """Запись ответа → пункт в формате раскладки (для проверки полей)."""
    item: dict[str, Any] = {
        "prefix": _prefix_of(str(entry["id"])),
        "body": entry["body"],
        "traces": list(entry.get("traces") or []),
    }
    for key, name in _ENTRY_FIELDS:
        if entry.get(name):
            item[key] = entry[name]
    return item


def _merged(item: dict[str, Any], view: SessionView) -> dict[str, Any]:
    """Обновление действующего пункта → пункт со слитыми полями (id и ref
    сохраняются): проверка и YAML видят итог, а не только присланное."""
    merged = entry_as_item(merge_entry(view.entry_record(item["id"]), item))
    merged["id"] = item["id"]
    if "ref" in item:
        merged["ref"] = item["ref"]
    return merged


def _topic_note(prefix: str, topics: dict[str, str]) -> str:
    """`тема goals` для пункта темы; `исследование` для открытого Q."""
    if prefix == RESEARCH_PREFIX:
        return "исследование"
    return f"тема {topics.get(prefix, '?')}"


def _fields_note(fields: dict[str, Any]) -> str:
    """` (Priority: Must; traces: G-01)` — поля пункта для промпта."""
    parts = [
        f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
        for k, v in fields.items()
    ]
    return f" ({'; '.join(parts)})" if parts else ""


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
