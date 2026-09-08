"""Мост «behaviour-spec → draft tasks.md-спека для spec-runner» (шаг 3).

Замыкает цикл «предмет → спецификация → исполнители» (решение владельца
2026-08-31): из вмерженного behaviour-spec бандла генерируется managed-спека
``spec/<ws-id>-tasks.md`` в репо-владельце и доставляется PR-ом. Спека
рождается ``status: draft`` и при strict-governance spec-runner НЕ
исполняется, пока человек не переведёт её в approved — «агент предлагает,
человек утверждает» (инвариант №4 devtools, скилл spec-bridge).

Отдельная команда, не S9 runner'а: спека конвейера (§1) явно останавливает
его на behaviour-spec — продолжение вниз запускается осознанно.

CLI: ``python -m governance.task_bridge --run-id <id>`` (make behaviour-tasks).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from governance import acceptance_guard, decomposition_guard, design_guard
from governance.ops import Ops, RealOps
from governance.policy_sources import PREFLIGHT_PROCEDURE_HINT, target_profile_declares
from governance.run_state import RunState, load, op_complete, op_start
from governance.stale_adapter import blob_sha1

# DAG бандла в порядке штампа (топологический): каждый узел перечисляет
# node-id своих upstream'ов; штамп идёт по порядку тюпла, и пин(ы) узла
# пересчитываются ПОСЛЕ штампа ВСЕХ его upstream-файлов (иначе пин
# протухает в момент записи). design и acceptance — узлы с ДВУМЯ
# upstream-пинами (design — Task 5 плана design-узла; acceptance — Task 7
# плана acceptance-node). decomposition — терминальный узел, пинует ОБА
# upstream (design, acceptance — Task 7 плана acceptance-node).
_BUNDLE_DAG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
    ("25-acceptance.md", ("requirements", "behaviour-spec")),
    ("30-decomposition.md", ("design", "acceptance")),
)

# Вариант ДО раскатки acceptance-узла (Task 7 плана acceptance-node,
# `--legacy-bundle=5`) — ЛИТЕРАЛЬНЫЙ отдельный кортеж, не срез нового
# `_BUNDLE_DAG`: decomposition этой эры пинует только design (acceptance
# ещё не существовал), состав каталога — ровно 00/10/15/20/30.
_BUNDLE_DAG_LEGACY5: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
    ("30-decomposition.md", ("design",)),
)


def _node_id(filename: str) -> str:
    """Имя файла бандла → node-id (числовой префикс и `.md` отрезаны)."""
    return filename.rsplit(".", 1)[0].split("-", 1)[1]


# Якорный узел моста — терминальный узел DAG (decomposition). Выводится из
# _BUNDLE_DAG, а не хардкодится второй раз (Task 6): смена терминального
# узла бандла — правка одной строки DAG, не поиск по файлу.
_ANCHOR_FILENAME = _BUNDLE_DAG[-1][0]
_ANCHOR_NODE_ID = _node_id(_ANCHOR_FILENAME)


def _dag_for(
    legacy_bundle: int | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Активный DAG по значению `--legacy-bundle` (Task 7 плана
    acceptance-node): `None` — полный DAG (текущий якорь — decomposition,
    два upstream-пина: design и acceptance); `3`/`4` — точный префикс
    `_BUNDLE_DAG` (легаси-бандлы, авторенные до раскатки
    design/decomposition-узла: три узла — charter→requirements→
    behaviour-spec, четыре — плюс design); `5` — `_BUNDLE_DAG_LEGACY5`
    (отдельный литеральный кортеж, НЕ срез: бандлы, авторенные до раскатки
    acceptance-узла — decomposition этой эры пинует только design). Иное
    значение — ValueError, argparse (`choices=(3, 4, 5)`) отсекает его на
    CLI-границе раньше, но функция вызывается и напрямую (тесты,
    `stamp_bundle_approved`/`conform_approved`/`deliver`/`deliver_conform`).
    """
    if legacy_bundle is None:
        return _BUNDLE_DAG
    if legacy_bundle == 5:
        return _BUNDLE_DAG_LEGACY5
    if legacy_bundle in (3, 4):
        return _BUNDLE_DAG[:legacy_bundle]
    raise ValueError(
        f"legacy_bundle: ожидается 3, 4 или 5, получено {legacy_bundle!r}"
    )


