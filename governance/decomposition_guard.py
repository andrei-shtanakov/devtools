"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DT_HEAD_RE = re.compile(
    r"^####\s+(DT-\d+):\s*(.+?)\s*·\s*type:\s*(implement|verify)"
    r"\s*·\s*owner:\s*(\S+)\s*$",
    re.M,
)
# near-miss: начинается как DT-заголовок, но строгую грамматику не прошёл
_DT_NEAR_RE = re.compile(r"^####\s+(DT-\d+)\b.*$", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class DtTask:
    """Одна задача decomposition-узла (заголовок #### DT-NN)."""

    dt_id: str
    title: str
    type: str
    owner: str
    scenarios: tuple[str, ...]
    depends_on: tuple[str, ...]
    delivered_by: tuple[str, ...]
    parallel_group: str


def parse_dt_tasks(text: str) -> tuple[list[DtTask], list[str]]:
    """DT-грамматика → (задачи, findings формы).

    Findings формы (не графа — граф в graph_findings): near-miss
    заголовок, дубль DT-id, отсутствие scenarios/depends_on/
    parallel_group, пустой scenarios. Блок задачи ограничен следующим
    DT-заголовком ЛИБО следующей секцией уровня 1–3 (урок major'а
    PR #145 — хвост документа не читается как метаданные последней
    задачи).
    """
    findings: list[str] = []
    strict = {m.start() for m in _DT_HEAD_RE.finditer(text)}
    for near in _DT_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике DT (`#### DT-NN: <название> · type: "
                "implement|verify · owner: <роль>`)"
            )
    matches = list(_DT_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    tasks: list[DtTask] = []
    for idx, m in enumerate(matches):
        dt_id = m.group(1)
        seen[dt_id] = seen.get(dt_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        scenarios = _list_field(block, "scenarios")
        depends_on = _list_field(block, "depends_on")
        delivered_by = _list_field(block, "delivered_by") or ()
        group_m = re.search(r"^parallel_group:\s*(\S+)\s*$", block, re.M)
        if scenarios is None or not scenarios:
            findings.append(f"{dt_id}: строка scenarios отсутствует или пуста")
        if depends_on is None:
            findings.append(f"{dt_id}: строка depends_on отсутствует")
        if group_m is None:
            findings.append(f"{dt_id}: строка parallel_group отсутствует")
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=m.group(3),
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
        ))
    for dt_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{dt_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return tasks, findings


# Та же строгая грамматика, что _BEH_HEADER моста (`: <название>`
# обязательны) — minor круга 4: расхождение (гард видит `#### BEH-02` без
# двоеточия, мост — нет) давало бы зелёный гейт и пустую задачу в
# tasks-спеке.
_BEH_HEAD_RE = re.compile(r"^####\s+(BEH-\d+):\s*\S", re.M)
_BEH_NEAR_RE = re.compile(r"^####\s+(BEH-\d+)\b", re.M)
_BEH_CHECKED_RE = re.compile(
    r"\*\*checked_by\*\*.*?`kind:\s*(\S+?)`.*?`target:\s*(\S+?)`"
)


def _parse_beh_bindings(text: str) -> dict[str, tuple[str | None, str | None]]:
    """beh_id → (файл checked_by-цели, kind); `::селектор` отброшен.

    Дубликат грамматики task_bridge._CHECKED (фактическое имя константы
    моста) намеренный и запинован тестом согласованности
    (test_beh_binding_grammar_matches_task_bridge): гард обязан остаться
    чистым модулем без импорта task_bridge (канон design_guard), а
    расхождение грамматик ловится тестом, не ревьюером. Берётся
    ПОСЛЕДНЕЕ вхождение checked_by в блоке — как у построчного разбора
    моста, где новая строка перетирает предыдущую (minor круга 2:
    расхождение «первое против последнего» пропускало бы single-owner
    по неактуальной цели).
    """
    heads = list(_BEH_HEAD_RE.finditer(text))
    result: dict[str, tuple[str | None, str | None]] = {}
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        block = text[m.start() : end]
        checked = None
        for checked in _BEH_CHECKED_RE.finditer(block):
            pass  # последнее вхождение — как у моста
        if checked is None:
            result[m.group(1)] = (None, None)
        else:
            target = checked.group(2).split("::", 1)[0]
            result[m.group(1)] = (target, checked.group(1))
    return result


def _transitive_deps(
    start: str, edges: dict[str, tuple[str, ...]]
) -> set[str]:
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return seen


def graph_findings(behaviour_text: str, decomposition_text: str) -> list[str]:
    """Инварианты графа DT (§3 спеки) + findings формы парсера.

    Порядок проверок фиксирован, findings накапливаются (гейт показывает
    всё сразу, не по одной). Пустой список — граф валиден.
    """
    tasks, findings = parse_dt_tasks(decomposition_text)
    bindings = _parse_beh_bindings(behaviour_text)
    ids = {t.dt_id for t in tasks}
    edges = {t.dt_id: t.depends_on for t in tasks}

    # near-miss BEH-заголовки behaviour-spec — тот же стандарт, что для
    # DT (Global Constraints): битый заголовок — находка, не молчание
    strict_beh = {m.start() for m in _BEH_HEAD_RE.finditer(behaviour_text)}
    for near in _BEH_NEAR_RE.finditer(behaviour_text):
        if near.start() not in strict_beh:
            findings.append(
                f"{near.group(1)}: заголовок behaviour-spec не соответствует "
                "машинной грамматике BEH (`#### BEH-NN: <название>`)"
            )

    # ссылки на несуществующее
    for t in tasks:
        for ref in (*t.depends_on, *t.delivered_by):
            if ref not in ids:
                findings.append(f"{t.dt_id}: ссылка на несуществующий {ref}")
        for beh in t.scenarios:
            if beh not in bindings:
                findings.append(
                    f"{t.dt_id}: сценарий {beh} отсутствует в behaviour-spec"
                )

    # сюръекция BEH без дублей
    coverage: dict[str, list[str]] = {}
    for t in tasks:
        for beh in t.scenarios:
            coverage.setdefault(beh, []).append(t.dt_id)
    for beh in bindings:
        owners = coverage.get(beh, [])
        if not owners:
            findings.append(f"{beh}: не покрыт ни одной DT-задачей")
        elif len(owners) > 1:
            findings.append(
                f"{beh}: покрыт дважды и более ({', '.join(owners)})"
            )

    # single-owner тест-файла
    file_owner: dict[str, str] = {}
    for t in tasks:
        for beh in t.scenarios:
            target, _kind = bindings.get(beh, (None, None))
            if target is None:
                continue
            prior = file_owner.get(target)
            if prior is not None and prior != t.dt_id:
                findings.append(
                    f"{target}: нарушен single-owner — checked_by-цель у "
                    f"{prior} и {t.dt_id}"
                )
            file_owner.setdefault(target, t.dt_id)

    # verify/implement-контракт delivered_by + транзитивное замыкание
    for t in tasks:
        if t.type == "verify":
            if not t.delivered_by:
                findings.append(
                    f"{t.dt_id}: type: verify без delivered_by"
                )
            else:
                closure = _transitive_deps(t.dt_id, edges)
                outside = [d for d in t.delivered_by if d not in closure]
                if outside:
                    findings.append(
                        f"{t.dt_id}: delivered_by "
                        f"({', '.join(outside)}) вне транзитивного "
                        "замыкания depends_on"
                    )
        elif t.delivered_by:
            findings.append(
                f"{t.dt_id}: delivered_by запрещён при type: implement"
            )

    # ацикличность (DFS с цветами)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(ids, WHITE)

    def _visit(node: str) -> bool:
        color[node] = GRAY
        for nxt in edges.get(node, ()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and _visit(nxt):
                return True
        color[node] = BLACK
        return False

    if any(color[n] == WHITE and _visit(n) for n in sorted(ids)):
        findings.append("depends_on: в графе есть цикл")

    # рёбра в чужую группу — от ВСЕХ стоков этой группы
    groups: dict[str, set[str]] = {}

    def _group_key(task: DtTask) -> str:
        # solo — задача сама по себе: собственная одиночная группа,
        # не общая ветвь всех solo (major ревью плана)
        if task.parallel_group == "solo":
            return f"solo:{task.dt_id}"
        return task.parallel_group

    for t in tasks:
        groups.setdefault(_group_key(t), set()).add(t.dt_id)
    dependents: dict[str, set[str]] = {i: set() for i in ids}
    for t in tasks:
        for dep in t.depends_on:
            if dep in dependents:
                dependents[dep].add(t.dt_id)
    for t in tasks:
        foreign = {
            dep for dep in t.depends_on
            if dep in ids
        }
        by_group: dict[str, set[str]] = {}
        own_key = _group_key(t)
        # рёбра, обоснованные delivered_by, из правила стоков исключены
        # (verify точечно за проверяемым — §3 спеки, minor круга 2)
        foreign -= set(t.delivered_by)
        for dep in foreign:
            dep_group = next(
                g for g, members in groups.items() if dep in members
            )
            if dep_group != own_key:
                by_group.setdefault(dep_group, set()).add(dep)
        if len(by_group) < 2:
            # точечное ребро в одну чужую группу — не «свод» (major
            # круга 4): правило стоков действует только на задачу,
            # сводящую две и более чужих группы
            continue
        for g, deps in by_group.items():
            sinks = {
                member for member in groups[g]
                if not (dependents[member] & groups[g])
            }
            missing = sinks - set(t.depends_on)
            if missing:
                findings.append(
                    f"{t.dt_id}: зависит от группы {g}, но не от всех её "
                    f"стоков (нет: {', '.join(sorted(missing))})"
                )
    return findings
