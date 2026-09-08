"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Маркер-подстрока находки «verify без verifies» (round 5 ревью PR #161,
# контракт владельца) — единственная точка истины формы этой строки:
# используется при генерации находки НИЖЕ и в `is_non_fatal_form_finding`
# (потребитель — S4-гейт `governance.runner._step_gate`, пишет её как
# `warning GC-DT-GRAPH:` в gate-findings.txt, round 6 ревью PR #161, минор
# — ДО этого находка нигде не была видна оператору, несмотря на
# комментарий, обещавший потребителя в task_bridge.deliver, которого не
# было; сам `task_bridge.deliver` fatal-набор не фильтрует НАПРЯМУЮ —
# фильтр внутри `graph_findings`, общей точки для обоих потребителей).
_VERIFY_WITHOUT_VERIFIES_MARKER = "без структурного поля verifies"


def is_non_fatal_form_finding(finding: str) -> bool:
    """True для находок формы, НЕ обязанных блокировать доставку (round 5
    ревью PR #161): единственный такой класс — ``type: verify`` без
    ``verifies`` (легаси-совместимость: checked_by-fallback
    ``render_tasks_dt`` остаётся достижим). Прочие находки (near-miss
    заголовок, дубль id, ``verifies`` на ``implement``, graph-инварианты
    ``graph_findings``) — fatal как раньше.
    """
    return _VERIFY_WITHOUT_VERIFIES_MARKER in finding