def _check_bundle_composition(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    """Заявленный состав бандла (`dag`) обязан совпасть с фактическим РОВНО.

    «По самому длинному существующему» запрещён (спека §4): красил бы
    недоавторенный бандл зелёным, если в каталоге случайно лежит лишний
    (или недостаёт) узел DAG. Сравнение — по множеству имён файлов, не по
    префиксу и не по count — лишний ИЛИ недостающий узел одинаково
    отказывает.
    """
    declared = {fname for fname, _ in dag}
    known = {fname for fname, _ in _BUNDLE_DAG}
    actual = {
        p.name for p in (Path(target_dir) / bundle_dir).glob("*.md")
        if p.name in known
    }
    if actual != declared:
        raise RuntimeError(
            f"состав бандла {sorted(actual)} не совпадает с заявленным "
            f"{sorted(declared)}: доавторьте недостающие узлы либо "
            "передайте --legacy-bundle=3|4|5 с ТОЧНЫМ фактическим составом"
        )


def split_frontmatter(text: str) -> tuple[dict, str]:
    """YAML-frontmatter → (meta, body); файл без frontmatter — ValueError.

    Перепиновка и штампы делаются ПАРСЕРОМ, не текстовой заменой
    (ретроспектива 2026-09-02, @id:spec-bridge-approve-conformance):
    sed по инлайн-форме `{requirements: "…"}` молча промахнулся и пустил
    stale-пин в коммит — «0 замен» у текстовых замен выглядит как успех.
    """
    if not text.startswith("---\n"):
        raise ValueError("нет YAML-frontmatter (файл не начинается с '---')")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ValueError("frontmatter не закрыт разделителем '---'")
    meta = yaml.safe_load(head)
    if not isinstance(meta, dict):
        raise ValueError("frontmatter — не YAML-маппинг")
    return meta, body.lstrip("\n")


def join_frontmatter(meta: dict, body: str) -> str:
    """(meta, body) → текст файла; ключи в порядке вставки, без сортировки."""
    dumped = yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n\n{body}"

# [a-z]?-суффикс: раунды ревью бандлов вставляют сценарии как BEH-18a —
# без суффикса в грамматике мост молча ронял сценарий (PR spec-runner#369,
# major: BEH-18a выпал из декомпозиции, а его checked_by приклеился к
# предыдущему сценарию). Класс devtools#123 «баги генератора».
_BEH_HEADER = re.compile(r"^####\s+(BEH-\d+[a-z]?):\s*(.+?)\s*$")
_FEATURE_HEADER = re.compile(r"^##\s+Feature:\s*(.+?)\s*$")
_TRACES = re.compile(r"`traces:\s*\[([^\]]*)\]`")
_CHECKED = re.compile(
    r"\*\*checked_by\*\*.*?`kind:\s*(\S+?)`.*?`target:\s*(\S+?)`"
)


@dataclass(frozen=True)
class Scenario:
    """Один BEH-сценарий behaviour-spec.

    ``feature`` — имя ближайшей секции ``## Feature:`` выше сценария (или
    None): единица группировки задач (решение владельца 2026-08-31 по
    боевому прогону kapelle#47 — 1:1 «задача на сценарий» давало 19 задач
    с церемониальными накладными; группировка по Feature показала себя
    лучше во многих местах экосистемы).
    """

    beh_id: str
    title: str
    traces: tuple[str, ...]
    checked_kind: str | None
    checked_target: str | None
    feature: str | None = None


def parse_behaviour(text: str) -> list[Scenario]:
    """Разбирает DSL behaviour-spec (`#### BEH-NN` + traces + checked_by).

    Парсер построчный и намеренно терпимый к прозе вокруг: сценарий — всё
    между его заголовком и следующим `#### BEH-`. Пустой результат — ошибка:
    бандл без единого сценария не даёт задач, и молча пустая спека хуже
    громкого отказа.
    """
    scenarios: list[Scenario] = []
    current: dict | None = None
    feature: str | None = None

    def flush() -> None:
        if current is None:
            return
        scenarios.append(
            Scenario(
                beh_id=current["beh_id"],
                title=current["title"],
                traces=tuple(current.get("traces", ())),
                checked_kind=current.get("kind"),
                checked_target=current.get("target"),
                feature=current.get("feature"),
            )
        )

    for line in text.splitlines():
        feat = _FEATURE_HEADER.match(line)
        if feat:
            feature = feat.group(1)
            continue
        if line.startswith("## "):
            # Любой обычный `##`-заголовок ЗАВЕРШАЕТ Feature-секцию
            # (приёмка PR #100, minor): иначе сценарий под «## Особые
            # случаи» унаследовал бы предыдущий Feature и склеился с ним.
            feature = None
            continue
        header = _BEH_HEADER.match(line)
        if header:
            flush()
            current = {
                "beh_id": header.group(1),
                "title": header.group(2),
                "feature": feature,
            }
            continue
        if current is None:
            continue
        traces = _TRACES.search(line)
        if traces:
            current["traces"] = tuple(
                part.strip() for part in traces.group(1).split(",") if part.strip()
            )
        checked = _CHECKED.search(line)
        if checked:
            current["kind"] = checked.group(1)
            current["target"] = checked.group(2)
    flush()
    if not scenarios:
        raise ValueError(
            "behaviour-spec не содержит ни одного `#### BEH-NN` — "
            "спеку задач генерировать не из чего"
        )
    return scenarios


def _target_files(scenarios: list[Scenario]) -> set[str]:
    """Файлы checked_by-целей группы (pytest-селектор `::…` отброшен)."""
    return {
        sc.checked_target.split("::", 1)[0]
        for sc in scenarios
        if sc.checked_target
    }


def _merge_featureless_by_target_file(
    groups: list[tuple[str, str, list[Scenario]]],
) -> list[tuple[str, str, list[Scenario]]]:
    """Группы без Feature с общим файлом цели → одна задача (single owner).

    Урок 8 ретроспективы (@id:task-bridge-beh-grouping): нарезка «один
    BEH — одна задача» на геометрически связанных сценариях (один
    файл/автомат состояний) даёт red-unverifiable задачи — поведение уже
    покрыто соседней реализацией, честный красный тест невозможен, и
    TDD-гейт стопит прогон до waiver-ритуала (WS-disputatio-57: 7 из 15).
    Детерминированный прокси связанности — файл checked_by-цели: BEH-ы
    одного автомата бьют в один тестовый файл.

    Владелец файла — ЕДИНСТВЕННЫЙ и по всему документу, не только среди
    смежных групп (ревью disputatio#86 по контракту
    docs/workstream-setup.md: у тест-файла один task-owner — невлитая
    ранняя задача держит byte-lock, и поздняя задача с тем же файлом не
    может честно выполнить свою RED-фазу; класс прожит на TASK-014/015
    WS-57). Слияние транзитивное: группы, связанные общими файлами через
    цепочку, попадают в задачу на месте ПЕРВОЙ из них — порядок документа
    сохраняется по первым вхождениям.

    Feature-группировка владельца (решение 2026-08-31) приоритетна и не
    трогается: мержатся только группы, у которых ни один сценарий не
    отнесён к Feature; бес-Feature сценарий в Feature-группу не вливается.
    Заголовок слитой группы — первый сценарий + счётчик.
    """
    # Union-find по индексам групп (приёмка PR #119, minor): группа-«мост»
    # с файлами {A, B} обязана объединить И уже разных владельцев A и B —
    # выбор одного из них оставлял бы у файла второго владельца.
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    owner_by_file: dict[str, int] = {}
    for idx, (_key, _title, scs) in enumerate(groups):
        if not all(s.feature is None for s in scs):
            continue  # Feature-группы владельца в union не участвуют
        for f in sorted(_target_files(scs)):
            if f in owner_by_file:
                union(idx, owner_by_file[f])
            else:
                owner_by_file[f] = idx
    merged: list[tuple[str, str, list[Scenario]]] = []
    root_pos: dict[int, int] = {}
    for idx, (key, title, scs) in enumerate(groups):
        root = find(idx)
        if root in root_pos:
            okey, _otitle, oscs = merged[root_pos[root]]
            oscs.extend(scs)
            merged[root_pos[root]] = (
                okey,
                f"{oscs[0].title} (+{len(oscs) - 1} смежных BEH)",
                oscs,
            )
        else:
            root_pos[root] = len(merged)
            merged.append((key, title, list(scs)))
    return merged


def _render_resolutions_section(design_text: str) -> list[str]:
    """Секция «Решения открытых вопросов» tasks-спеки из design-DSL.

    Потребляет ``design_guard.parse_design_resolutions`` (Task 4, голова
    записи — state/reason) и ``parse_design_resolution_bodies`` (devtools#158,
    тело записи целиком) — не рукописный пересказ, а генерация из
    фактического 20-design.md. Раньше секция несла только первый абзац/
    ``reason:`` блока — списки и таблицы после него молча терялись (живая
    находка kapelle: классификационная таблица Q-05 не доходила до
    executors). Теперь каждая запись — голова (``**Q-NN — resolved:**`` /
    ``**Q-NN — deferred:** reason: …``) + пустая строка + ВЕСЬ текст блока
    дословно (списки/таблицы с колонки 0 остаются валидным markdown).
    Пустое тело (голый заголовок без абзаца/reason:) — fallback на старое
    однострочное поведение. Пустой набор резолюций (design без единого
    блока ``#### Q-NN``) — секция не рендерится вовсе.
    """
    resolutions = design_guard.parse_design_resolutions(design_text)
    if not resolutions:
        return []
    bodies = design_guard.parse_design_resolution_bodies(design_text)
    lines = ["## Решения открытых вопросов (уровень design)", ""]
    for qid, (state, reason) in resolutions.items():
        body = bodies.get(qid, "")
        if state == "deferred":
            # None недостижим через конвейер (S4 ловит deferred без
            # reason:), но deliver зовут и на легаси/ручных бандлах —
            # литерал «None» в человеко-читаемом артефакте недопустим.
            lines.append(
                f"**{qid} — deferred:** reason: "
                f"{reason if reason else 'причина не указана'}"
            )
            if body:
                # Тело несёт строку reason: внутри себя же — дедуп с
                # головой намеренно НЕ делаем (devtools#158): усложнение
                # ради косметики, содержимое не теряется ни там, ни там.
                lines += ["", body]
            lines.append("")
        elif body:
            lines += [f"**{qid} — resolved:**", "", body, ""]
        else:
            # `reason` не может быть непустым здесь: у resolved
            # justification выводится из ТОГО ЖЕ блока (design_guard.
            # parse_design_resolutions — reason:/первый абзац), а пустой
            # body означает блок без единой непробельной строки — значит и
            # reason: None. Отдельная `elif reason:`-ветка была бы мёртвым
            # кодом (nit ревью PR #160) — убрана, не оговорена комментарием.
            lines += [f"- **{qid}:** resolved", ""]
    lines.append("")
    return lines


def _render_acceptance_section(acceptance_text: str) -> list[str]:
    """Справочная секция «Критерии приёмки» tasks-спеки из AC-DSL.

    Только id, verification и название — полный текст критериев живёт в
    25-acceptance.md бандла (§4 спеки); исполнителю задач достаточно
    знать, ЧТО будет приниматься. Пустой вход — секции нет.
    """
    crits, _findings = acceptance_guard.parse_ac_criteria(acceptance_text)
    if not crits:
        return []
    lines = ["## Критерии приёмки (уровень acceptance)", ""]
    lines += [
        f"- **{c.ac_id}** ({c.verification}): {c.title}" for c in crits
    ]
    lines.append("")
    return lines


def _render_header(
    ws_id: str,
    subject: str,
    generated_at: str,
    anchor_blob: str,
    anchor_node_id: str,
    version: int = 1,
) -> list[str]:
    """Frontmatter + шапка Milestone — общая часть `render_tasks` и
    `render_tasks_dt` (Task 8 плана decomposition-node).

    Вынесено из `render_tasks` БЕЗ изменения текста — регрессионные тесты
    рендера держат байт-в-байт поведение `render_tasks`.

    Форма активного governance-профиля сразу при рождении (урок 1
    ретроспективы): traces_to/upstream_hashes переживают `spec approve`
    (он мержит traces и не трогает существующий пин), так что рукам после
    approve остаётся только нормализация `--conform-approve`.

    `version` (FIX 2, owner ruling supersede/redelivery): по умолчанию 1
    (первая доставка); `deliver()` передаёт `max(предыдущий, 1) + 1`, когда
    в target_dir уже лежит вмерженная `spec/<ws-id>-tasks.md` — редоставка
    обязана нести монотонный version, не откатывать его хардкодом в 1.
    """
    return [
        "---",
        "spec_stage: tasks",
        "status: draft",
        "owner_role: stream-owner",
        f"version: {version}",
        "generated_by: fleet-agent",
        # В кавычках: голый ISO-скаляр YAML резолвит в timestamp, а
        # схема спеки ждёт строку (minor ревью PR spec-runner#369, круг 3)
        f'generated_at: "{generated_at}"',
        'source_prompt_version: ""',
        'validation: ""',
        'approved_by: ""',
        "traces_to:",
        f"- {anchor_node_id}",
        "upstream_hashes:",
        f"  {anchor_node_id}: {anchor_blob}",
        "---",
        "",
        f"## Milestone 1: {subject}",
        "",
        f"Сгенерировано task_bridge из behaviour-spec бандла {ws_id} "
        "(шаг 3 плана развития конвейера; группировка задач — по "
        "Feature-секциям). Draft: исполнение только после человеческого "
        "approve.",
        "",
    ]


def render_tasks(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    generated_at: str,
    design_blob: str,
    design_text: str = "",
    anchor_node_id: str = _ANCHOR_NODE_ID,
    version: int = 1,
) -> str:
    """tasks.md по шаблону templates/tasks-spec-template.md.

    Правила шаблона, которые несёт рендер: frontmatter managed-спеки со
    ``status: draft``; Source-провенанс в каждой задаче (сюда — путь бандла
    и якоря BEH); чеклист с колонки 0; последний пункт чеклиста — проверка
    (checked_by-биндинг), не действие.

    Группировка (решение владельца 2026-08-31): одна задача на
    ``## Feature:``-секцию behaviour-spec, а не на сценарий — 1:1 в боевом
    прогоне kapelle#47 дало 19 церемониальных задач. Сценарии без Feature
    остаются задачами 1:1; задачи зависят цепочкой (порядок документа).

    Якорь traces_to/upstream_hashes по умолчанию — decomposition
    (терминальный узел `_BUNDLE_DAG`, Task 7 плана decomposition-node):
    каждый узел цепочки транзитивно пинует всех своих upstream, так что
    один пин терминального узла покрывает весь бандл — traces_to дальше по
    цепочке незачем.

    `anchor_node_id` — легаси-режим (Task 7 плана design-узла, обобщено
    Task 7 плана decomposition-node на `--legacy-bundle=3|4`): вызывающий
    (`deliver`) передаёт терминальный узел ФАКТИЧЕСКИ активного (усечённого)
    DAG вместо дефолтного `decomposition` — сам рендер об этом режиме не
    знает, только про то, ЧТО именно является якорем.
    """
    lines = _render_header(
        ws_id, subject, generated_at, design_blob, anchor_node_id,
        version=version,
    )
    lines += _render_resolutions_section(design_text)
    groups: list[tuple[str, str, list[Scenario]]] = []  # (key, title, scs)
    for sc in scenarios:
        key = sc.feature or sc.beh_id
        if groups and groups[-1][0] == key:
            groups[-1][2].append(sc)
        else:
            groups.append((key, sc.feature or sc.title, [sc]))
    groups = _merge_featureless_by_target_file(groups)
    for index, (_key, title, group) in enumerate(groups, start=1):
        beh_ids = [g.beh_id for g in group]
        traces: list[str] = []
        for g in group:
            traces += [t for t in g.traces if t not in traces]
        # Пары target+kind, не голые targets (приёмка PR #100, major):
        # checked_by-биндинг несёт ОБЕ части — исполнитель обязан знать вид
        # проверки (integration/e2e/...), не только файл.
        bindings: list[str] = []
        for g in group:
            if g.checked_target:
                pair = f"{g.checked_target} (kind: {g.checked_kind})"
                if pair not in bindings:
                    bindings.append(pair)
        check = (
            f"проверка группы: {', '.join(bindings)} зелёные на "
            f"{', '.join(beh_ids)}"
            if bindings
            else f"проверка группы {', '.join(beh_ids)} определена и зелёная"
        )
        lines += [
            f"### TASK-{index:03d}: {title}",
            "P2 | TODO   Est: 0.5d",
            "",
            f"Реализовать сценарии {', '.join(beh_ids)}.",
            f"Source: {bundle_path}#{beh_ids[0]}"
            + (f" (—{beh_ids[-1]})" if len(beh_ids) > 1 else ""),
        ]
        if index > 1:
            lines.append(f"**Depends on:** [TASK-{index - 1:03d}]")
        lines += ["", "**Checklist:**"]
        lines += [
            f"- [ ] реализовать {g.beh_id}: {g.title}" for g in group
        ]
        lines += [
            f"- [ ] {check}",
            "",
            # Каждая ссылка в СВОИХ скобках (spec/FORMAT.md spec-runner:
            # `[FR-02], [FR-03]`) — парсер task.py требует `]` сразу после
            # id, общая скобка молча роняла traces_to у многоссылочных
            # задач (major ревью PR spec-runner#369, круг 2).
            (
                "**Traces to:** "
                + ", ".join(f"[{ref}]" for ref in traces)
                if traces
                else ""
            ),
            "",
        ]
    return "\n".join(line for line in lines if line is not None) + "\n"


def render_tasks_dt(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    dt_tasks: list[decomposition_guard.DtTask],
    generated_at: str,
    anchor_blob: str,
    design_text: str = "",
    acceptance_text: str = "",
    version: int = 1,
) -> str:
    """tasks.md из решённой декомпозиции: 1 DT = 1 задача.

    Мост — ТРАНСЛЯТОР (§1 спеки): состав задач, типы и рёбра решены
    tech-lead-узлом и проверены гейтом; здесь только джойн BEH →
    checked_by и перевод depends_on → Depends on. Эвристика
    _merge_featureless_by_target_file на этом пути НЕ применяется — её
    инвариант переехал в гейт (single-owner, GC-DT-GRAPH).

    ``acceptance_text`` (Task 8 плана acceptance-node) — справочная секция
    критериев приёмки из 25-acceptance.md, вставляется сразу после секции
    решений design (`_render_resolutions_section`); пустой вход (legacy-
    бандл без узла acceptance) — секции нет, как у design_text.
    """
    # verify-first доставлен (spec-runner#367 закрыт 2026-09-07, WS-367
    # PR #371–#387): verify-DT рендерится задачей с `**Mode:**
    # verify_first` — spec-runner начнёт её живым прогоном объявленной
    # группы и уйдёт green-only/TDD/стоп по evidence. Fail-closed отказ
    # эпохи блокера снят (@id:decomposition-verify-first-unblock).
    by_beh = {sc.beh_id: sc for sc in scenarios}
    number = {t.dt_id: idx for idx, t in enumerate(dt_tasks, start=1)}
    lines = _render_header(
        ws_id, subject, generated_at, anchor_blob,
        anchor_node_id="decomposition", version=version,
    )
    lines += _render_resolutions_section(design_text)
    lines += _render_acceptance_section(acceptance_text)
    for t in dt_tasks:
        group = [by_beh[b] for b in t.scenarios if b in by_beh]
        beh_ids = [g.beh_id for g in group]
        bindings: list[str] = []
        for g in group:
            if g.checked_target:
                pair = f"{g.checked_target} (kind: {g.checked_kind})"
                if pair not in bindings:
                    bindings.append(pair)
        check = (
            f"проверка группы: {', '.join(bindings)} зелёные на "
            f"{', '.join(beh_ids)}"
            if bindings
            else f"проверка группы {', '.join(beh_ids)} определена и зелёная"
        )
        idx = number[t.dt_id]
        action = "Проверить" if t.type == "verify" else "Реализовать"
        lines += [
            f"### TASK-{idx:03d}: {t.title}",
            "P2 | TODO   Est: 0.5d",
            "",
            f"{action} сценарии {', '.join(beh_ids)} ({t.dt_id}, "
            f"группа {t.parallel_group}).",
            f"Source: {bundle_path}#{t.dt_id}",
        ]
        if t.type == "verify":
            # Задача проверки: spec-runner исполняет её в режиме
            # verify-first — живой прогон группы до первого платного
            # вызова, green → green-only без покупки красного
            lines.append("**Mode:** verify_first")
            # Полные селекторы (node id с `::`) НАМЕРЕННО: группа
            # verify-first прогоняется по-селекторно (FR-06), словарь
            # судит адаптер spec-runner; дедуп — как у bindings.
            # Контракт формата ЗАПИНОВАН (minor ревью PR #152) — точные
            # регексы парсера spec-runner (src/spec_runner/task.py,
            # TASK-001/002 WS-367, PR #371/#372):
            #   MODE     = r"\*\*Mode:\*\* (.+)"
            #   VERIFIES = r"\*\*Verifies:\*\*\s*(.*)$"
            # — построчный разбор среди прочих **…**-метаданных, позиция
            # строки в теле задачи свободная; значение `verify_first` —
            # через подчёркивание (EXECUTION_MODES spec-runner).
            #
            # Источник targets (FIX 1, owner ruling DT-14 multi-file group):
            # структурное поле `t.verifies` — ЕСЛИ оно объявлено, verbatim
            # порядок + дедуп, БЕЗ обращения к checked_by сценариев вовсе
            # (verifies — группа наблюдения, отдельная от checked_by-
            # владения через scenarios). Fallback на старый checked_by-
            # вывод — только для легаси-бандлов без verifies (DtTask.verifies
            # по умолчанию пуст).
            targets: list[str] = []
            if t.verifies:
                for f in t.verifies:
                    if f not in targets:
                        targets.append(f)
            else:
                for b in t.scenarios:
                    sc_target = (
                        by_beh[b].checked_target if b in by_beh else None
                    )
                    if sc_target and sc_target not in targets:
                        targets.append(sc_target)
            if not targets:
                # verify без прогоняемой группы необоснован: суть режима
                # — живой прогон объявленных целей; молчаливый Mode без
                # Verifies уехал бы обычным TDD (minor ревью PR #152)
                raise RuntimeError(
                    f"{t.dt_id}: type: verify, но ни один сценарий "
                    f"({', '.join(t.scenarios)}) не несёт checked_by-цели "
                    "— verify-first нечего прогонять"
                )
            lines.append(f"**Verifies:** {', '.join(targets)}")
        if t.depends_on:
            # Та же пер-ссылочная форма, что у Traces to: TASK_REF
            # spec-runner требует ] сразу после id (minor ревью PR #149)
            deps = ", ".join(
                f"[TASK-{number[d]:03d}]" for d in t.depends_on if d in number
            )
            lines.append(f"**Depends on:** {deps}")
        lines += ["", "**Checklist:**"]
        item_verb = "проверить" if t.type == "verify" else "реализовать"
        lines += [
            f"- [ ] {item_verb} {g.beh_id}: {g.title}" for g in group
        ]
        traces = []
        for g in group:
            traces += [x for x in g.traces if x not in traces]
        lines += [
            f"- [ ] {check}",
            "",
            # Каждая ссылка в СВОИХ скобках (spec/FORMAT.md spec-runner:
            # `[FR-02], [FR-03]`) — парсер task.py требует `]` сразу после
            # id, общая скобка молча роняла traces_to у многоссылочных
            # задач (major ревью PR spec-runner#369, круг 2).
            (
                "**Traces to:** "
                + ", ".join(f"[{ref}]" for ref in traces)
                if traces
                else ""
            ),
            "",
        ]
    return "\n".join(line for line in lines if line is not None) + "\n"


def stamp_bundle_approved(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None = None,
) -> list[str]:
    """Штамп статусов вмерженного бандла + перепиновка цепочки; → rel-пути.

    `legacy_bundle` (Task 7 плана acceptance-node) выбирает активный DAG
    через `_dag_for`: `None` — полный (charter→…→decomposition, два
    upstream-пина у decomposition — design и acceptance); `3`/`4` — точный
    префикс (бандлы, авторенные до раскатки design/decomposition-узла);
    `5` — `_BUNDLE_DAG_LEGACY5` (бандл авторен до раскатки acceptance-узла,
    decomposition пинует только design). Состав каталога обязан совпасть с
    выбранным DAG РОВНО (`_check_bundle_composition`, вызов в начале
    функции) — явный RuntimeError с процедурой (доавторить недостающие
    узлы ЛИБО передать `--legacy-bundle=3|4|5` с точным фактическим
    составом), а не сырой traceback от `path.read_text()` на отсутствующем
    файле.

    Урок 2 ретроспективы (devtools#110): после мержа бандла charter /
    requirements / behaviour-spec остаются `status: draft` — «никто не
    проштамповал». Approve-событие уже состоялось: по решению владельца
    (devtools#110, 2026-09-02) инициированный им мерж = человеческий
    approve, а агентский мерж легитимен по DarkFactory (ADR-ECO-011).
    Штамп записывает ЭТОТ факт: `approved_by` = mergedBy бандл-PR (честный
    различитель agent/human), `approved_at` = mergedAt.

    Перепиновка идёт по DAG (Task 5: цепочка стала DAG — design пинует ОБА
    upstream): штамп меняет байты файла, поэтому пин(ы) каждого узла
    пересчитываются ПОСЛЕ штампа ВСЕХ его upstream-файлов — порядок
    обхода `_BUNDLE_DAG` уже топологический. Идемпотентно: уже approved
    файл с верными пинами не трогается и в результат не входит.
    """
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    changed: list[str] = []
    stamped_blobs: dict[str, str] = {}
    base = Path(target_dir) / bundle_dir
    for name, upstream_ids in dag:
        path = base / name
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        dirty = False
        if meta.get("status") != "approved":
            meta["status"] = "approved"
            meta["approved_by"] = approved_by
            meta["approved_at"] = approved_at
            meta["version"] = int(meta.get("version") or 1) + 1
            dirty = True
        if upstream_ids:
            pins = meta.get("upstream_hashes")
            pins = dict(pins) if isinstance(pins, dict) else {}
            for upstream_id in upstream_ids:
                blob = stamped_blobs[upstream_id]
                if pins.get(upstream_id) != blob:
                    pins[upstream_id] = blob
                    dirty = True
            if dirty:
                meta["upstream_hashes"] = pins
        if dirty:
            path.write_text(join_frontmatter(meta, body), encoding="utf-8")
            changed.append(f"{bundle_dir}/{name}")
        stamped_blobs[_node_id(name)] = blob_sha1(
            path.read_text(encoding="utf-8")
        )
    return changed


def conform_approved(
    target_dir: str,
    ws_id: str,
    bundle_dir: str,
    legacy_bundle: int | None = None,
) -> bool:
    """Нормализация frontmatter tasks-спеки ПОСЛЕ `spec approve` владельца.

    Якорь — терминальный узел активного DAG (`_dag_for(legacy_bundle)`,
    Task 7 плана acceptance-node: `None` и `5` — decomposition (полный DAG
    либо `_BUNDLE_DAG_LEGACY5`), `3`/`4` — усечённый префикс,
    behaviour-spec/design соответственно). Не хардкодится второй раз —
    выводится из DAG, так что смена терминального
    узла бандла правит DAG в одном месте, не эту функцию. Нормализация
    возвращает форму активного governance-профиля: traces_to ровно
    [<anchor>], пин — на ТЕКУЩИЙ blob вмерженного файла анкера (independent
    от того, что туда дописал/недописал `spec approve` — lite-профиль
    spec-runner не знает про наш DAG). Строгий run проверяет только
    status — правка безопасна. Возвращает, менялся ли файл.

    Состав бандла проверяется В НАЧАЛЕ (`_check_bundle_composition`) —
    отсутствие файла-анкера (напр., design без флага на легаси-бандле)
    ловится ТАМ явным RuntimeError с процедурой, не сырым traceback.
    """
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    anchor_filename = dag[-1][0]
    anchor_node_id = _node_id(anchor_filename)
    rel = Path(target_dir) / "spec" / f"{ws_id}-tasks.md"
    meta, body = split_frontmatter(rel.read_text(encoding="utf-8"))
    if meta.get("status") != "approved":
        raise RuntimeError(
            f"{rel.name}: status={meta.get('status')!r} — нормализация идёт "
            "ПОСЛЕ человеческого `spec approve` (инвариант №4), сначала он"
        )
    anchor = Path(target_dir) / bundle_dir / anchor_filename
    pin = blob_sha1(anchor.read_text(encoding="utf-8"))
    changed = False
    if meta.get("traces_to") != [anchor_node_id]:
        meta["traces_to"] = [anchor_node_id]
        changed = True
    want = {anchor_node_id: pin}
    if meta.get("upstream_hashes") != want:
        meta["upstream_hashes"] = want
        changed = True
    if changed:
        rel.write_text(join_frontmatter(meta, body), encoding="utf-8")
    return changed


def deliver(
    target_dir: str,
    repo_slug: str,
    ws_id: str,
    subject: str,
    bundle_dir: str,
    base_ref: str,
    ops: Ops,
    approved_by: str,
    approved_at: str,
    generated_at: str | None = None,
    legacy_bundle: int | None = None,
    profile: str | None = None,
) -> int:
    """Штампует бандл + пишет spec/<ws-id>-tasks.md; один draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.

    Порядок «штамп бандла → пин анкера → рендер tasks» жёсткий: штамп
    меняет байты терминального узла активного DAG, и пин, взятый до
    штампа, протух бы в том же PR (@id:spec-bridge-approve-conformance).

    `ops.ensure_branch` (переключение/создание ветки доставки) идёт ДО
    `stamp_bundle_approved` (round 2 ревью PR #161, finding 3, контракт
    владельца C — восстановленный исходный порядок): `git switch` на УЖЕ
    СУЩЕСТВУЮЩУЮ ветку доставки по ГРЯЗНОМУ дереву (штамп уже переписал
    файлы бандла) аварийно завершается (`CalledProcessError` мимо `main`,
    который ловит только `RuntimeError`) — переключение обязано идти по
    ЧИСТОМУ, только что зачекаученному дереву, штамп/чтение анкера/рендер —
    уже после него.

    `legacy_bundle` (Task 7 плана acceptance-node) выбирает активный DAG
    через `_dag_for`: `None` — полный (якорь decomposition, два upstream-
    пина: design и acceptance); `3`/`4` — точный префикс (бандл авторен до
    раскатки design/decomposition-узла) — файлы терминального узла ЗА
    пределами префикса не читаются вовсе; `5` — `_BUNDLE_DAG_LEGACY5`
    (бандл авторен до раскатки acceptance-узла — decomposition этой эры
    пинует только design, но DT-путь идёт тем же образом, что и на полном
    DAG).

    `profile` (опционально): путь профиля относительно `target_dir` — тот
    же, что получит `gate_check_candidate` в раннере (`state.profile`), не
    захардкоженный `profiles/team-exp.yaml`. Для каждого узла из набора
    ``("design", "acceptance", "decomposition")``, входящего в АКТИВНЫЙ DAG,
    доставка отказывает, если ФАКТИЧЕСКИЙ профиль target-репо не
    декларирует этот узел — та же процедура, что у `stopped_preflight` раннера
    (`governance.policy_sources.target_profile_declares`): соседний репо
    может нести старую копию файла того же имени без
    design/acceptance/decomposition.
    `profile=None` (дефолт) — проверка пропускается; CLI (`main`) всегда
    передаёт `state.profile`.
    """
    if ops.is_dirty(target_dir):
        raise RuntimeError(
            f"target_dir {target_dir!r} грязный — доставка спеки не начата"
        )
    ops.checkout_and_pull(target_dir, base_ref)
    # Существование и чтение бандла — строго ПОСЛЕ чекаута базы (приёмка
    # PR #96, major): до него чекаут мог стоять на произвольной ветке, и
    # спека сгенерировалась бы из невмерженной ревизии бандла. Тот же
    # порядок — для гарда состава и preflight профиля ниже (инвариант
    # приёмки PR #96, Task 7): ПОСЛЕ checkout_and_pull, ДО ensure_branch.
    dag = _dag_for(legacy_bundle)
    base = Path(target_dir) / bundle_dir
    behaviour = base / "15-behaviour-spec.md"
    if not behaviour.exists():
        raise RuntimeError(
            f"{behaviour} не найден на {base_ref} — бандл не вмержен "
            "или путь неверен"
        )
    _check_bundle_composition(target_dir, bundle_dir, dag)
    # Preflight: та же проверка, что стопит раннер `stopped_preflight`'ом —
    # target-профиль может не декларировать design/acceptance/decomposition
    # вовсе (старая копия того же имени у соседнего репо), и доставка не
    # имеет права молча анкериться на узле, которого активный профиль этого
    # репо не признаёт. Проверяются ровно узлы, входящие в АКТИВНЫЙ dag —
    # `--legacy-bundle=3` не требует ни одного из трёх, `=4` требует
    # design, `=5` требует design и decomposition (без acceptance — узла
    # этой эры ещё нет), полный DAG требует все три.
    if profile is not None:
        for node in ("design", "acceptance", "decomposition"):
            if any(
                _node_id(fname) == node for fname, _ in dag
            ) and not target_profile_declares(target_dir, profile, node):
                raise RuntimeError(
                    f"{Path(target_dir) / profile} не декларирует узел "
                    f"'{node}' — {PREFLIGHT_PROCEDURE_HINT}"
                )
    # Валидация DT-пути (полный DAG, Task 8 плана decomposition-node) —
    # ЗДЕСЬ, ПОСЛЕ existence/composition-гардов и ДО ensure_branch/
    # stamp_bundle_approved: отказ (невалидный граф DT) не должен
    # оставлять target на чужой ветке с незакоммиченным штампом —
    # dirty-гард заблокировал бы повторную доставку. verify-DT больше не
    # отказ: verify-first доставлен (spec-runner#367 закрыт 2026-09-07),
    # мост рендерит их задачами `**Mode:** verify_first`. Ветвление — по
    # составу АКТИВНОГО DAG (Task 7 плана acceptance-node), не по
    # `legacy_bundle is None`: `--legacy-bundle=5` тоже несёт decomposition
    # (старая эра до раскатки acceptance-узла) и обязан идти DT-путём с той
    # же валидацией графа.
    if any(_node_id(fname) == "decomposition" for fname, _ in dag):
        decomposition_pre = (
            base / "30-decomposition.md"
        ).read_text(encoding="utf-8")
        behaviour_pre = behaviour.read_text(encoding="utf-8")
        graph_errors = decomposition_guard.graph_findings(
            behaviour_pre, decomposition_pre
        )
        if graph_errors:
            raise RuntimeError(
                "decomposition: граф DT невалиден:\n"
                + "\n".join(f"- {e}" for e in graph_errors)
            )
    # ensure_branch — ЗДЕСЬ, ДО stamp_bundle_approved (round 2 ревью
    # PR #161, finding 3, контракт владельца C): штамп мутирует файлы
    # бандла НА ДИСКЕ; `git switch` на уже существующую ветку доставки по
    # ГРЯЗНОМУ (постштампованному) дереву аварийно завершается
    # (CalledProcessError мимо main, который ловит только RuntimeError).
    # Переключение обязано идти по чистому дереву сразу после
    # checkout_and_pull — восстановленный исходный порядок.
    branch = f"spec/{ws_id}-tasks"
    ops.ensure_branch(target_dir, branch)
    stamped = stamp_bundle_approved(
        target_dir, bundle_dir, approved_by, approved_at,
        legacy_bundle=legacy_bundle,
    )
    # Анкер — терминальный узел АКТИВНОГО DAG (не хардкод design/behaviour):
    # читаем ПОСЛЕ штампа, иначе пин взят из уже стухшего blob'а. design_text
    # (для секции резолюций) — из 20-design.md, но только когда design
    # входит в активный DAG; на 3-узловом легаси-бандле design в нём нет
    # вовсе, секция резолюций не рендерится.
    anchor_node_id = _node_id(dag[-1][0])
    anchor_text = (base / dag[-1][0]).read_text(encoding="utf-8")
    design_blob = blob_sha1(anchor_text)
    design_text = (
        (base / "20-design.md").read_text(encoding="utf-8")
        if any(_node_id(fname) == "design" for fname, _ in dag)
        else ""
    )
    # acceptance_text (Task 8 плана acceptance-node) — тот же канон, что
    # design_text: только когда узел acceptance входит в активный DAG
    # (полный DAG; `--legacy-bundle=5` его не несёт — эра до раскатки
    # acceptance-узла).
    acceptance_text = (
        (base / "25-acceptance.md").read_text(encoding="utf-8")
        if any(_node_id(fname) == "acceptance" for fname, _ in dag)
        else ""
    )
    scenarios = parse_behaviour(behaviour.read_text(encoding="utf-8"))
    # Локальное время С офсетом (не naive `datetime.now()`): spec-runner
    # пишет tz-aware `approved_at` на approve, и сравнение naive/aware
    # штампов неопределено (devtools#157 — живая аномалия «approve раньше
    # генерации» в kapelle).
    stamp = generated_at or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    # version монотонный (FIX 2, owner ruling supersede/redelivery):
    # редоставка НЕ обязана откатывать version в 1 хардкодом рендера —
    # если в target_dir уже лежит доставленная (вмерженная) tasks-спека,
    # версия продолжает её цикл. Пустой target — обычная первая доставка,
    # version: 1 как раньше.
    prior_spec_path = Path(target_dir) / f"spec/{ws_id}-tasks.md"
    version = 1
    if prior_spec_path.exists():
        prior_meta, _prior_body = split_frontmatter(
            prior_spec_path.read_text(encoding="utf-8")
        )
        version = max(int(prior_meta.get("version") or 1), 1) + 1
    if any(_node_id(fname) == "decomposition" for fname, _ in dag):
        # DT-путь (Task 8 плана decomposition-node, обобщено Task 7 плана
        # acceptance-node на `--legacy-bundle=5`): состав задач решён
        # tech-lead-узлом и уже проверен graph_findings выше; здесь только
        # парсинг ПОСЛЕ штампа (тело DT-задач штамп не трогает, но пин
        # анкера должен идти с уже проштампованного blob'а) и джойн BEH →
        # checked_by.
        dt_tasks, _form_findings = decomposition_guard.parse_dt_tasks(
            anchor_text
        )
        text = render_tasks_dt(
            ws_id=ws_id,
            subject=subject,
            bundle_path=f"{bundle_dir}/30-decomposition.md",
            scenarios=scenarios,
            dt_tasks=dt_tasks,
            generated_at=stamp,
            anchor_blob=design_blob,
            design_text=design_text,
            acceptance_text=acceptance_text,
            version=version,
        )
    else:
        text = render_tasks(
            ws_id=ws_id,
            subject=subject,
            bundle_path=f"{bundle_dir}/15-behaviour-spec.md",
            scenarios=scenarios,
            generated_at=stamp,
            design_blob=design_blob,
            design_text=design_text,
            anchor_node_id=anchor_node_id,
            version=version,
        )
    rel = f"spec/{ws_id}-tasks.md"
    out = Path(target_dir) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    ops.commit_paths(
        target_dir,
        [*stamped, rel],
        f"spec: {ws_id} tasks (draft) + штамп статусов бандла (fleet-agent)",
    )
    ops.push_branch(target_dir, branch)
    body = (
        f"Draft tasks.md-спека из behaviour-spec бандла {ws_id} "
        f"({bundle_dir}/15-behaviour-spec.md), сгенерирована task_bridge.\n\n"
        + (
            "Этим же PR — штамп статусов вмерженного бандла "
            f"({len(stamped)} файл(а): approved_by = mergedBy бандл-PR, "
            "перепиновка DAG "
            + "→".join(_node_id(fname) for fname, _ in dag)
            + (
                f" (легаси-бандл, --legacy-bundle={legacy_bundle})"
                if legacy_bundle
                else ""
            )
            + ")."
            "\n\n"
            if stamped
            else ""
        )
        + "Спека managed: `status: draft` НЕ исполняется при "
        "strict-governance — approve (перевод в approved) делает человек, "
        f"затем `spec-runner run --strict --spec-prefix={ws_id}-` в "
        "репо-владельце; после approve — нормализация frontmatter: "
        f"`make behaviour-tasks ARGS='--run-id <id> --conform-approve'`."
    )
    return ops.create_draft_pr(
        target_dir,
        repo_slug,
        branch,
        f"spec: {ws_id} tasks (draft) — {subject}",
        body,
        "",
    )


def _current_anchor_blob(
    target_dir: str, bundle_dir: str, legacy_bundle: int | None
) -> str:
    """Blob-хэш терминального узла АКТИВНОГО DAG — текущее (уже
    зачекаученное) содержимое `target_dir/bundle_dir` на диске.

    Общая точка сравнения для supersede-гейта (FIX 2, owner ruling): анкер,
    записанный в завершённом op ``tasks-deliver``, сравнивается с этим же
    хэшем, посчитанным заново, — равенство значит «апстрим ничего не
    поправил с последней доставки».
    """
    dag = _dag_for(legacy_bundle)
    anchor_path = Path(target_dir) / bundle_dir / dag[-1][0]
    if not anchor_path.exists():
        # Fail-closed (minor ревью PR #161): сырой FileNotFoundError не
        # ловится main()'ом (перехватывает только RuntimeError, строка с
        # `except RuntimeError` в CLI) — оператор получил бы traceback
        # вместо принятой в модуле формулировки с процедурой, как у
        # `_check_bundle_composition`.
        raise RuntimeError(
            f"{anchor_path} не найден — терминальный узел активного DAG "
            "отсутствует на диске (легаси-бандл без --legacy-bundle, "
            "бандл ещё не вмержен, либо bundle_dir указывает неверный "
            "путь); доавторьте недостающий узел либо передайте "
            "--legacy-bundle=3|4|5 с точным фактическим составом"
        )
    return blob_sha1(anchor_path.read_text(encoding="utf-8"))


def _deliver_fresh_for_run(
    state: RunState, ops: Ops, legacy_bundle: int | None
) -> int:
    """op_start → `deliver()` → op_complete(pr, anchor) — общий хвост
    обычной (первой) доставки и supersede-редоставки.

    Обе обязаны идти через write-ahead op_start/op_complete (ручная правка
    ledger недопустима, owner ruling); `op_start` вызывается ТОЛЬКО здесь,
    ПОСЛЕ того как supersede-гейт в `deliver_for_run` уже пропустил
    (round 2 ревью PR #161, finding 2/B) — отказ гейта (равный анкер)
    поднимает RuntimeError РАНЬШЕ, чем управление вообще попадает сюда, так
    что завершённый op не трогается ни при отказе, ни при неизвестном
    анкере до захода в доставку.

    Момент anchor (round 2 ревью PR #161, finding 1/A, контракт владельца):
    ПОСЛЕ-штамповые байты — anchor читается ПОСЛЕ `deliver()`, когда
    терминальный узел уже проштампован (`stamp_bundle_approved`) и именно
    ЭТИ байты уехали в PR доставки и окажутся на base после его мержа;
    supersede-гейт в `deliver_for_run` сравнивает со свежим base ИМЕННО
    этот же (пост-штамповый) снимок.
    """
    # approved_by/at — факт мержа бандл-PR (решение владельца devtools#110:
    # инициированный мерж = approve; mergedBy — различитель agent/human,
    # ADR-ECO-011). Отсутствие факта — стоп, не выдуманное значение.
    if state.pr is None:
        raise RuntimeError(
            "в леджере нет номера бандл-PR — штамп невозможен"
        )
    facts = ops.pr_facts(state.repo_slug, state.pr)
    merged_by = (facts.get("mergedBy") or {}).get("login")
    merged_at = facts.get("mergedAt")
    if not merged_by or not merged_at:
        raise RuntimeError(
            f"у PR #{state.pr} нет mergedBy/mergedAt — бандл не вмержен "
            "или API не отдал факт мержа; стоп"
        )
    op_start(state, "tasks-deliver")
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=state.base_ref or "master",
        ops=ops,
        approved_by=merged_by,
        approved_at=merged_at,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
    )
    # anchor — ПОСЛЕ deliver(): target_dir стоит на ветке доставки с уже
    # проштампованным (закоммиченным в PR) содержимым терминального узла —
    # ровно те байты, что окажутся на base после мержа (see docstring, A).
    anchor = _current_anchor_blob(
        state.target_dir, state.bundle_dir, legacy_bundle
    )
    op_complete(state, "tasks-deliver", pr=pr, anchor=anchor)
    return pr