_DT_HEAD_RE = re.compile(
    r"^####\s+(DT-\d+):\s*(.+?)\s*·\s*type:\s*(implement|verify)"
    r"\s*·\s*owner:\s*(\S+)\s*$",
    re.M,
)
# near-miss: начинается как DT-заголовок, но строгую грамматику не прошёл;
# широкая форма id ([^\s:]*) — суффиксный мусор (DT-3a) тоже находка
_DT_NEAR_RE = re.compile(r"^####\s+(DT-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


_LIST_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")


def _block_list_field(block: str, name: str) -> tuple[str, ...] | None:
    """`name:` в блочной YAML-форме (`- элемент` построчно, без `[...]`)."""
    m = re.search(rf"^{name}:\s*$", block, re.M)
    if m is None:
        return None
    items: list[str] = []
    # m.end() стоит ПЕРЕД '\n' ($ в re.M его не поглощает) — первый элемент
    # splitlines() всегда пустая строка-остаток заголовка, отбрасываем её.
    for line in block[m.end():].splitlines()[1:]:
        item = _LIST_ITEM_RE.match(line)
        if item is None:
            break
        items.append(item.group(1))
    return tuple(items)


def _verifies_field(block: str) -> tuple[str, ...] | None:
    """`verifies:` — инлайн (`[a, b]`) ИЛИ блочная (`- a`) форма, обе
    принимаются (спека владельца по DT-14, FIX 1)."""
    inline = _list_field(block, "verifies")
    if inline is not None:
        return inline
    return _block_list_field(block, "verifies")


@dataclass(frozen=True)
class DtTask:
    """Одна задача decomposition-узла (заголовок #### DT-NN).

    ``verifies`` — структурное поле группы наблюдения (owner ruling,
    multi-file DT-14): список файлов, за которыми присматривает
    ``type: verify`` DT, СТРУКТУРНО отдельный от ``checked_by``-владения
    (которое несут ``scenarios``) и НЕ участвующий в single-owner
    инварианте ``graph_findings`` вовсе (round 4 ревью PR #161, finding 3:
    исключение по verifies снято — тот же файл, дошедший до проверки через
    ``scenarios``/``checked_by``, ВСЕГДА заявка на редактирующее владение,
    независимо от того, что ещё перечислено в ``verifies``; владение
    всегда старше наблюдения).
    """

    dt_id: str
    title: str
    type: str
    owner: str
    scenarios: tuple[str, ...]
    depends_on: tuple[str, ...]
    delivered_by: tuple[str, ...]
    parallel_group: str
    verifies: tuple[str, ...] = ()


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
        verifies = _verifies_field(block) or ()
        dt_type = m.group(3)
        group_m = re.search(r"^parallel_group:\s*(\S+)\s*$", block, re.M)
        if scenarios is None or not scenarios:
            findings.append(f"{dt_id}: строка scenarios отсутствует или пуста")
        if depends_on is None:
            findings.append(f"{dt_id}: строка depends_on отсутствует")
        if group_m is None:
            findings.append(f"{dt_id}: строка parallel_group отсутствует")
        # verifies — структурное поле группы НАБЛЮДЕНИЯ (owner ruling,
        # DT-14): рекомендовано у type: verify (объявляется, когда нужна
        # multi-file группа наблюдения отдельно от checked_by-владения) и
        # ЗАПРЕЩЕНО у type: implement (checked_by через scenarios —
        # единственный канал ВЛАДЕНИЯ implement-задач). Находка «verify без
        # verifies» — ФОРМЫ, не graph-инвариант, и НЕ fatal (round 5 ревью
        # PR #161, контракт владельца): легаси-бандлы, авторенные до
        # раскатки поля, обязаны продолжать доставляться через checked_by-
        # union render_tasks_dt — `graph_findings` отфильтровывает эту
        # находку из своего результата (`is_non_fatal_form_finding` выше),
        # а S4-гейт показывает её отдельно как warning (round 6 ревью
        # PR #161, минор — см. docstring `graph_findings` и
        # `governance.runner._step_gate`).
        if dt_type == "verify" and not verifies:
            findings.append(
                f"{dt_id}: type: verify {_VERIFY_WITHOUT_VERIFIES_MARKER} "
                "— группа наблюдения не объявлена (рекомендация формы, "
                "не блокирует доставку)"
            )
        elif dt_type == "implement" and verifies:
            findings.append(f"{dt_id}: verifies запрещён при type: implement")
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=dt_type,
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
            verifies=verifies,
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
# [a-z]?-суффикс — как у _BEH_HEADER моста (BEH-18a из раундов ревью).
# NEAR ловит ЛЮБОЙ BEH-подобный заголовок (BEH-18A, BEH-18ab, BEH-18a2):
# неизвестная форма id — находка формы, не молчание; узкий NEAR с \b
# был слеп к суффиксам вне [a-z]? — тот же fail-open, что и до фикса
# (major ревью PR #148).
_BEH_HEAD_RE = re.compile(r"^####\s+(BEH-\d+[a-z]?):\s*\S", re.M)
_BEH_NEAR_RE = re.compile(r"^####\s+(BEH-[^\s:]*)", re.M)
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

    Находки формы, отмеченные `is_non_fatal_form_finding` (round 5 ревью
    PR #161, контракт владельца — сейчас единственный класс: verify без
    verifies), в результат НЕ попадают: и S4-гейт (`runner._step_gate`), и
    `task_bridge.deliver()` трактуют ЛЮБУЮ находку этого возврата как
    fatal, единой точки «не блокировать, но показать» у них нет — фильтр
    здесь, а не у потребителей, значит легаси-бандл без verifies проходит
    ОБА пути сразу, одним изменением. `parse_dt_tasks`, вызванный напрямую
    (не через graph_findings), эту находку по-прежнему возвращает — этим
    пользуется `runner._step_gate` (round 6 ревью PR #161, минор): читает
    `parse_dt_tasks` НАПРЯМУЮ рядом с вызовом `graph_findings`, чтобы
    показать отфильтрованную находку оператору как `warning GC-DT-GRAPH:`
    в gate-findings.txt, не останавливая прогон.
    """
    tasks, findings = parse_dt_tasks(decomposition_text)
    findings = [f for f in findings if not is_non_fatal_form_finding(f)]
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
                "машинной грамматике BEH (`#### BEH-NN[a-z]: <название>` — "
                "суффикс не более одной строчной буквы)"
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

    # порядок объявления: depends_on обязан ссылаться только на DT, уже
    # объявленные ВЫШЕ по документу (Task 9 находка 2 финального ревью) —
    # мост-транслятор переносит рёбра как есть, и forward-ссылка «уезжает»
    # в целевой репо как ссылка на задачу, которой там ещё нет
    order = {t.dt_id: idx for idx, t in enumerate(tasks)}
    for t in tasks:
        for ref in t.depends_on:
            if ref in order and order[ref] > order[t.dt_id]:
                findings.append(
                    f"{t.dt_id}: depends_on ссылается на {ref}, "
                    "объявленный ниже по документу — порядок объявления "
                    "обязан быть топологическим"
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

    # single-owner тест-файла — БЕЗ исключения для verifies (round 4 ревью
    # PR #161, finding 3: узкое per-task исключение round 2/3, `target in
    # t.verifies`, срабатывало РОВНО там, где задача t сама владеет
    # файлом — target здесь ВСЕГДА выведен из checked_by-цели t
    # (bindings по её же scenarios), т.е. это ВСЕГДА заявка t на
    # редактирующее владение, независимо от того, что ещё перечислено в
    # t.verifies. Владение (scenarios/checked_by) всегда старше
    # наблюдения (verifies, owner ruling DSL: «checked_by remains the
    # single source of EDITING ownership») — этот цикл смотрит только на
    # scenarios/checked_by и НИКОГДА на verifies, так что вопрос
    # исключения здесь просто не встаёт: single-owner проверяется как для
    # любых двух задач.
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