def deliver_for_run(
    state: RunState,
    ops: Ops,
    legacy_bundle: int | None = None,
    supersede: bool = False,
) -> int:
    """Идемпотентная доставка tasks-спеки для прогона (кнопка spec-loop).

    Durable reconciliation поверх неидемпотентного `deliver()` (дизайн-
    решение владельца 2026-09-07): op ``tasks-deliver`` в `run.json`
    ведётся write-ahead (`started` ДО эффектов), а перед любой доставкой
    ищется уже созданный PR по ветке ``spec/<ws-id>-tasks`` — повтор
    НИКОГДА не создаёт PR заново, в каком бы состоянии op ни застал
    прогон (new/started/completed).

    `supersede` (FIX 2, owner ruling): опт-ин редоставка после апстрим-
    правки decomposition — обычный повторный прогон моста НЕ регенерирует
    (ledger уже несёт завершённый tasks-deliver, а `find_pr(any_state)`
    принял бы прежний PR как состоявшуюся доставку). Санкционирована
    ТОЛЬКО когда анкер (blob терминального узла активного DAG) ОТЛИЧАЕТСЯ
    от записанного в завершённом op (`anchor=`, пишет `op_complete` с этой
    правки) — равный анкер отказывает явно (upstream ничего не поправил).
    Op, записанный ДО появления поля anchor (леджер старой эры), — анкер
    расценивается как неизвестный: supersede разрешён по явному флагу, но с
    предупреждением, без сравнения. Под supersede найденный по ветке PR
    (`find_pr any_state`) НЕ принимается как готовая доставка — идёт
    свежая генерация, op пере-совершается с новым pr+anchor. Ручная правка
    ledger по-прежнему недопустима: путь всегда идёт через
    op_start/op_complete, как и обычная доставка.

    Возвращает номер PR; любое препятствие — RuntimeError (fail-closed,
    вызывающая сторона печатает и выходит ненулевым RC).
    """
    if state.status != "completed":
        raise RuntimeError(
            f"run {state.run_id!r} в статусе {state.status!r}, нужен "
            "'completed' — сперва доведите прогон (resume/verify)"
        )
    op = state.ops.get("tasks-deliver") or {}
    if op.get("status") == "completed":
        pr_done = op.get("pr")
        if pr_done is None:
            raise RuntimeError(
                "op tasks-deliver completed, но без номера PR — леджер "
                f"{state.run_id!r} повреждён или правлен вручную; "
                "почините op прежде, чем продолжать"
            )
        if not supersede:
            print(
                f"tasks-спека уже доставлена: PR #{pr_done} "
                f"({state.repo_slug}) — повтор не создаёт PR"
            )
            return pr_done
        # Гейт — ЗДЕСЬ, ДО op_start (round 2 ревью PR #161, finding 2/B):
        # отказ (RuntimeError ниже) поднимается раньше любого касания op —
        # завершённый tasks-deliver остаётся нетронутым при отказе.
        ops.checkout_and_pull(state.target_dir, state.base_ref or "master")
        recorded_anchor = op.get("anchor")
        if recorded_anchor is None:
            print(
                f"op tasks-deliver (PR #{pr_done}) завершён до появления "
                "поля anchor — анкер неизвестен, supersede выполняется по "
                "явному флагу без сравнения хэша"
            )
        else:
            # current_anchor — те же ПОСЛЕ-штамповые байты, что recorded_
            # anchor (A): свежий base уже несёт содержимое, вмерженное из
            # PR предыдущей доставки (пост-штамповое), так что сравнение
            # сопоставимо и корректно ловит «апстрим не менялся».
            current_anchor = _current_anchor_blob(
                state.target_dir, state.bundle_dir, legacy_bundle
            )
            if current_anchor == recorded_anchor:
                raise RuntimeError(
                    "supersede отклонён: анкер decomposition не изменился "
                    f"с последней доставки (PR #{pr_done}) — upstream "
                    "ничего не поправил, повторная доставка не нужна"
                )
        # Свежая доставка МИМО adoption чужого PR (owner ruling): найденный
        # find_pr(any_state) PR НЕ принимается как состоявшаяся доставка.
        return _deliver_fresh_for_run(state, ops, legacy_bundle)
    # Поиск PR по ветке ВО ВСЕХ состояниях (major терм. ревью #156):
    # отсутствие ОТКРЫТОГО PR не значит «доставки не было» — спека могла
    # быть доставлена ранее, вмержена и переведена в approved; повторный
    # deliver() перегенерировал бы её обратно в draft вторым PR-ом.
    branch = f"spec/{state.ws_id}-tasks"
    existing = ops.find_pr(state.repo_slug, branch, any_state=True)
    if existing is not None:
        pr_state = ops.pr_facts(state.repo_slug, existing).get("state")
        if pr_state not in ("OPEN", "MERGED"):
            raise RuntimeError(
                f"PR #{existing} по ветке {branch} закрыт без мержа "
                f"(state={pr_state!r}) — реконсиляция fail-closed: "
                "решите судьбу ветки/PR вручную, повторная доставка "
                "поверх отклонённой не выполняется"
            )
        ops.checkout_and_pull(state.target_dir, state.base_ref or "master")
        anchor = _current_anchor_blob(
            state.target_dir, state.bundle_dir, legacy_bundle
        )
        op_complete(state, "tasks-deliver", pr=existing, anchor=anchor)
        print(
            f"найден существующий PR #{existing} по ветке {branch} "
            f"({pr_state}) — принят как доставка, новый не создаётся"
        )
        return existing
    return _deliver_fresh_for_run(state, ops, legacy_bundle)


def deliver_conform(
    target_dir: str,
    repo_slug: str,
    ws_id: str,
    bundle_dir: str,
    ops: Ops,
    legacy_bundle: int | None = None,
) -> int:
    """Нормализация после approve владельца → номер PR (нового или уже
    открытого).

    Глобального dirty-гарда здесь НЕТ намеренно: approve-штамп владельца
    (`spec approve`) живёт в рабочем дереве незакоммиченным — он и есть
    груз этого PR. commit_paths берёт только tasks-файл.

    Состав бандла проверяется В НАЧАЛЕ функции, ДО `ops.ensure_branch`
    (Task 7 плана acceptance-node): здесь НЕТ ни dirty-гарда, ни
    checkout, ни existence-гардов (их отсутствие — намеренный инвариант
    выше) — отказ по составу не должен оставлять в target созданную
    approve-ветку.

    Идемпотентность (приёмка PR #117, круги 1–2): при уже открытом PR
    ветки повторный запуск НЕ создаёт второй PR (`gh pr create` упал бы),
    но по-прежнему доставляет текущее содержимое — свежий незакоммиченный
    approve-штамп владельца коммитится и пушится В ТУ ЖЕ ветку (пустой
    индекс/актуальный push — no-op у RealOps).
    """
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    anchor_node_id = _node_id(dag[-1][0])
    anchor_filename = dag[-1][0]
    branch = f"spec/{ws_id}-tasks-approve"
    existing = ops.find_pr(repo_slug, branch)
    ops.ensure_branch(target_dir, branch)
    changed = conform_approved(
        target_dir, ws_id, bundle_dir, legacy_bundle=legacy_bundle
    )
    rel = f"spec/{ws_id}-tasks.md"
    ops.commit_paths(
        target_dir,
        [rel],
        f"spec: {ws_id} tasks — approve-штамп владельца + нормализация "
        "frontmatter (conform-approve)",
    )
    ops.push_branch(target_dir, branch)
    if existing is not None:
        return existing
    return ops.create_draft_pr(
        target_dir,
        repo_slug,
        branch,
        f"spec: {ws_id} tasks — approve + нормализация frontmatter",
        (
            f"Approve-штамп владельца для spec/{ws_id}-tasks.md и "
            "нормализация frontmatter под активный governance-профиль: "
            f"traces_to ровно [{anchor_node_id}] (якорь — терминальный узел "
            "_BUNDLE_DAG, либо его легаси-вариант при --legacy-bundle=3|4|5 "
            "— 3/4 усечённый префикс до behaviour-spec/design, 5 — "
            "отдельный _BUNDLE_DAG_LEGACY5 до раскатки acceptance-узла; "
            "lite-профиль spec-runner может дописать/подменить traces — "
            "других профилей у него нет, upstream-плечо заведено "
            "отдельно), пин upstream_hashes — на текущий blob вмерженного "
            f"{bundle_dir}/{anchor_filename}."
            + ("" if changed else " Файл уже был конформен — PR несёт "
               "только approve-штамп.")
        ),
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: параметры доставки берутся из леджера прогона (`run.json`)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--conform-approve", action="store_true",
        help="после `spec approve` владельца: нормализовать frontmatter "
        "tasks-спеки и доставить approve-штамп PR-ом",
    )
    parser.add_argument(
        "--legacy-bundle", type=int, choices=(3, 4, 5), default=None,
        help="точный фактический состав легаси-бандла: 3 — "
        "charter+requirements+behaviour-spec (без design/acceptance/"
        "decomposition); 4 — + design (без acceptance/decomposition); "
        "5 — + decomposition, но БЕЗ acceptance (бандл до раскатки "
        "acceptance-узла, decomposition пинует только design); без флага — "
        "полный DAG (+ acceptance, decomposition пинует design и "
        "acceptance); значение обязано совпасть с составом каталога РОВНО, "
        "лишний либо недостающий узел отказывает",
    )
    parser.add_argument(
        "--supersede", action="store_true",
        help="санкционированная редоставка (FIX 2, owner ruling) поверх "
        "уже завершённого tasks-deliver: разрешено ТОЛЬКО если анкер "
        "decomposition (blob терминального узла активного DAG) изменился "
        "с прошлой доставки — равный анкер отказывает явно; прежний PR "
        "(find_pr any_state) НЕ принимается как состоявшаяся доставка, "
        "идёт свежая генерация с новым pr+anchor",
    )
    args = parser.parse_args(argv)
    state = load(args.run_id)
    # Мост работает только над ВМЕРЖЕННЫМ и верифицированным бандлом
    # (приёмка PR #96, major): completed — единственный статус, в котором
    # S8 подтвердил бандл на дефолтной ветке. merged_unverified — мерж без
    # зелёного гейта, задачи из него генерировать нельзя.
    if state.status != "completed":
        print(
            f"task_bridge: run {state.run_id!r} в статусе "
            f"{state.status!r}, нужен 'completed' — сперва доведите "
            "прогон (resume/verify)"
        )
        return 1
    ops = RealOps()
    if args.conform_approve:
        pr = deliver_conform(
            target_dir=state.target_dir,
            repo_slug=state.repo_slug,
            ws_id=state.ws_id,
            bundle_dir=state.bundle_dir,
            ops=ops,
            legacy_bundle=args.legacy_bundle,
        )
        print(
            f"approve-штамп + нормализация доставлены: PR #{pr} "
            f"({state.repo_slug})"
        )
        return 0
    # Доставка — только через deliver_for_run (durable reconciliation,
    # кнопка spec-loop): write-ahead op tasks-deliver + поиск уже
    # созданного PR по ветке; повтор не создаёт PR заново.
    try:
        pr = deliver_for_run(
            state, ops, legacy_bundle=args.legacy_bundle,
            supersede=args.supersede,
        )
    except RuntimeError as exc:
        print(f"task_bridge: {exc}")
        return 1
    print(f"draft tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
