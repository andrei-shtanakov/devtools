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
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

import yaml

from governance import acceptance_guard, decomposition_guard, design_guard
from governance.ops import (
    REVIEW_GH_CONFIG_DIR,
    Ops,
    RealOps,
    review_login,
)
from governance.policy_sources import PREFLIGHT_PROCEDURE_HINT, target_profile_declares
from governance.run_state import RunState, load, op_complete, op_start, save
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

    `version` (Task 7 плана supersede): номер ревизии tasks-спеки —
    `1` у первой доставки, `_previous_tasks_version(state) + 1` у
    переиздания (`deliver_superseded`). Дефолт `1` сохраняет прежний
    захардкоженный текст для всех вызовов без явного значения.
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
        ws_id, subject, generated_at, anchor_blob, anchor_node_id="decomposition",
        version=version,
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
            # Источник targets (FIX 1, owner ruling DT-14 multi-file group;
            # major ревью PR #161, round 6: UNION, не замещение): verify-DT
            # ВЛАДЕЕТ своими checked_by-целями (через scenarios) И
            # НАБЛЮДАЕТ файлы из verifies — обе группы обязаны прогоняться
            # verify_first, иначе собственный тест-файл DT молча выпадает
            # из прогона, хотя чек-лист той же задачи требует его зелёным.
            # Порядок ДЕТЕРМИНИРОВАН и задокументирован: СНАЧАЛА
            # собственные checked_by-цели сценариев (порядок scenarios,
            # полный pytest-селектор с `::`), ПОТОМ verifies (порядок
            # объявления, голые пути по канону гарда — decomposition_guard
            # сравнивает verifies с checked_by-целями ПОСЛЕ среза `::`).
            # Дедуп — ПО ПОЛНОЙ СТРОКЕ селектора (round 11 ревью PR #161,
            # major — откат round-8/9 «дедупа по файлу»: тот дедуп молча
            # ронял ВТОРОЙ checked_by-селектор СОБСТВЕННЫХ сценариев DT,
            # когда два сценария single-owner-задачи бьют в один файл
            # разными селекторами (`tests/x.py::t1` + `tests/x.py::t2`,
            # форма SHARED_FILE_BEHAVIOUR_MD) — чек-лист задачи требует
            # обе цели зелёными, а **Verifies:** нёс бы только первую.
            # Голый путь в verifies и полный `file::test`-селектор из
            # checked_by того же файла — РАЗНЫЕ строки, ОБЕ остаются
            # (spec-runner резолвит пересечение сам); срез `::` для
            # сверки владения/принадлежности — дело ТОЛЬКО гарда
            # (`decomposition_guard`, ownership/closure-проверки), не
            # рендера. Легаси-путь (verifies не объявлен вовсе) — ничем
            # не отличим от чисто checked_by-вывода, как раньше.
            targets: list[str] = []
            for b in t.scenarios:
                sc_target = (
                    by_beh[b].checked_target if b in by_beh else None
                )
                if sc_target and sc_target not in targets:
                    targets.append(sc_target)
            for f in t.verifies:
                if f not in targets:
                    targets.append(f)
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
    restamp_nodes: frozenset[str] | None = None,
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

    `restamp_nodes` (§I7, фикс-круг PR #165 + правка по решению владельца
    2026-09-09) — режим ПЕРЕИЗДАНИЯ, и только его. К моменту supersede
    бандл в base уже проштампован предыдущей доставкой (её tasks-PR
    вмержен), а correction-PR правит тело узла, не frontmatter — статус
    остаётся `approved`, и ветка выше не исполняется вовсе. Тогда
    подпись, разрешённая по §I7 у correction-PR, отбрасывалась бы, и
    переизданный анкер утверждал бы, что текущие байты одобрил человек,
    мерживший ИСХОДНЫЙ бандл-PR — дословно то, что §I7 объявляет
    недопустимым.

    §I7 ПОУЗЛОВОЙ: аргумент — множество node-id, которые correction-PR
    действительно менял (состав его файлов, `ops.pr_files`), и подпись
    перезаписывается ТОЛЬКО на них:

    - `None` (дефолт, обычная доставка `deliver_for_run`) — поведение
      байт-в-байт прежнее: чужая подпись на approved-узле не трогается,
      а узел в любом другом статусе законно идёт `draft → approved` с
      подписью бандл-PR. Понятия `signed_nodes` на этом пути нет вовсе,
      и ни один отказ ниже на нём не достижим;
    - узел, которого correction-PR не касался, сохраняет ИСХОДНЫЙ
      провенанс: приписать ему подпись correction-PR значит стереть
      факт, что эти байты одобрил другой человек и в другой момент.
      Механическая перепиновка downstream-узла (его upstream сменил
      подпись ⇒ сменился и пин) байты узла меняет, а подпись — нет:
      перепиновка не событие approve;
    - перезапись УСЛОВНАЯ (только если подпись отличается от требуемой) —
      повторный заход переиздания с тем же correction-PR не меняет ничего
      и в `changed` не входит; на этом детерминизме стоит §I3.1;
    - `version` НЕ инкрементится: инкремент привязан к ПЕРЕХОДУ
      в `approved` (он внутри той же ветки), а не к «файл тронули».
      Перештамп перехода не совершает — он исправляет провенанс тех же
      байтов, которые доставил correction-PR, и приписывать им новое
      поколение документа нечем.

    §I7 ДЕЙСТВУЕТ И НА ПЕРЕХОДЕ (правка по решению владельца 2026-09-09,
    devtools#172). `draft`/`stale` — не экзотика, а штатный след
    correction'а: коррекция бандла приводит узлы ровно в эти статусы.
    Пока `restamp_nodes` смотрела только ветка уже approved-узла,
    поузловое правило на этом — основном — пути не работало вовсе:
    переходящий узел получал подпись correction-PR независимо от состава
    его файлов. Живой случай (переиздание
    `verify-first-file-scope-group-targets-20260908`, `approval_pr: 407`):
    `design`/`acceptance` были `stale` и вне `signed_nodes`, а после
    штампа утверждали, что их одобрил человек, мерживший #407 — PR, их
    не касавшийся. Ровно та ложь о провенансе, ради запрета которой §I7 и
    писался. Поэтому у перехода вне `signed_nodes` подпись берётся
    `_require_preserved_signature` — сохраняется прежняя либо fail-closed.

    `version` у сохранившего подпись `stale`-узла всё же РАСТЁТ: он
    совершает переход, а §I5 требует, чтобы фактический штамп совпал с
    проспективным (`_content_anchor` считает его с `restamp_nodes=None`,
    то есть всегда первой веткой). Канонизация вырезает подпись, но НЕ
    `status`/`version` — оставь мы `version` прежним, записанный
    `content_anchor` разошёлся бы с тем, что доставка кладёт в base, и
    no-op второго круга стал бы недостижим.
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
        signed = restamp_nodes is not None and _node_id(name) in restamp_nodes
        status = meta.get("status")
        if status != "approved":
            if restamp_nodes is None or signed:
                meta["approved_by"] = approved_by
                meta["approved_at"] = approved_at
            else:
                # Подпись не меняется — либо сохраняется прежняя, либо
                # отказ; `version`/`status` идут общим путём перехода.
                _require_preserved_signature(bundle_dir, name, status, meta)
            meta["status"] = "approved"
            meta["version"] = int(meta.get("version") or 1) + 1
            dirty = True
        elif (
            signed
            and (
                meta.get("approved_by") != approved_by
                or meta.get("approved_at") != approved_at
            )
        ):
            # §I7 на реальном состоянии base переиздания: узел approved,
            # но подпись — от предыдущей доставки. `version` здесь не
            # растёт (см. докстринг), статус уже верен.
            meta["approved_by"] = approved_by
            meta["approved_at"] = approved_at
            dirty = True
        elif restamp_nodes is not None and not signed:
            # Клетка §I7 «узел уже approved, вне signed_nodes»: подпись
            # мы не ставим — но и пустой она быть не вправе. Перехода
            # здесь нет, файл не трогается; проверяется только то, что
            # `approved` не стоит без подписавшего.
            _require_preserved_signature(bundle_dir, name, status, meta)
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


#: Единственный статус, с которого узел ВНЕ `signed_nodes` вправе войти в
#: `approved`, сохранив прежнюю подпись: `stale` означает «изменился
#: upstream», то есть собственное содержание узла осталось тем же, что
#: одобрил его прежний подписант (§I7, решение владельца 2026-09-09).
_PRESERVING_STATUS = "stale"

#: Статусы, на которых узел ВНЕ `signed_nodes` проходит проверку, если
#: прежняя подпись непуста: `stale` входит в `approved`, сохранив её,
#: а `approved` остаётся как есть. Разница между клетками только в
#: переходе; требование к подписи у них общее — она обязана БЫТЬ.
_SIGNATURE_BEARING_STATUSES = (_PRESERVING_STATUS, "approved")

#: Процедура оператору при fail-closed поузлового §I7 — отказ без
#: процедуры бесполезен.
_UNSIGNED_NODE_PROCEDURE = (
    "назовите correction, покрывающий этот узел (--approval-pr <n>), "
    "либо доставьте его правку отдельным PR"
)


def _require_preserved_signature(
    bundle_dir: str,
    name: str,
    status: object,
    meta: dict,
) -> None:
    """Узел вне `signed_nodes`: прежняя подпись есть и сохранима?

    Зовётся на ДВУХ клетках политики §I7, и обе про одно — «подпись мы
    не ставим, значит она обязана уже быть»:

    - `stale → approved`: переход разрешён ровно из `stale` и ровно при
      непустой прежней подписи;
    - узел УЖЕ `approved`: перехода нет вовсе, но подпись всё равно
      обязана быть непустой.

    `meta` не мутируется: сохранение подписи и есть «ничего не трогать»,
    проверить остаётся только право на это.

    Вторая клетка была объявлена спекой и не реализована (major ревью
    3c07bb0): первая ветка политики входит только при
    `status != "approved"`, вторая требует членства в `signed_nodes` —
    и узел `approved` с пустым `approved_by` не проверял никто.
    Переиздание доставляло его с RC 0: «одобрено» без одобрившего, молча.
    Поймать это потом нечем — `_content_anchor` подпись вырезает по
    построению (§I2), поэтому ни сверка проспективного anchor'а, ни
    гвард `_commit_facts_cb` расхождения не видят, а аудит по леджеру не
    отличает пустую подпись от потерянной.

    Почему `stale` — да. Статус выставлен потому, что сменился upstream,
    а не содержание узла: те же байты тела одобрял тот же человек, и
    прежняя подпись остаётся истинной.

    Почему `draft` — нет. Здесь содержание могло измениться, и изменить
    его мог ДРУГОЙ correction-PR, которого мы не разрешали. Сохранённая
    подпись утверждала бы, что прежний подписант одобрил байты, которых
    не видел, — недоказуемо, значит fail-closed. По той же причине
    fail-closed и любой иной не-approved статус: доказательство есть
    только у `stale`.

    Почему отсутствующая подпись — нет. Сохранять нечего: узел никогда не
    был одобрен, а выдумать провенанс неоткуда. Форма «не подписано» —
    та, которую заводит шаблон бандла: `approved_by: ""`.
    """
    if (
        status in _SIGNATURE_BEARING_STATUSES
        and meta.get("approved_by")
        and meta.get("approved_at")
    ):
        return
    reason = (
        "прежней подписи нет (approved_by/approved_at пусты) — сохранять "
        "нечего"
        if status in _SIGNATURE_BEARING_STATUSES
        else (
            f"status={status!r} — сохранять прежнюю подпись недоказуемо "
            "(содержание узла мог изменить другой correction-PR), а "
            "приписать подпись correction-PR, который узла не касался, "
            "значит солгать о провенансе"
        )
    )
    raise RuntimeError(
        f"{bundle_dir}/{name}: узел вне signed_nodes переиздания, "
        f"{reason}. Процедура: {_UNSIGNED_NODE_PROCEDURE}"
    )


@contextmanager
def _shadow_stamped(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None,
    restamp_nodes: frozenset[str] | None,
) -> Iterator[Path]:
    """Копия бандла, проштампованная во временном каталоге; → её target-корень.

    Общий примитив обоих проспективных вычислений (`_prospective_anchor`
    §I2 и `_content_anchor` §I5): штамп — эффект, а сравнивать надо те
    байты, что уйдут в PR. Рабочее дерево не трогается вовсе
    (утверждается тестом `..._writes_nothing`).

    Состав бандла проверяется по ФАКТИЧЕСКОМУ каталогу в `target_dir` и
    ДО копирования: в теневом каталоге лежит ровно заявленное подмножество,
    и вызов `_check_bundle_composition` внутри `stamp_bundle_approved`
    там вырождается в тождество. Без этой проверки легаси-бандл без
    `25-acceptance.md` с забытым `--legacy-bundle=5` ронял сырой
    `FileNotFoundError` из `src.read_text()` мимо диагностики
    `stamp_bundle_approved` (`main` ловит только RuntimeError).
    """
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    with tempfile.TemporaryDirectory(prefix="prospective-stamp-") as tmp:
        shadow = Path(tmp) / "target"
        (shadow / bundle_dir).mkdir(parents=True)
        for fname, _ in dag:
            src = Path(target_dir) / bundle_dir / fname
            (shadow / bundle_dir / fname).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        stamp_bundle_approved(
            str(shadow), bundle_dir, approved_by, approved_at,
            legacy_bundle=legacy_bundle,
            restamp_nodes=restamp_nodes,
        )
        yield shadow


#: Ключи frontmatter, которые КАНОНИЧЕСКОЕ представление узла вырезает:
#: провенанс approve, а не содержание (§I5).
_SIGNATURE_KEYS = ("approved_by", "approved_at")

#: Подпись-заглушка проспективного штампа при вычислении `content_anchor`.
#: Канонизация подпись вырезает, поэтому результат от значения не зависит —
#: и ровно это позволяет §I5 стоять ДО сетевого разрешения §I7.
_CANON_SIGNATURE = ("-", "-")


def _canonical_dag_hash(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    """Канонический хэш DAG: содержание бандла без провенанса approve.

    ОПРЕДЕЛЕНИЕ (контракт, не деталь реализации). Каноническое,
    signature-free представление узла — текст его файла, у которого:

    - из frontmatter удалены `approved_by` и `approved_at`;
    - `upstream_hashes` (у узла с upstream'ами) заменены на КАНОНИЧЕСКИЕ
      блобы этих upstream'ов, то есть на `blob_sha1` их же
      signature-free представлений, вычисленные тем же правилом
      рекурсивно (порядок обхода `dag` топологический, поэтому к моменту
      узла его upstream'ы уже посчитаны).

    Второй пункт — не украшение. Реальный пин считается с байтов
    upstream-файла ВМЕСТЕ с подписью: без пересчёта смена подписи одного
    узла каскадом меняла бы пины всех, кто его пинует, и подпись
    протекла бы в «содержание» ровно там, где §I5 обязан её не видеть.

    Хэш всего DAG — `blob_sha1` манифеста «`<node-id> <канонический
    блоб>`», по строке на узел в порядке DAG. Манифест, а не блоб
    терминального узла: терминальный пинует лишь своё транзитивное
    замыкание, и узел вне него в хэш бы не вошёл — «хэш активного DAG»
    обязан покрывать активный DAG целиком.

    Что НЕ вырезается: `status` и `version`. Их меняет только переход
    draft → approved, а обе стороны сверки §I5 считаются по
    ПРОШТАМПОВАННОМУ дереву (`_content_anchor`), где переход уже
    совершён, — значит они совпадают и содержание не размывают.
    """
    canon: dict[str, str] = {}
    lines: list[str] = []
    base = Path(target_dir) / bundle_dir
    for fname, upstream_ids in dag:
        meta, body = split_frontmatter(
            (base / fname).read_text(encoding="utf-8")
        )
        for key in _SIGNATURE_KEYS:
            meta.pop(key, None)
        if upstream_ids:
            meta["upstream_hashes"] = {u: canon[u] for u in upstream_ids}
        node_id = _node_id(fname)
        canon[node_id] = blob_sha1(join_frontmatter(meta, body))
        lines.append(f"{node_id} {canon[node_id]}")
    return blob_sha1("\n".join(lines) + "\n")


def _content_anchor(
    target_dir: str,
    bundle_dir: str,
    legacy_bundle: int | None = None,
) -> str:
    """`content_anchor` §I5: канонический хэш ПРОШТАМПОВАННОГО DAG.

    Отвечает на вопрос «менялось ли содержание апстрима», и только на
    него. Пост-штамповый `anchor` (§I2) на него ответить не может: он —
    точный blob терминального узла ПОСЛЕ штампа, а штамп несёт подпись
    correction-PR, то есть провенанс лежит ВНУТРИ сравниваемых байтов.
    Бандл-PR и tasks-PR вмержены разными людьми в разное время, поэтому
    равенство §I5 по `anchor` не достигалось никогда — каждый
    `--supersede` заводил бы очередную ревизию.

    Штамп применяется ПРОСПЕКТИВНО (теневой каталог): те же
    преобразования, что уйдут в PR, — `status: approved`, инкремент
    `version` на переходе, перепиновка. Иначе доставка v1, чей base ещё
    `draft`, записала бы хэш ДОштамповых байтов, а следующее переиздание
    считало бы его по уже проштампованному base — §I5 расходился бы на
    пустом месте.

    Подпись штампа здесь — ЗАГЛУШКА (`_CANON_SIGNATURE`), а не факты
    correction-PR: канонизация подпись вырезает, поэтому результат от неё
    не зависит. Ровно это и позволяет §I5 стоять ДО сетевого разрешения
    провенанса (§I7) — то есть до того, как переиздание могло бы принять
    СВОЙ ЖЕ tasks-PR за correction.

    По той же причине `restamp_nodes` здесь `None`, и это НЕ забывчивость,
    а контракт: канонический штамп — нейтральная процедура, клеток
    поузловой политики §I7 он не применяет. На его шаге состав
    подписываемых узлов ещё не вычислен и вычислен быть НЕ МОЖЕТ —
    провенанс резолвится позже и только если содержание изменилось.
    Передай сюда любое множество (хоть пустое) — fail-closed политики
    сработал бы на ЛЮБОМ бандле с `draft`-узлом, то есть раньше, чем
    выяснено, менялся ли апстрим: бесследный no-op §I5 стал бы
    недостижим. Утверждается тестами `..._canonical_stamp_is_neutral...`
    и `..._noop_reachable_with_draft_node_outside_signed_nodes`.
    """
    dag = _dag_for(legacy_bundle)
    with _shadow_stamped(
        target_dir, bundle_dir, *_CANON_SIGNATURE, legacy_bundle, None
    ) as shadow:
        return _canonical_dag_hash(str(shadow), bundle_dir, dag)


def _prospective_anchor(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None = None,
    restamp_nodes: frozenset[str] | None = None,
) -> str:
    """blob терминального узла ПОСЛЕ штампа, без записи в рабочее дерево.

    §I2 спеки: сравнивать надо те байты, что уйдут в PR и после мержа
    лягут в base, но штамп — эффект. Поэтому бандл копируется во временный
    каталог, штампуется ТАМ (`_shadow_stamped`), и хеш берётся оттуда;
    рабочее дерево не трогается вовсе (тест `..._writes_nothing`).

    В отличие от `content_anchor` (§I5) здесь берутся ТОЧНЫЕ байты со
    всей подписью: §I2 — идентичность доставки, и ей провенанс не помеха,
    а часть предмета. Разделение ролей и есть фикс дефекта §I5×§I7.

    `restamp_nodes` пробрасывается в штамп БЕЗ изменений: §I2 требует
    «те же преобразования», и проспективный anchor обязан совпасть с
    фактическим. Состав, забытый на одной из двух сторон, разводит их — и
    гард `_commit_facts_cb` рвёт доставку между коммитом и push.
    """
    dag = _dag_for(legacy_bundle)
    with _shadow_stamped(
        target_dir, bundle_dir, approved_by, approved_at,
        legacy_bundle, restamp_nodes,
    ) as shadow:
        anchor_file = shadow / bundle_dir / dag[-1][0]
        return blob_sha1(anchor_file.read_text(encoding="utf-8"))


# --- §I12: одобрение узла бандла — человеческий акт ----------------------
#
# Одна величина, один вопрос: узел активного DAG ЧЕСТНО ОДОБРЕН, когда
# выполнены все три условия сразу — `status: approved`, непустая подпись
# (`approved_by`/`approved_at`; шаблон бандла заводит `approved_by: ""`, и
# пустая строка есть «не подписано», а не подпись) и сходимость КАЖДОГО
# пина с фактическим блобом upstream-файла В ТОМ ЖЕ ДЕРЕВЕ. Предикат один
# на все проверки: гейт доставки, топологическая готовность upstream у
# `--approve-node` и отчёт о готовности волны спрашивают ровно его.


def _dag_files(
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, str]:
    """node-id → имя файла активного DAG."""
    return {_node_id(fname): fname for fname, _ in dag}


def _pin_drift(
    base: Path,
    files: dict[str, str],
    upstream_ids: tuple[str, ...],
    meta: dict,
) -> list[tuple[str, object, str]]:
    """Разошедшиеся пины узла: `(upstream-id, записанный, фактический)`.

    Сравнение идёт с блобом upstream-файла В ТОМ ЖЕ ДЕРЕВЕ — предикат
    относителен дереву, потому что и подпись относительна ему: она
    утверждает одобрение конкретных байтов, а байты живут в дереве.
    """
    pins = meta.get("upstream_hashes")
    pins = pins if isinstance(pins, dict) else {}
    drift: list[tuple[str, object, str]] = []
    for upstream_id in upstream_ids:
        actual = blob_sha1(
            (base / files[upstream_id]).read_text(encoding="utf-8")
        )
        if pins.get(upstream_id) != actual:
            drift.append((upstream_id, pins.get(upstream_id), actual))
    return drift


def _approval_findings(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    nodes: tuple[str, ...] | None = None,
) -> list[str]:
    """Узлы, не проходящие предикат честной одобренности — по строке.

    Пустой список — «все спрошенные узлы честно одобрены». `nodes`
    сужает вопрос до перечисленных node-id (топологическая готовность
    прямых upstream — тот же предикат на подмножестве, а не второе
    правило рядом с первым).

    Сходимость пинов спрашивается ТОЛЬКО у `approved`-узла, и это не
    послабление: `stale` с разошедшимися пинами — нормальное состояние
    честного долга («что именно покрывала подпись»), а `approved` с
    разошедшимися — состояние, которое контракт объявляет незаконным.
    Разница не в байтах, а в том, что утверждает статус.

    У корневого узла (без upstream) условие сходимости выполнено пусто —
    это не поблажка, а отсутствие предмета.
    """
    base = Path(target_dir) / bundle_dir
    files = _dag_files(dag)
    findings: list[str] = []
    for fname, upstream_ids in dag:
        node = _node_id(fname)
        if nodes is not None and node not in nodes:
            continue
        meta, _ = split_frontmatter((base / fname).read_text(encoding="utf-8"))
        status = meta.get("status")
        if status != "approved":
            findings.append(
                f"{bundle_dir}/{fname}: status={status!r} — узел не одобрен "
                f"(долг человеческого approve: --approve-node {node})"
            )
            continue
        if not (meta.get("approved_by") and meta.get("approved_at")):
            findings.append(
                f"{bundle_dir}/{fname}: status=approved, но approved_by/"
                "approved_at пусты — «одобрено» без одобрившего"
            )
        for upstream_id, recorded, actual in _pin_drift(
            base, files, upstream_ids, meta
        ):
            findings.append(
                f"{bundle_dir}/{fname}: пин {upstream_id} записан "
                f"{recorded!r}, фактический блоб {actual!r} — подпись стоит "
                "под байтами, которых в этом дереве нет"
            )
    return findings


#: Процедура оператору при отказе гейта §I12. Отказ без процедуры
#: бесполезен — то же требование, что у §I10.
_APPROVE_PROCEDURE = (
    "одобрите перечисленные узлы в топологическом порядке "
    "(`--approve-node <node-id>` по каждому), затем approve-PR ветки "
    "`spec/<ws-id>-bundle-approve`, человеческий мерж — и повторите доставку"
)


def _require_approved_dag(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    """Гейт §I12 — общий для ЛЮБОЙ доставки, и он только ПРОВЕРЯЕТ.

    Там, где `stamp_bundle_approved` СОЗДАВАЛ approved-состояние, доставка
    теперь его ТРЕБУЕТ. Штамп, встретив неодобренный узел, доводил
    доставку до конца и оставлял ложь в артефакте; проверка на том же
    узле останавливает доставку до ветки, коммита и PR. Fail-open стал
    fail-closed без единого нового понятия — операция сменила знак.
    """
    findings = _approval_findings(target_dir, bundle_dir, dag)
    if findings:
        raise RuntimeError(
            "активный DAG одобрен не целиком (§I12) — доставка не начата:\n"
            + "\n".join(f"- {f}" for f in findings)
            + f"\nПроцедура: {_APPROVE_PROCEDURE}"
        )


#: Статусы, из которых узел входит в `approved` (§I12 п.3). Никакой
#: другой в `approved` не входит: он либо уже там, либо контракту неизвестен.
_APPROVABLE_STATUSES = ("draft", "stale")


def _approvable(
    target_dir: str,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node_id: str,
) -> bool:
    """Узел готов к approve (True) либо уже честно одобрен (False); иначе
    отказ.

    ЧИСТОЕ ЧТЕНИЕ — ни одна ветка ниже ничего не пишет, и это свойство
    несущее: `approve_node_for_run` зовёт функцию ДО создания
    накапливающей ветки, потому что §I12 п.6 требует, чтобы отказ не
    оставил ни файла узла, ни статусов downstream, ни ветки. Второй раз
    её же зовёт `approve_node` — гвард для прямых вызовов; читать дерево
    дважды дешевле, чем разводить валидацию и запись по двум правилам.
    """
    files = _dag_files(dag)
    if node_id not in files:
        raise RuntimeError(
            f"узел {node_id!r} не входит в активный DAG — approve есть акт о "
            "ПОЗИЦИИ В ГРАФЕ, а не о файле на диске; допустимые node-id: "
            + ", ".join(files)
        )
    node_findings = _approval_findings(
        target_dir, bundle_dir, dag, nodes=(node_id,)
    )
    if not node_findings:
        return False
    base = Path(target_dir) / bundle_dir
    meta, _ = split_frontmatter(
        (base / files[node_id]).read_text(encoding="utf-8")
    )
    status = meta.get("status")
    if status == "approved":
        # §I12 п.5: молчаливая перепиновка (или дописывание подписи) под
        # сохранённым провенансом — ровно дефект spec-runner#410,
        # вызванный человеком по другому поводу. Узел обязан сначала
        # ПРИЗНАТЬ долг и уже из `stale` быть одобрен заново.
        raise RuntimeError(
            f"{bundle_dir}/{files[node_id]}: узел approved, но состояние "
            "незаконно — перепиновать его под сохранённой подписью механика "
            "не вправе:\n"
            + "\n".join(f"- {f}" for f in node_findings)
            + "\nПроцедура: вернуть узел в `stale` коррекцией в "
            "репо-владельце (апстрим действительно сдвинулся) и одобрить "
            f"заново: --approve-node {node_id}"
        )
    if status not in _APPROVABLE_STATUSES:
        raise RuntimeError(
            f"{bundle_dir}/{files[node_id]}: status={status!r} — в approved "
            f"входят только {' и '.join(_APPROVABLE_STATUSES)}"
        )
    upstream_ids = next(u for f, u in dag if _node_id(f) == node_id)
    upstream_findings = _approval_findings(
        target_dir, bundle_dir, dag, nodes=upstream_ids
    )
    if upstream_findings:
        # Прямые, а не транзитивные: каждый approve сам требовал того же
        # от своих upstream, значит индукция покрывает всё замыкание
        # предков. Индукция здесь не допущение — условие сходимости пинов
        # ловит замыкание, собранное НЕ этой командой.
        raise RuntimeError(
            f"{bundle_dir}/{files[node_id]}: топологическая готовность не "
            "предъявлена — прямые upstream не одобрены:\n"
            + "\n".join(f"- {f}" for f in upstream_findings)
            + "\nПорядок обхода — топологический, сверху вниз: "
            + " → ".join(_dag_files(dag))
        )
    return True


def _cascade_stale(
    base: Path,
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    changed_node: str,
) -> list[str]:
    """Рекурсивный каскад долга вниз по DAG; → rel-пути помеченных файлов.

    Approve узла X меняет байты X, значит пины его downstream перестают
    сходиться. Оставить их `approved` значило бы своими руками создать
    незаконное состояние, а следующая команда упёрлась бы в тупик:
    одобрить `approved`-узел с неверными пинами нельзя, а перевести его в
    `stale` некому.

    Каскад РЕКУРСИВНЫЙ, а не одноуровневый: смена статуса есть правка
    файла, а файл узла входит в пин его собственных downstream —
    одноуровневый каскад создавал бы то же незаконное состояние уровнем
    ниже. Ветка обрывается на узле, уже лежащем `draft`/`stale`: долг у
    него объявлен, помечать нечем. Рекурсия конечна без счётчика —
    помеченный узел сам становится долговым.

    Меняется РОВНО `status`: подпись остаётся записью о прошлом
    одобрении, `version` не растёт (нового поколения одобрения не
    случилось), пины продолжают указывать на одобренные байты — этого
    прямо требует условие (3) предиката для `stale`.
    """
    downstream: dict[str, list[str]] = {}
    for fname, upstream_ids in dag:
        for upstream_id in upstream_ids:
            downstream.setdefault(upstream_id, []).append(fname)
    marked: list[str] = []
    queue = [changed_node]
    while queue:
        node = queue.pop(0)
        for fname in downstream.get(node, []):
            path = base / fname
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
            if meta.get("status") != "approved":
                continue
            meta["status"] = "stale"
            path.write_text(join_frontmatter(meta, body), encoding="utf-8")
            marked.append(f"{bundle_dir}/{fname}")
            queue.append(_node_id(fname))
    return marked


def approve_node(
    target_dir: str,
    bundle_dir: str,
    node_id: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None = None,
) -> list[str]:
    """Человеческий approve узла + рекурсивный каскад `stale`; → rel-пути.

    Единственный переход `draft|stale → approved` во всём мосту (§I12):
    доставка права одобрять не имеет ни на одном пути (§I7). Человек
    РЕШАЕТ — выбирает узел; механика СЧИТАЕТ — валидирует узел, проверяет
    одобренность прямых upstream, вычисляет их реальные blob-хеши,
    обновляет пины и `version`, записывает подпись. Решения об одобрении
    механика не принимает, она его РЕГИСТРИРУЕТ; всё, что она умеет
    добавить от себя, — это отказ.

    Пустой список — no-op (§I12 п.4): узел уже честно одобрен, файл не
    переписывается, `version` не растёт, новая подпись не ставится,
    downstream не трогается. Это требование, а не вежливость: подпишись
    повтор заново, сменились бы байты узла, его downstream уехал бы в
    `stale`, и каждый лишний вызов плодил бы долг на ровном месте.

    `approved_by`/`approved_at` приходят аргументами, но НЕ произвольные:
    вызывающий (`approve_node_for_run`) берёт логин у форджи и время —
    в момент вызова (§I12). Здесь они — данные акта, который уже
    совершён.
    """
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    if not _approvable(target_dir, bundle_dir, dag, node_id):
        return []
    files = _dag_files(dag)
    base = Path(target_dir) / bundle_dir
    path = base / files[node_id]
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    upstream_ids = next(u for f, u in dag if _node_id(f) == node_id)
    meta["status"] = "approved"
    # Переход в `approved` и есть новое поколение одобрения документа —
    # отсюда инкремент. Открытый вопрос отменённой редакции §I7 («растёт
    # ли version на переходе вне signed_nodes») закрыт исчезновением
    # клетки: такого перехода механика не совершает вовсе.
    meta["version"] = int(meta.get("version") or 1) + 1
    meta["approved_by"] = approved_by
    meta["approved_at"] = approved_at
    if upstream_ids:
        pins = meta.get("upstream_hashes")
        pins = dict(pins) if isinstance(pins, dict) else {}
        for upstream_id in upstream_ids:
            pins[upstream_id] = blob_sha1(
                (base / files[upstream_id]).read_text(encoding="utf-8")
            )
        meta["upstream_hashes"] = pins
    path.write_text(join_frontmatter(meta, body), encoding="utf-8")
    return [
        f"{bundle_dir}/{files[node_id]}",
        *_cascade_stale(base, bundle_dir, dag, node_id),
    ]


def _human_login(ops: Ops) -> str:
    """Логин человека, совершающего approve; иначе — отказ (§I12).

    Свободный аргумент позволил бы подписать чужим именем, и ложь была бы
    неотличима от правды: в артефакте осталась бы валидная строка, а
    проверить её потом нечем. Логин — факт, установленный форджей о том,
    КТО вызвал команду.

    Профильный guard отказывает в двух случаях, и оба — «подписывать
    нечем либо нечестно»:

    - логин не разрешается или пуст (нет авторизации, сеть, пустой
      ответ) — выдумать подпись неоткуда;
    - активный профиль ревью-контурный. Опознаётся по ДВУМ фактам одной
      и той же учётки — `GH_CONFIG_DIR`, указывающему на её профиль, и
      самому логину: спека называет оба (`~/.config/review`, логин
      `REVIEW_LOGIN`), и достаточно любого, потому что переопределённый
      `REVIEW_LOGIN` рассогласовал бы проверку по одному лишь имени. Эта
      учётка существует, чтобы публиковать АГЕНТСКИЕ вердикты; подпись
      узла от её имени была бы в артефакте неотличима от человеческой и
      уничтожила бы различитель agent/human (ADR-ECO-011) ровно так же,
      как голый `gh pr merge` обнуляет `merged_by`. Правило — зеркало
      того, что действует на мерже: там агентский профиль обязателен,
      здесь запрещён.

    Принятый остаток (§I12, та же незакрытая развилка, что в §I10):
    прочие агентские учётки командой не опознаются — список агентских
    логинов есть явная конфигурация прогона, по умолчанию в нём только
    ревью-контурный.
    """
    config_dir = os.environ.get("GH_CONFIG_DIR")
    if config_dir and Path(config_dir).expanduser() == REVIEW_GH_CONFIG_DIR:
        raise RuntimeError(
            f"активный профиль gh — ревью-контурный ({config_dir}): approve "
            "узла есть ЧЕЛОВЕЧЕСКИЙ акт, а подпись от агентской учётки "
            "неотличима в артефакте от человеческой. Запустите команду от "
            "своего профиля (без GH_CONFIG_DIR)"
        )
    login = ops.gh_login()
    if not login:
        raise RuntimeError(
            "активный GitHub-логин не разрешается (нет авторизации, сеть "
            "либо пустой ответ) — подписывать нечем, а выдумать подпись "
            "неоткуда; проверьте `gh auth status`"
        )
    if login == review_login():
        raise RuntimeError(
            f"активный GitHub-логин — {login!r}, учётка ревью-контура: "
            "approve узла есть ЧЕЛОВЕЧЕСКИЙ акт, и подпись от неё "
            "уничтожила бы различитель agent/human. Запустите команду от "
            "своего профиля"
        )
    return login


def _approve_branch(ws_id: str) -> str:
    """Накапливающая ветка волны одобрения — ОДНА на весь активный DAG.

    Не путать с `spec/<ws-id>-tasks-approve` (`--conform-approve`): тот
    нормализует frontmatter TASKS-СПЕКИ после `spec approve` владельца,
    здесь предмет другой — узлы бандла.
    """
    return f"spec/{ws_id}-bundle-approve"


def _wave_pr(state: RunState, ops: Ops, branch: str) -> int:
    """Draft-PR волны: первый вызов создаёт, следующие возвращают тот же.

    Второй PR не создаётся никогда — поиск идёт по ОТКРЫТЫМ PR ветки
    (`any_state` не годится: вмерженный PR прошлой волны означает, что та
    волна доехала в base, и новая волна той же ветки обязана получить
    свой PR).

    PR остаётся draft, пока активный DAG одобрен не целиком; перевод в
    ready и мерж — человеческие (§I9), и довод там сильнее обычного: PR
    несёт зафиксированный человеческий акт одобрения, и мержить его от
    агентской учётки значило бы дописать к решению человека агентский шаг
    ровно там, где различитель agent/human и живёт.
    """
    existing = ops.find_pr(state.repo_slug, branch)
    if existing is not None:
        return existing
    return ops.create_draft_pr(
        state.target_dir,
        state.repo_slug,
        branch,
        f"spec: {state.ws_id} bundle — одобрение узлов (approve)",
        (
            f"Человеческое одобрение узлов бандла {state.ws_id} "
            f"({state.bundle_dir}/), накапливающая ветка `{branch}`.\n\n"
            "Каждый вызов `--approve-node <node-id>` добавляет сюда коммит: "
            "одобренный узел (`status: approved`, подпись = логин "
            "вызвавшего, `approved_at` = время вызова, пины пересчитаны с "
            "фактических блобов upstream) и весь рекурсивный след `stale` "
            "по downstream.\n\n"
            "PR остаётся **draft**, пока активный DAG одобрен не целиком. "
            "Перевод в ready и мерж — человеческие (§I9): PR несёт "
            "зафиксированный акт одобрения, и мерж от агентской учётки "
            "дописал бы к решению человека агентский шаг.\n\n"
            "После мержа доставка (`deliver_for_run` либо `--supersede`) "
            "ТОЛЬКО проверяет одобренность DAG и несёт в своём коммите "
            "ровно tasks-спеку — файлов бандла в нём нет (§I7)."
        ),
        "",
    )


def approve_node_for_run(
    state: RunState,
    ops: Ops,
    node_id: str,
    legacy_bundle: int | None = None,
) -> int | None:
    """`--approve-node` для прогона: акт человека + доставка его веткой.

    Гейт §I12 читает BASE, значит approve обязан там оказаться, а devtools
    пишет в соседние репо только PR-ом. Поэтому доставку одобрения
    выполняет сама команда: одна накапливающая ветка на волну, draft-PR,
    коммит на каждый вызов.

    **Каждый вызов читает ГОЛОВУ накапливающей ветки, а не base**, и это
    не удобство, а условие сходимости волны. Волна идёт по DAG сверху вниз
    несколькими вызовами, и всё, что сделал предыдущий вызов, — свежая
    подпись upstream и долг, объявленный каскадом, — лежит в ветке: мержа
    ещё не было. Читай второй вызов один base, топологическая готовность
    не была бы предъявлена ни для одного узла ниже первого, а одобрял бы
    он поверх устаревшего дерева. Волна не сошлась бы никогда, причём
    молча — каждый вызов в отдельности выглядел бы законным.

    Возврат — номер draft-PR волны либо `None`, когда писать было нечего
    (узел уже честно одобрен и волны нет).
    """
    dag = _dag_for(legacy_bundle)
    if ops.is_dirty(state.target_dir):
        raise RuntimeError(
            f"target_dir {state.target_dir!r} грязный — approve не начат"
        )
    # Профильный guard — ДО первой записи и до синхронизации дерева:
    # подписывать нечем, значит и начинать нечего (§I12 п.6).
    approved_by = _human_login(ops)
    approved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    base_ref = state.base_ref or "master"
    ops.checkout_and_pull(state.target_dir, base_ref)
    base_sha = ops.rev_parse(state.target_dir, "HEAD")
    if base_sha is None:
        raise RuntimeError(
            f"rev_parse HEAD в {state.target_dir!r} не дал SHA — база "
            "неизвестна, approve не начат"
        )
    branch = _approve_branch(state.ws_id)
    wave = ops.fetch_branch(state.target_dir, branch)
    if wave:
        ops.switch_to(state.target_dir, branch, "FETCH_HEAD")
    _check_bundle_composition(state.target_dir, state.bundle_dir, dag)
    # Валидация — ДО создания ветки: отказ не оставляет ни файла узла, ни
    # статусов downstream, ни ветки (§I12 п.6).
    if not _approvable(state.target_dir, state.bundle_dir, dag, node_id):
        print(
            f"узел {node_id} уже честно одобрен — no-op: файл не "
            "переписан, version не вырос, новая подпись не поставлена"
        )
        # Волна уже идёт (крэш между push и созданием PR) — довести хвост
        # надо и здесь, иначе PR не появится никогда.
        return _wave_pr(state, ops, branch) if wave else None
    if not wave:
        ops.switch_to(state.target_dir, branch, base_sha)
    changed = approve_node(
        state.target_dir, state.bundle_dir, node_id,
        approved_by, approved_at, legacy_bundle=legacy_bundle,
    )
    # Коммитом уходят ВСЕ файлы, которые вызов изменил: одобренный узел и
    # весь рекурсивный след каскада. Частичный коммит оставил бы в ветке
    # `approved`-узел с ложным пином — состояние, которое каскад и обязан
    # не допускать.
    ops.commit_paths(
        state.target_dir,
        changed,
        f"spec: {state.ws_id} bundle — approve узла {node_id} "
        f"({approved_by}) + каскад stale",
    )
    ops.push_branch(state.target_dir, branch)
    pr = _wave_pr(state, ops, branch)
    cascaded = changed[1:]
    print(
        f"узел {node_id} одобрен ({approved_by}); "
        + (
            f"каскад stale: {', '.join(cascaded)}"
            if cascaded else "downstream трогать не пришлось"
        )
        + f"; PR волны #{pr} ({branch})"
    )
    remaining = _approval_findings(state.target_dir, state.bundle_dir, dag)
    if remaining:
        print(
            "волна не завершена — активный DAG одобрен не целиком, PR "
            "остаётся draft:\n"
            + "\n".join(f"- {finding}" for finding in remaining)
        )
    else:
        print(
            f"активный DAG одобрен целиком — PR #{pr} готов; перевод в "
            "ready и мерж человеческие (§I9), после мержа — доставка"
        )
    return pr


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


# --- Перенос состояния исполнения в переиздание (§I11) --------------------
#
# Формат состояния ЗАПИНОВАН по парсеру spec-runner
# (`src/spec_runner/task.py`) — не по нашему рендеру: писать эти байты
# будет он, а не мы.
#   TASK_HEADER: `^### (<ID>): (.+)$`, где ID — `[A-Z][A-Z0-9]*-\d+`.
#   TASK_META: `(🔴|🟠|🟡|🟢)? (P\d) \| (⬜|🔄|🔍|✅|⏸️)? <STATUS>` — ПЕРВАЯ
#     подходящая строка после заголовка (сканирование до следующего
#     заголовка). Наш рендер пишет плоское `P2 | TODO   Est: 0.5d`, а
#     `update_task_status` на нём делает `re.sub(r"\|\s*(TODO|…)",
#     "| ✅ DONE", …)` — то есть ВСТАВЛЯЕТ эмодзи. Значит нормализовать и
#     переносить надо весь сегмент «эмодзи? + слово», а не одно слово.
#   CHECKLIST_ITEM: `^- \[([ x])\] (.+)$` — ровно колонка 0 и строчная
#     `x`; шире брать нельзя, иначе перенос отметил бы то, что spec-runner
#     за пункт чеклиста не считает вовсе.
_TASK_HEAD_RE = re.compile(r"^### [A-Z][A-Z0-9]*-\d+: ")
_TASK_META_RE = re.compile(
    r"^(?:(?:🔴|🟠|🟡|🟢)\s+)?P\d\s*\|\s*"
    r"(?P<status>(?:(?:⬜|🔄|🔍|✅|⏸️)\s+)?"
    r"(?:TODO|IN_PROGRESS|REVIEW|DONE|BLOCKED))\b",
    re.IGNORECASE,
)
_CHECKLIST_RE = re.compile(r"^(- \[)(?P<mark>[ x])(\] )(?P<text>.+)$")
# Вид, который даёт рендер: к нему нормализуются оба маркера состояния на
# время побайтовой сверки блоков.
_CANON_STATUS = "TODO"


class _DeliveredState(NamedTuple):
    """Состояние исполнения ДОСТАВЛЕННОЙ tasks-спеки (§I11).

    `status_by_block` — state-free блок задачи → сегмент статуса
    мета-строки целиком (`✅ DONE`); `checked` — тексты отмеченных пунктов
    чеклиста. В обоих словарях остаётся только ОДНОЗНАЧНОЕ: блок или
    текст, встретившийся больше одного раза, не переносит ничего.
    """

    status_by_block: dict[str, str]
    checked: frozenset[str]


def _task_bounds(lines: list[str]) -> list[tuple[int, int]]:
    """Границы блоков задач: от `### TASK-NNN:` до следующего заголовка."""
    heads = [i for i, ln in enumerate(lines) if _TASK_HEAD_RE.match(ln)]
    return [
        (start, heads[k + 1] if k + 1 < len(heads) else len(lines))
        for k, start in enumerate(heads)
    ]


def _task_meta(body: list[str]) -> tuple[int, re.Match[str]] | None:
    """Мета-строка задачи (индекс + матч) по канону spec-runner.

    ПЕРВАЯ подходящая строка после заголовка; заголовок (индекс 0) сам
    под `TASK_META` не подходит, но пропускается явно — так же, как это
    делает `update_task_status`.
    """
    for i, line in enumerate(body[1:], start=1):
        m = _TASK_META_RE.match(line)
        if m:
            return i, m
    return None


def _state_free(body: list[str]) -> str:
    """State-free представление блока задачи — предмет сверки §I11.

    Оба маркера состояния НОРМАЛИЗУЮТСЯ к виду рендера (`TODO`, `- [ ]`),
    а не вырезаются: выбросить мета-строку целиком значило бы ослепить
    сравнение к смене приоритета и оценки, живущих в той же строке.
    Заголовок `### TASK-NNN: <title>` в представление ВХОДИТ — из этого и
    следует единственность кандидата (номера задач в спеке различны).
    """
    out = list(body)
    found = _task_meta(out)
    if found is not None:
        i, m = found
        out[i] = (
            out[i][: m.start("status")]
            + _CANON_STATUS
            + out[i][m.end("status"):]
        )
    for i, line in enumerate(out):
        c = _CHECKLIST_RE.match(line)
        if c:
            out[i] = f"{c.group(1)} {c.group(3)}{c.group('text')}"
    return "\n".join(out)


def _checklist_texts(lines: list[str]) -> list[str]:
    """Тексты пунктов чеклиста спеки — только ВНУТРИ блоков задач.

    Та же граница, что у spec-runner (`update_checklist_item` считает
    пункты, лишь находясь внутри задачи): `- [ ]` в секции решений design
    пунктом чеклиста не является и в перенос не участвует.
    """
    return [
        c.group("text")
        for start, stop in _task_bounds(lines)
        for c in (_CHECKLIST_RE.match(ln) for ln in lines[start:stop])
        if c is not None
    ]


def _unique(values: list[str]) -> set[str]:
    """Значения, встретившиеся РОВНО один раз."""
    seen: dict[str, int] = {}
    for v in values:
        seen[v] = seen.get(v, 0) + 1
    return {v for v, n in seen.items() if n == 1}


def _delivered_state(delivered: str) -> _DeliveredState:
    """Состояние исполнения доставленной спеки (§I11).

    Единственность — УСЛОВИЕ переноса, а не свойство сегодняшних данных:
    и блок, и текст пункта, встретившиеся дважды, выбрасываются целиком.
    Двусмысленность разрешается чистым результатом, а не догадкой — иначе
    перенос рискнул бы объявить выполненной чужую работу.
    """
    lines = delivered.split("\n")
    bodies = [lines[start:stop] for start, stop in _task_bounds(lines)]
    blocks = [_state_free(b) for b in bodies]
    single = _unique(blocks)
    status_by_block = {}
    for block, body in zip(blocks, bodies):
        if block not in single:
            continue
        found = _task_meta(body)
        status_by_block[block] = (
            found[1].group("status") if found else _CANON_STATUS
        )
    marked = {
        c.group("text")
        for start, stop in _task_bounds(lines)
        for c in (_CHECKLIST_RE.match(ln) for ln in lines[start:stop])
        if c is not None and c.group("mark") != " "
    }
    return _DeliveredState(
        status_by_block, frozenset(marked & _unique(_checklist_texts(lines)))
    )


def _carry_execution_state(text: str, delivered: str) -> str:
    """Свежий рендер спеки + состояние исполнения из доставленной спеки.

    Правило §I11: **состояние переносится там, где содержание не
    изменилось**. Кандидат в базовой спеке ищется как блок с тем же
    state-free содержимым — сопоставление идёт ПО БАЙТАМ, а не по
    отдельному ключу. Стабильный ключ в теле есть (`Source: …#<DT-id>`),
    но взять его значило бы завести ВТОРУЮ идентичность задачи рядом с
    байтами, и расходились бы они ровно там, где перенумеровался
    `**Depends on:**`, — вместо одного правила стало бы два, дающих разные
    ответы на один вопрос.

    Отметка пункта переносится независимо от статуса объемлющей задачи:
    задача, у которой переформулирован один пункт, не теряет отметок
    остальных. Условие — единственность текста пункта И в базовой спеке,
    И в новом рендере.

    Перенос ЧИСТЫЙ: результат — функция двух текстов, длина блоков не
    меняется. Значит повторный заход по тому же намерению (тот же base,
    тот же рендер) даёт те же байты, и `tasks_blob` §I3 воспроизводится.
    """
    prev = _delivered_state(delivered)
    if not prev.status_by_block and not prev.checked:
        return text
    lines = text.split("\n")
    carryable = prev.checked & _unique(_checklist_texts(lines))
    out = list(lines)
    for start, stop in _task_bounds(lines):
        body = list(lines[start:stop])
        was = prev.status_by_block.get(_state_free(body))
        found = _task_meta(body)
        if was is not None and found is not None:
            i, m = found
            body[i] = (
                body[i][: m.start("status")] + was + body[i][m.end("status"):]
            )
        for i, line in enumerate(body):
            c = _CHECKLIST_RE.match(line)
            if c is not None and c.group("text") in carryable:
                body[i] = f"{c.group(1)}x{c.group(3)}{c.group('text')}"
        out[start:stop] = body
    return "\n".join(out)


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
    branch: str | None = None,
    version: int = 1,
    restamp_nodes: frozenset[str] | None = None,
    carry_from: str | None = None,
    before_commit: Callable[[dict], None] | None = None,
    after_commit: Callable[[dict], None] | None = None,
) -> int:
    """Штампует бандл + пишет spec/<ws-id>-tasks.md; один draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.

    Порядок «штамп бандла → пин анкера → рендер tasks» жёсткий: штамп
    меняет байты терминального узла активного DAG, и пин, взятый до
    штампа, протух бы в том же PR (@id:spec-bridge-approve-conformance).

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

    `branch`/`version` (Task 7 плана supersede): `deliver_superseded`
    доставляет переиздание в СВОЮ ветку `spec/<ws-id>-tasks-v<N>` с
    возросшим `version:` во frontmatter — обычная доставка (`deliver_for_run`)
    не передаёт ни того, ни другого, и получает прежние дефолты
    (`spec/<ws-id>-tasks`, `version: 1`).

    `restamp_nodes` (§I7) — тоже только у переиздания: множество node-id,
    которые менял correction-PR; на уже approved-узле ИЗ ЭТОГО МНОЖЕСТВА
    подпись перезаписывается его фактами, остальные узлы сохраняют
    исходный провенанс (полное обоснование — докстринг
    `stamp_bundle_approved`). `deliver_for_run` аргумента не передаёт, и
    весь обычный путь — файлы, ветка, PR, содержимое штампа — прежний.

    `carry_from` — байты УЖЕ ДОСТАВЛЕННОЙ tasks-спеки (из base по
    `base_sha` намерения), из которых переиздание переносит состояние
    исполнения: `_carry_execution_state`. Рендер детерминирован по бандлу
    и о предыдущей доставке не знает ничего, поэтому без этого аргумента
    переиздание воркстрима, где часть задач уже выполнена, возвращало все
    `✅ DONE` в `TODO`, а все `- [x]` — в `- [ ]` (живой дефект
    spec-runner#409): state DB помнит успешные задачи, файл больше нет, и
    следующий прогон встаёт на `state_spec_mismatch`. `deliver_for_run`
    аргумента не передаёт (спеки в base ещё нет — переносить нечего), и
    обычная доставка байт-в-байт прежняя.

    `before_commit`/`after_commit` (Task 7b плана supersede) — два хука
    переиздания, намеренно РАЗДЕЛЁННЫЕ коммитом:

    - `before_commit` вызывается сразу после записи файла спеки и ДО
      `commit_paths` с `tasks_blob` — ожидаемым блобом спеки. Записать
      его вместе с `head_sha` (после коммита) нельзя: тогда состояние
      «коммит есть, `head_sha: null`» всегда приходит и с
      `tasks_blob: null`, и строка §I3.1 «`null` + подходящий коммит →
      ПРИНЯТЬ его» недостижима — восстановлению не с чем сравнивать блоб.
    - `after_commit` вызывается РОВНО между `commit_paths` и
      `push_branch` с `head_sha` и `anchor_blob` (фактический штамп
      терминального узла, тот же, что ушёл в пин спеки). §I3 требует
      durable-записи `head_sha` до push: падение между коммитом и push
      иначе оставляет ревизию без единственного факта, по которому её
      опознают в ветке.

    Отказ внутри любого из хуков прерывает доставку ДО push — это
    корректное состояние, его разбирает реконсиляция. `None` у обоих
    (дефолт) — поведение обычной доставки байт-в-байт прежнее.
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
        # verifies: существование пути на диске НЕ проверяется здесь
        # (round 11 ревью PR #161 — откат ошибочного round-10 решения:
        # владелец файла из verifies, по конструкции этого инварианта
        # (graph_findings, closure-проверка), создаёт файл ПОСЛЕ доставки
        # tasks-спеки — своей задачей в исполнении, а не до неё; проверка
        # существования на момент deliver() ломала бы доставку ЛЮБОГО
        # бандла, где verify-DT наблюдает файл более поздней задачи).
        # Существование файла В МОМЕНТ ПРОГОНА — забота spec-runner
        # (spec-runner#402), не гейта доставки.
        #
        # ЧЕСТНАЯ формулировка того, что graph_findings реально
        # гарантирует про verifies (round 11 ревью PR #161, минор —
        # предыдущая версия этого комментария заявляла более сильную
        # гарантию, которой нет): FATAL — ЕСЛИ у пути есть владелец
        # (checked_by), он обязан быть в транзитивном замыкании depends_on
        # наблюдающей задачи (round 9). Путь БЕЗ ВЛАДЕЛЬЦА ВООБЩЕ (опечатка
        # либо осиротевший путь) — НЕ fatal здесь: это отдельная non-fatal
        # находка формы (`non_fatal_findings`, потребитель — только
        # S4-гейт `runner._step_gate`, не `deliver()`) — такой бандл
        # доставляется, а осиротевший путь молча уезжает в **Verifies:**.
    branch = branch or f"spec/{ws_id}-tasks"
    ops.ensure_branch(target_dir, branch)
    stamped = stamp_bundle_approved(
        target_dir, bundle_dir, approved_by, approved_at,
        legacy_bundle=legacy_bundle,
        restamp_nodes=restamp_nodes,
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
    if carry_from is not None:
        # Состояние исполнения переносится ПОСЛЕ рендера и ДО записи:
        # `tasks_blob` (§I3.1) обязан считаться по ФАКТИЧЕСКИМ байтам
        # файла, иначе возобновление не опознало бы собственный коммит.
        text = _carry_execution_state(text, carry_from)
    rel = f"spec/{ws_id}-tasks.md"
    out = Path(target_dir) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if before_commit is not None:
        # ДО коммита (§I3.1): ожидаемый блоб спеки обязан быть в намерении
        # раньше, чем появится коммит, — иначе падение между коммитом и
        # записью head_sha оставляет ревизию неопознаваемой.
        before_commit({"tasks_blob": blob_sha1(text)})
    ops.commit_paths(
        target_dir,
        [*stamped, rel],
        f"spec: {ws_id} tasks (draft) + штамп статусов бандла (fleet-agent)",
    )
    if after_commit is not None:
        # Между коммитом и push (§I3): падение здесь оставляет коммит
        # опознаваемым. `anchor_blob` — ФАКТИЧЕСКИЙ штамп терминального
        # узла, тот же, что ушёл в пин спеки.
        after_commit({
            "head_sha": ops.rev_parse(target_dir, "HEAD"),
            "anchor_blob": design_blob,
        })
    ops.push_branch(target_dir, branch)
    body = (
        f"Draft tasks.md-спека из behaviour-spec бандла {ws_id} "
        f"({bundle_dir}/15-behaviour-spec.md), сгенерирована task_bridge.\n\n"
        + (
            "Этим же PR — штамп статусов вмерженного бандла "
            f"({len(stamped)} файл(а): approved_by = mergedBy "
            # Источник подписи разный (§I7): у обычной доставки — бандл-PR,
            # у переиздания — correction-PR. Одна формулировка на оба пути
            # называла бы оператору не тот PR ровно там, где провенанс и
            # есть предмет.
            + ("correction-PR" if restamp_nodes is not None else "бандл-PR")
            + ", "
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


def _delivered_anchor(
    state: RunState,
    ops: Ops,
    facts: dict,
    legacy_bundle: int | None,
) -> str | None:
    """Anchor уже доставленного PR — blob анкера в его head-коммите (§I2).

    Реконсиляция принимает доставку, которую этот вызов НЕ делал: штамп
    терминального узла уже состоялся и лежит в коммите PR-а, а те же
    байты после мержа лягут в base. Значит anchor не надо пересчитывать —
    его надо ПРОЧИТАТЬ там, где он существует.

    Пересчитать проспективный штамп здесь было бы и невозможно (на этом
    пути нет `approved_by`/`approved_at`, а поднимать их вычисление выше
    значит добавить отказ «у PR нет mergedBy/mergedAt» туда, где сегодня
    доставка успешно реконсилируется), и НЕВЕРНО: считался бы ТЕКУЩИЙ
    апстрим, который у более ранней доставки мог уже уехать вперёд
    доставленного, — и §I5 объявил бы «апстрим не менялся» ложно, то есть
    стал бы fail-open ровно в том гейте, ради которого anchor вводится.

    `None` — факт недоступен (PR без `headRefOid`; объект не подтянут в
    локальный клон): доставка НЕ падает, переиздание уйдёт §6-путём
    (`comparison: unavailable`) — как и до появления записи anchor'а.
    """
    head = facts.get("headRefOid")
    if not head:
        return None
    dag = _dag_for(legacy_bundle)
    return ops.blob_in_commit(
        state.target_dir, head, f"{state.bundle_dir}/{dag[-1][0]}"
    )


def _delivered_content_anchor(
    state: RunState,
    ops: Ops,
    facts: dict,
    legacy_bundle: int | None,
) -> str | None:
    """`content_anchor` уже доставленного PR — из байтов его head-коммита.

    Тот же довод, что у `_delivered_anchor`: реконсиляция принимает
    доставку, которую этот вызов не делал, поэтому содержание читается
    там, где оно существует. Текущий base сюда не годится: в нём лежат
    ДОштамповые байты (`status: draft`, прежний `version`), а
    `content_anchor` считается по проштампованному дереву — сверка §I5
    разошлась бы с первым же переизданием.

    Читается СОДЕРЖИМОЕ (`ops.show_file`), а не blob-хеши: канонизация
    работает с текстом frontmatter, хеша ей мало. Повторный проспективный
    штамп поверх уже проштампованных байтов ничего не меняет (статус
    approved, `version` не растёт, подпись канонизация вырезает) —
    значение то же, что записала бы сама доставка.

    `None` — факт недоступен (нет `headRefOid`, объект не подтянут в
    клон, frontmatter не разобрать): переиздание уйдёт §6-путём
    (`comparison: unavailable`), как и без записи вовсе. Тот же
    сознательный fail-open, что у `_delivered_anchor`.
    """
    head = facts.get("headRefOid")
    if not head:
        return None
    dag = _dag_for(legacy_bundle)
    with tempfile.TemporaryDirectory(prefix="delivered-content-") as tmp:
        shadow = Path(tmp) / "target"
        (shadow / state.bundle_dir).mkdir(parents=True)
        for fname, _ in dag:
            text = ops.show_file(
                state.target_dir, head, f"{state.bundle_dir}/{fname}"
            )
            if text is None:
                return None
            (shadow / state.bundle_dir / fname).write_text(
                text, encoding="utf-8"
            )
        try:
            return _content_anchor(
                str(shadow), state.bundle_dir, legacy_bundle
            )
        except ValueError:
            return None


def deliver_for_run(
    state: RunState,
    ops: Ops,
    legacy_bundle: int | None = None,
) -> int:
    """Идемпотентная доставка tasks-спеки для прогона (кнопка spec-loop).

    Durable reconciliation поверх неидемпотентного `deliver()` (дизайн-
    решение владельца 2026-09-07): op ``tasks-deliver`` в `run.json`
    ведётся write-ahead (`started` ДО эффектов), а перед любой доставкой
    ищется уже созданный PR по ветке ``spec/<ws-id>-tasks`` — повтор
    НИКОГДА не создаёт PR заново, в каком бы состоянии op ни застал
    прогон (new/started/completed).

    Оба завершения op пишут ДВА поля, отвечающих на разные вопросы:

    - `anchor` — точный blob терминального узла после штампа (§I2):
      идентичность доставленных байтов;
    - `content_anchor` — канонический signature-free хэш DAG (§I5):
      менялось ли СОДЕРЖАНИЕ апстрима. Без него `prev_op` у v1 всегда без
      сверки, и ПЕРВОЕ переиздание любого воркстрима уходит в
      compatibility-случай §6: `--supersede` сразу после обычной доставки
      завёл бы v2 с новой веткой и PR-ом вместо бесследного no-op.

    Источник значений у двух завершений разный — новая доставка знает их
    изнутри (`after_commit`, из того же состояния дерева, что ушло в
    коммит), реконсиляция читает доставленные байты из head-коммита PR-а
    (`_delivered_anchor` / `_delivered_content_anchor`).

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
        print(
            f"tasks-спека уже доставлена: PR #{pr_done} "
            f"({state.repo_slug}) — повтор не создаёт PR"
        )
        return pr_done
    # Поиск PR по ветке ВО ВСЕХ состояниях (major терм. ревью #156):
    # отсутствие ОТКРЫТОГО PR не значит «доставки не было» — спека могла
    # быть доставлена ранее, вмержена и переведена в approved; повторный
    # deliver() перегенерировал бы её обратно в draft вторым PR-ом.
    branch = f"spec/{state.ws_id}-tasks"
    existing = ops.find_pr(state.repo_slug, branch, any_state=True)
    if existing is not None:
        # Факты PR берутся ЦЕЛИКОМ (один и тот же запрос, что и раньше):
        # кроме `state` из них нужен `headRefOid` — по нему читается
        # доставленный anchor (`_delivered_anchor`).
        existing_facts = ops.pr_facts(state.repo_slug, existing)
        pr_state = existing_facts.get("state")
        if pr_state not in ("OPEN", "MERGED"):
            raise RuntimeError(
                f"PR #{existing} по ветке {branch} закрыт без мержа "
                f"(state={pr_state!r}) — реконсиляция fail-closed: "
                "решите судьбу ветки/PR вручную, повторная доставка "
                "поверх отклонённой не выполняется"
            )
        op_complete(
            state, "tasks-deliver", pr=existing,
            anchor=_delivered_anchor(
                state, ops, existing_facts, legacy_bundle
            ),
            content_anchor=_delivered_content_anchor(
                state, ops, existing_facts, legacy_bundle
            ),
        )
        print(
            f"найден существующий PR #{existing} по ветке {branch} "
            f"({pr_state}) — принят как доставка, новый не создаётся"
        )
        return existing
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
    stamped: dict[str, str] = {}

    def _capture_anchor(commit_facts: dict) -> None:
        """§I2 + §I5: идентичность и содержание тех байтов, что ушли в PR.

        Хук — единственный момент, когда значения верны: до `deliver()`
        рабочее дерево ещё не синхронизировано с `base_ref`
        (`checkout_and_pull` живёт ВНУТРИ доставки), а после неё дерево
        уже на ветке ревизии. Оба значения берутся из ОДНОГО состояния
        дерева — того, что лежит в коммите.

        `content_anchor` (§I5) считается здесь же, а не после `deliver()`:
        отказ на этом шаге останавливает доставку ДО push, то есть без PR,
        который потом нечем было бы объяснить. На диск колбэк по-прежнему
        ничего не пишет (`_content_anchor` работает в теневом каталоге).
        """
        stamped["anchor"] = commit_facts["anchor_blob"]
        stamped["content_anchor"] = _content_anchor(
            state.target_dir, state.bundle_dir, legacy_bundle
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
        after_commit=_capture_anchor,
    )
    op_complete(
        state, "tasks-deliver", pr=pr, anchor=stamped.get("anchor"),
        content_anchor=stamped.get("content_anchor"),
    )
    return pr


#: Ключ ревизии переиздания. v1 живёт под историческим `tasks-deliver`
#: (§I4 спеки): переименовать её значило бы переписать журнал.
_REVISION_PREFIX = "tasks-deliver-v"
_V1_KEY = "tasks-deliver"


def _revisions(state: RunState) -> list[tuple[int, dict]]:
    """(N, op) всех ревизий переиздания по возрастанию N."""
    found: list[tuple[int, dict]] = []
    for key, op in state.ops.items():
        if not key.startswith(_REVISION_PREFIX):
            continue
        suffix = key[len(_REVISION_PREFIX):]
        if suffix.isdigit():
            found.append((int(suffix), op))
    return sorted(found)


def _discharged_replacements(state: RunState, ops: Ops) -> frozenset[int]:
    """Номера отозванных PR, чей отзыв потерял силу: они оказались MERGED.

    Отзыв адресован ПРЕДЛОЖЕНИЮ. Стоит человеку смержить отозванный PR,
    пока замена не доведена, — отзывать становится нечего: байты в base,
    и снимать со стола нечего. Обязательство при этом обязано
    РАЗРЯЖАТЬСЯ, а не переноситься дальше: иначе каждая следующая ревизия
    перенимает невыполнимое требование, упирается в безусловный запрет
    «вмерженное не заменяется» и падает RC 1 — воркстрим не переиздать
    больше ничем (major ревью d8f83f0).

    Лекарь для этого исхода спека называет сама (§I10): вмерженная
    дефектная доставка чинится обычным переизданием, которое ничего не
    отзывает, а добавляет.

    Цена ограничена ровно теми прогонами, где отзыв в леджере ЕСТЬ: нет
    записей с `replaces_pr` — сети не касаемся вовсе. Обычное
    переиздание и бесследный no-op §I5 не платят ничего, а §I5 остаётся
    бесследным и подавно: это чтение, `run.json` оно не трогает.
    """
    revoked = {
        op["replaces_pr"] for _, op in _revisions(state)
        if isinstance(op.get("replaces_pr"), int)
    }
    if not revoked:
        return frozenset()
    return frozenset(
        pr for pr in sorted(revoked)
        if ops.pr_facts(state.repo_slug, pr).get("state") == "MERGED"
    )


def _replaced_revisions(
    state: RunState, discharged: frozenset[int] = frozenset()
) -> set[int]:
    """Ревизии, которые более поздняя запись объявила отозванными (§I10).

    Связь направлена ВПЕРЁД, как и `supersedes`: запись N не мутируется
    (§I4), о снятии её предложения говорит запись M > N полем
    `replaces_revision: N`. Читать состояние отозванной ревизии из неё
    самой было бы мутацией журнала.

    `discharged` — номера PR, чей отзыв потерял силу
    (`_discharged_replacements`). Ревизия с таким PR отозванной НЕ
    считается: её байты вмержены, значит доставка состоялась, и
    `supersedes` следующего переиздания обязан указывать именно на неё.
    """
    return {
        op["replaces_revision"] for m, op in _revisions(state)
        if isinstance(op.get("replaces_revision"), int)
        and op["replaces_revision"] < m
        and op.get("replaces_pr") not in discharged
    }


def _replaced_by(state: RunState, n: int, pr: int | None) -> int | None:
    """Ревизия, объявившая замену ревизии `n` ВМЕСТЕ с её PR; None — нет.

    Сверяется ПАРА (ревизия, номер PR): forward-ссылка объявляет
    ожидаемым закрытие именно того предложения, которое заменяли. Под
    именем ветки ревизии мог позже завестись другой PR — его закрытие
    ожидаемым не объявлял никто, и §I3 обязан остаться fail-closed.
    """
    if pr is None:
        return None
    for m, op in _revisions(state):
        if (
            m > n
            and op.get("replaces_revision") == n
            and op.get("replaces_pr") == pr
        ):
            return m
    return None


def _last_delivery(
    state: RunState,
    skip: int | None = None,
    discharged: frozenset[int] = frozenset(),
) -> tuple[int, dict] | None:
    """Последняя ЗАВЕРШЁННАЯ доставка: старшая ревизия либо v1; None — их нет.

    Записи в статусе `abandoned` и `started` пропускаются: ни та, ни
    другая ничего не доставили. `started` — штатный след падения (§I4,
    write-ahead), и принять её за доставку значит сделать §I5 fail-CLOSED
    на пустом месте: намерение ревизии `content_anchor` НЕСЁТ (он
    пишется write-ahead, ДО эффектов), и сверка объявила бы бесследный
    no-op по содержанию, которое НЕ доставлено, — переиздание стало бы
    неремонтируемым. Плюс `supersedes` указывал бы на недоставку.
    Счёт одинаков для ревизий и исторического ключа v1: `deliver_for_run`
    тоже ведёт свой op write-ahead (`op_start` ДО эффектов), так что
    упавшая ПЕРВАЯ доставка оставляет `tasks-deliver` в `started`.

    ЗАМЕНЁННЫЕ ревизии (§I10) пропускаются наравне с ними, хотя статус у
    них `completed`: замена закрывает их PR без мержа, то есть
    спецификация не доставлена. Взять такую за предыдущую доставку значит
    указать `supersedes` на предложение, снятое со стола, и сверять §I5/
    §I8 с содержанием, которого в base нет. `skip` — ревизия, замена
    которой начинается ПРЯМО СЕЙЧАС: forward-ссылки на неё ещё нет, её
    пишет намерение, которое вызывающий только собирает. `discharged` —
    отзывы, потерявшие силу (`_discharged_replacements`): вмерженный PR
    делает ревизию полноценной доставкой, и пропускать её нельзя.
    """
    excluded = _replaced_revisions(state, discharged)
    if skip is not None:
        excluded.add(skip)
    for n, op in reversed(_revisions(state)):
        if n in excluded:
            continue
        if op.get("status") == "completed":
            return n, op
    v1 = state.ops.get(_V1_KEY)
    if 1 in excluded:
        return None
    return (1, v1) if v1 and v1.get("status") == "completed" else None


def _next_revision(state: RunState) -> int:
    """Следующий N: максимум из леджера + 1, минимум 2 (v1 — историческая)."""
    revs = _revisions(state)
    return (revs[-1][0] + 1) if revs else 2


def _start_revision(state: RunState, n: int, intent: dict) -> None:
    """Write-ahead намерения ревизии (§I4): пишется ДО единого эффекта."""
    state.ops[f"{_REVISION_PREFIX}{n}"] = {
        "status": "started", "revision": n, "head_sha": None, **intent,
    }
    save(state)


def _complete_revision(state: RunState, n: int, **result: object) -> None:
    state.ops[f"{_REVISION_PREFIX}{n}"] = {
        **state.ops.get(f"{_REVISION_PREFIX}{n}", {"revision": n}),
        "status": "completed", **result,
    }
    save(state)


#: Терминальные статусы ревизии — и только они (§I3 спеки). Гвард
#: `_abandon_revision` сверяется со ВСЕМ набором: отсекать один
#: `completed` значило бы разрешить повторный `--abandon-revision` по уже
#: брошенной ревизии, а он перезаписал бы её `reason` — единственный след
#: того, почему она брошена (`save` пишет run.json целиком).
_TERMINAL_REVISION_STATUSES = ("completed", "abandoned")


def _abandon_revision(state: RunState, n: int, reason: str) -> None:
    """Терминальный `abandoned` с причиной — причина хранится навсегда.

    §I3/§I4: перевод уже терминальной записи в другое состояние — мутация
    журнала, поэтому отказ идёт на ЛЮБОМ терминальном статусе, не только
    на `completed`.
    """
    key = f"{_REVISION_PREFIX}{n}"
    if key not in state.ops:
        raise RuntimeError(f"ревизии {n} нет в леджере — нечего абандонить")
    op = state.ops[key]
    status = op.get("status")
    if status in _TERMINAL_REVISION_STATUSES:
        detail = (
            f"завершена (PR #{op.get('pr')})"
            if status == "completed"
            else f"уже брошена (причина: {op.get('reason')!r})"
        )
        raise RuntimeError(
            f"ревизия {n} {detail} — терминальная запись не мутируется"
        )
    state.ops[key] = {**op, "status": "abandoned", "reason": reason}
    save(state)


#: Единственная точка настройки allowlist'а ревьюеров, чьё submitted
#: review не блокирует автоматический отзыв предложения (§I10). Дефолт —
#: ПУСТО, и получить непустой случайно нельзя: имя переменной уникально,
#: значение перечисляется поимённо, а пустые элементы отбрасываются.
_REVIEW_ALLOWLIST_ENV = "REPLACEMENT_REVIEW_ALLOWLIST"


def _review_allowlist() -> frozenset[str]:
    """Логины, чьё review не считается вмешательством; по умолчанию — НЕТ.

    Решение владельца 2026-09-09: review — уже созданный аудитный
    артефакт, и агентская учётная запись не делает его находки
    одноразовыми. Привилегий по логину в коде нет ВООБЩЕ — в частности,
    учётка ревью-контура (`ops.review_login()`) здесь не участвует: та
    отвечает на вопрос «чьим именем мы действуем» (закрытие PR, удаление
    веток идут от неё), а не «чьё ревью можно не заметить». Одна учётка,
    две разные роли; путать их нельзя.

    Отсюда и форма: не константа в коде, которую правят коммитом, а
    внешняя конфигурация с пустым дефолтом. Пока её не выставили явно,
    блокирует ЛЮБОЕ submitted review.
    """
    raw = os.environ.get(_REVIEW_ALLOWLIST_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class Replacement(NamedTuple):
    """Явный replace-переход: какую ревизию заменяем и почему.

    Собирается из CLI (`--replace-revision` + общий `--reason`) и
    существует ТОЛЬКО на первом заходе: дальше связь живёт в намерении
    новой ревизии (`replaces_*`), и любой повтор — включая голый
    `--supersede` — доводит шаги оттуда.
    """

    revision: int
    reason: str


def _check_replacement_target(
    state: RunState,
    ops: Ops,
    pr: int,
    head_sha: str | None,
    facts: dict,
    pending_revision: int | None = None,
) -> None:
    """Внешнее вмешательство в заменяемый PR → fail-closed (§I10).

    Порядок владельца: замена валидна, пока предложение никем, кроме нас,
    не тронуто. Что считается вмешательством:

    - `MERGED` — безусловный отказ: вмерженное предложение заменять
      нельзя ни при каких условиях, оно уже часть базы;
    - head PR разошёлся с записанным `head_sha` намерения (или его не
      узнать) — под тем же именем ветки чужая работа, закрывать её мы не
      вправе;
    - ЛЮБОЕ submitted review, независимо от автора, — оно уже созданный
      аудитный артефакт, и агентская учётка не делает его находки
      одноразовыми (решение владельца). Исключения — только через
      `_review_allowlist()`, пустой по умолчанию;
    - непогашенный review thread любого автора — разговор, который ждёт
      ответа; `None` («узнать не удалось») читается как непогашенный.

    Граница «блокирует / не блокирует» проведена по СУЩНОСТЯМ форджи, а
    не по автору:

    - `ops.pr_reviews` спрашивает `pulls/<n>/reviews` — эндпоинт отдаёт
      только review, поэтому check runs и обычные issue-комментарии сюда
      структурно не попадают; `statusCheckRollup` не спрашивается вовсе;
    - `ops.unresolved_threads` спрашивает `reviewThreads` — inline-треды
      ревью, а не комментарии PR: бот, оставивший отчёт через
      `gh pr comment`, замену не удерживает;
    - `state == "PENDING"` — черновик, ещё НЕ отправленный автором:
      вердикта в нём нет, и владельческое «submitted review» его не
      покрывает.

    Бот, публикующий отчёт как REVIEW (а не комментарием), замену
    удержит — и это ровно правило владельца, а не его обход.

    `pending_revision` — номер ревизии, чьё намерение УЖЕ записано (шаг
    закрытия на повторе). От него зависит только выход, который называет
    диагностика `MERGED`: до записи намерения оператору нечего сворачивать
    и нужен просто `--supersede`, а с записанным намерением сначала надо
    закрыть повисшую ревизию (`--abandon-revision`) — иначе её
    обязательство будет ходить по кругу.

    Уже закрытый (не вмерженный) PR проверок не проходит и не требует:
    предложение снято, а именно это замена и делает. Это же — ручной
    выход оператора из отказа: закрыть #N руками с объяснением и
    повторить переход.
    """
    pr_state = facts.get("state")
    if pr_state == "MERGED":
        exit_hint = (
            f"сверните ревизию {pending_revision} "
            f"(--abandon-revision {pending_revision} --reason ...), затем "
            "переиздайте обычным --supersede"
            if pending_revision is not None
            else "дефектная вмерженная доставка чинится обычным "
            "--supersede: он ничего не отзывает, а добавляет"
        )
        raise RuntimeError(
            f"PR #{pr} вмержен — вмерженное предложение не отзывается: "
            f"оно уже часть base, и снимать со стола нечего. {exit_hint}"
        )
    if pr_state != "OPEN":
        return
    if not head_sha:
        raise RuntimeError(
            f"замена PR #{pr}: в записи ревизии нет head_sha — "
            "идентичность предложения не проверить, закрывать нельзя"
        )
    actual_head = facts.get("headRefOid")
    if not actual_head:
        raise RuntimeError(
            f"замена PR #{pr}: у него нет headRefOid, а намерение ревизии "
            f"стоит на {head_sha[:7]} — идентичность не проверить"
        )
    if actual_head != head_sha:
        raise RuntimeError(
            f"замена PR #{pr}: он стоит на {actual_head[:7]}, а намерение "
            f"ревизии — на {head_sha[:7]}; в PR вмешались снаружи — "
            "закройте его сами, с объяснением, и повторите замену"
        )
    reviews = ops.pr_reviews(state.repo_slug, pr)
    if reviews is None:
        raise RuntimeError(
            f"замена PR #{pr}: список ревью не получен — «вмешательства "
            "нет» утверждать не из чего, fail-closed"
        )
    allowed = _review_allowlist()
    blocking = sorted({
        str(r.get("login")) for r in reviews
        if r.get("state") != "PENDING" and r.get("login") not in allowed
    })
    if blocking:
        raise RuntimeError(
            f"замена PR #{pr}: на нём есть submitted review "
            f"({', '.join(blocking)}) — это аудитный артефакт, и закрыть "
            "PR вместе с разбором механика не вправе. Закройте PR "
            "вручную, с объяснением ревьюеру, и повторите замену тем же "
            "--replace-revision"
        )
    if ops.unresolved_threads(state.repo_slug, pr) is not False:
        raise RuntimeError(
            f"замена PR #{pr}: есть непогашенные review threads (либо их "
            "состояние не узнать) — разговор ждёт ответа. Погасите треды "
            "либо закройте PR вручную, с объяснением, и повторите замену "
            "тем же --replace-revision"
        )


def _validate_replacement(
    state: RunState, ops: Ops, replace: Replacement
) -> dict:
    """Проверки заменяемой ревизии ДО единого эффекта → поля намерения.

    Порядок владельца начинается именно этим: «validate v3/#408 →
    записать started-v4». Возврат — `replaces_*`-часть намерения новой
    ревизии; связь направлена вперёд, запись заменяемой ревизии не
    трогается (§I4).

    Историческая v1 (`tasks-deliver`) заменяться НЕ может, и это не
    экономия: у её записи нет ни `branch`, ни `head_sha` — ни
    идентичность предложения проверить, ни ветку удалить. Если её PR
    дефектен, воркстрим не доставил ещё ничего, и разбирается это
    обычной доставкой, а не переизданием.
    """
    n = replace.revision
    if n < 2:
        raise RuntimeError(
            "заменять можно только ревизию переиздания (N ≥ 2): у первой "
            "доставки нет ни ветки, ни head_sha — идентичность её "
            "предложения не проверить"
        )
    op = state.ops.get(f"{_REVISION_PREFIX}{n}")
    if op is None:
        raise RuntimeError(f"ревизии {n} нет в леджере — заменять нечего")
    status = op.get("status")
    if status == "abandoned":
        raise RuntimeError(
            f"ревизия {n} брошена (причина: {op.get('reason')!r}) — "
            "отзывать нечего: предложения она не сделала"
        )
    if status != "completed":
        raise RuntimeError(
            f"ревизия {n} в статусе {status!r} — заменяется только "
            "завершённая доставка: замена адресована ПРЕДЛОЖЕНИЮ, а "
            "незавершённая ревизия его ещё не сделала. Её выход — "
            f"--abandon-revision {n}"
        )
    later = [
        m for m, later_op in _revisions(state)
        if m > n and later_op.get("replaces_revision") != n
    ]
    if later:
        # Замена середины истории породила бы ДВЕ конкурирующие цепочки
        # forward-ссылок: у §I3 не осталось бы однозначного ответа, чьё
        # закрытие ожидаемо. Исключение — ревизии, которые эту же замену
        # уже ведут: с ними повтор `--replace-revision N` продолжает
        # начатое, а не начинает вторую цепочку.
        raise RuntimeError(
            f"ревизия {n} не последняя в леджере (после неё: "
            f"{', '.join(str(m) for m in later)}) — заменять середину "
            "истории нельзя; адресуйте замену последней ревизии"
        )
    pr, branch = op.get("pr"), op.get("branch")
    if not isinstance(pr, int) or not branch:
        raise RuntimeError(
            f"ревизия {n}: в записи нет pr/branch (pr={pr!r}, "
            f"branch={branch!r}) — заменять нечего"
        )
    _check_replacement_target(
        state, ops, pr, op.get("head_sha"),
        ops.pr_facts(state.repo_slug, pr),
    )
    return {
        "replaces_revision": n,
        "replaces_pr": pr,
        "replaces_branch": branch,
        # Идентичность заменяемого предложения — durable в НАМЕРЕНИИ, а не
        # вычитываемая из записи ревизии N на каждом шаге: повтор
        # (`_replacement_close`) обязан сверять её тем же значением, каким
        # её утвердила валидация, иначе «в PR вмешались» и «мы читаем
        # другую запись» стали бы неразличимы.
        "replaces_head_sha": op.get("head_sha"),
        "replacement_reason": replace.reason,
    }


def _pending_replacement(
    state: RunState, discharged: frozenset[int] = frozenset()
) -> dict:
    """Незавершённое обязательство замены, которое обязана перенять новая
    ревизия; пустой словарь — переносить нечего.

    Ревизия-замена может сама оказаться `abandoned` (сдвинулся base,
    открытого PR нет). Обязательство при этом остаётся: PR отозванной
    ревизии либо ещё открыт, либо уже закрыт, а её ветка жива — и без
    переноса полей forward-ссылка на неё осталась бы только в брошенной
    записи, а хвост замены не довёлся бы никогда.

    Смотрится ТОЛЬКО новейшая запись с `replaces_*`: если она не брошена,
    обязательство ведёт она сама (и цикл реконсиляции до этого места не
    дошёл бы).

    Обязательство с ВМЕРЖЕННЫМ отозванным PR (`discharged`) не
    переносится: отзывать нечего, байты уже в base. Перенося его, мы
    вручали бы каждой следующей ревизии невыполнимое требование —
    `_replacement_close` упирался бы в безусловный запрет «вмерженное не
    заменяется», прогон падал бы RC 1, а `--abandon-revision` только
    сдвигал бы обязательство на ревизию вперёд. Воркстрим оказывался бы
    заперт навсегда — ровно тот тупик, ради снятия которого §I10 и
    заводился (major ревью d8f83f0).
    """
    for _, op in reversed(_revisions(state)):
        if not isinstance(op.get("replaces_revision"), int):
            continue
        if op.get("status") != "abandoned":
            return {}
        if op.get("replaces_pr") in discharged:
            print(
                f"отзыв PR #{op['replaces_pr']} потерял силу: он вмержен — "
                "обязательство замены снято, дальше идёт обычное "
                "переиздание"
            )
            return {}
        return {
            key: op[key] for key in (
                "replaces_revision", "replaces_pr", "replaces_branch",
                "replaces_head_sha", "replacement_reason",
            ) if key in op
        }
    return {}


def _replacement_close(state: RunState, ops: Ops, op: dict) -> None:
    """Шаг «закрыть заменяемый PR» — идемпотентный, ИЗ НАМЕРЕНИЯ ревизии.

    Читается запись леджера, а не аргументы CLI: любой повтор (в том
    числе голый `--supersede`) обязан продолжать ту же ревизию и довести
    её шаги. Иначе окно «намерение записано, PR ещё открыт» разрешалось
    бы доставкой второго открытого PR на ту же спеку — ровно то, что §I3
    запрещает.

    Факты PR перезапрашиваются, хотя `_validate_replacement` их уже
    смотрел: между валидацией и закрытием стоят сетевые шаги §I7/§I8, и
    человек успевает оставить ревью именно в этом окне.
    """
    pr = op.get("replaces_pr")
    if not isinstance(pr, int):
        return
    facts = ops.pr_facts(state.repo_slug, pr)
    _check_replacement_target(
        state, ops, pr, op.get("replaces_head_sha"), facts,
        pending_revision=op.get("revision"),
    )
    if facts.get("state") != "OPEN":
        return          # уже закрыт: шаг состоялся раньше (или оператором)
    reason = op.get("replacement_reason", "")
    body = (
        f"Закрыт механикой replace-перехода `task_bridge` прогона "
        f"`{state.run_id}`: предложение ревизии "
        f"{op['replaces_revision']} заменяется ревизией "
        f"{op.get('revision', '?')}.\n\n"
        f"Причина: {reason}\n\n"
        "Ревизия-предшественник в леджере не мутируется: этот PR и его "
        "коммиты остаются аудитным следом, удаляется только живая ветка "
        f"`{op.get('replaces_branch')}`."
    )
    if not ops.close_pr(state.repo_slug, pr, body):
        raise RuntimeError(
            f"не удалось закрыть заменяемый PR #{pr} — замена не "
            "продолжается: новый PR завёлся бы вторым открытым на ту же "
            "спеку"
        )
    print(f"заменяемый PR #{pr} закрыт")


def _replacement_cleanup(state: RunState, ops: Ops, op: dict) -> None:
    """Шаг «удалить ветку заменённой ревизии» — последний, идемпотентный.

    Порядок владельца: ветка удаляется ТОЛЬКО после того, как намерение
    durable, заменяемый PR закрыт и новый PR создан. Поэтому зовётся
    исключительно там, где PR новой ревизии уже существует.

    Удаляется ТОЛЬКО ссылка, чей head совпал с записанным
    `replaces_head_sha` — и в каждой половине отдельно. Иначе шаг
    необратимо сносит чужие коммиты: под тем же именем ветки могли
    оказаться дописанные после закрытия PR байты либо одноимённая ветка
    оператора в его клоне, а удаление идёт `git branch -D` (force —
    отозванная ветка не вмержена по построению, `-d` отказал бы всегда) и
    в СОСЕДНЕМ репо. Мотивировка та же, что у head-сверки при закрытии:
    мы не трогаем то, содержимое чего не то, что записывали. Единственный
    путь, на котором сверки к этому моменту не было ни разу, — «оператор
    закрыл PR сам»: там `_check_replacement_target` выходит на
    `pr_state != "OPEN"` до сверки идентичности (major ревью 3c07bb0).

    Расхождение — не отказ: доставка уже состоялась, и RC 1 сказал бы
    оператору неправду о ней. Ветка остаётся жить, диагностика называет
    оба SHA. То же на пустом `replaces_head_sha` (запись старого
    образца): сверять нечем — значит не удаляем.

    Отсутствие ссылки — выполненный шаг, а не сбой: `None` от
    `remote_branch_head` и от `rev_parse` молчат.
    """
    branch = op.get("replaces_branch")
    if not branch:
        return
    head = op.get("replaces_head_sha")
    if not head:
        print(
            f"ветка {branch} оставлена: в намерении нет replaces_head_sha, "
            "идентичность не проверить"
        )
        return
    removed: list[str] = []
    for where, actual, drop in (
        (
            "origin",
            ops.remote_branch_head(state.repo_slug, branch),
            lambda: ops.delete_remote_branch(state.repo_slug, branch),
        ),
        (
            "локально",
            ops.rev_parse(state.target_dir, branch),
            lambda: ops.delete_local_branch(state.target_dir, branch),
        ),
    ):
        if actual is None:
            continue          # ссылки нет — шаг по этой половине состоялся
        if actual != head:
            print(
                f"ветка {branch} ({where}) оставлена: она стоит на "
                f"{actual[:7]}, а отозванная ревизия — на {head[:7]}; под "
                "тем же именем чужая работа"
            )
            continue
        if drop():
            removed.append(where)
    if removed:
        print(
            f"ветка заменённой ревизии удалена ({', '.join(removed)}): "
            f"{branch}"
        )


class Correction(NamedTuple):
    """Разрешённый correction-PR: провенанс + состав затронутых узлов.

    `signed_nodes` — node-id узлов активного DAG, которые этот PR
    действительно менял. §I7 поузловой: подпись получают только они, и
    множество обязано ехать вместе с подписью — иначе вызывающая сторона
    решала бы «кому подписывать» отдельно от «чья подпись», а это два
    ответа на один вопрос.
    """

    pr: int
    approved_by: str
    approved_at: str
    signed_nodes: frozenset[str]


def _ledger_delivery_prs(state: RunState) -> tuple[set[int], list[str]]:
    """Собственные доставки прогона: (номера PR, ветки доставок без номера).

    Первая линия опознания §I7 шага 3 — номера ИЗ ЛЕДЖЕРА: `pr` записей
    `tasks-deliver` (историческая v1) и `tasks-deliver-v<N>`. Номер
    записан НАМИ САМИМИ в момент доставки, поэтому он опознаёт её и после
    переименования ветки, и после её удаления, и независимо от того, кто
    её мержил.

    Вторая половина ответа — ветки тех доставок, чей номер в леджер ещё
    не попал: намерение ревизии пишется write-ahead (§I4), ДО создания
    PR, поэтому `started` номера не несёт. Отсюда же и условие: ветка
    возвращается только у `started`. Терминальные записи (`completed`,
    `abandoned`) без номера ветку не отдают — у `completed` номер есть по
    построению, а брошенная ревизия у GitHub не спрашивается вовсе (её
    PR оператор закрыл, и переиздание это уже решило).

    Резолвить ветку в номер здесь НЕ входит — это сетевой запрос, и
    вызывающая сторона решает, нужен ли он ей (на явном пути
    `--approval-pr` не нужен).
    """
    numbers: set[int] = set()
    branches: list[str] = []
    records: list[tuple[str | None, dict]] = [
        (f"spec/{state.ws_id}-tasks", state.ops.get(_V1_KEY) or {})
    ]
    records += [(op.get("branch"), op) for _, op in _revisions(state)]
    for branch, op in records:
        if not op:
            continue
        pr = op.get("pr")
        if isinstance(pr, int):
            numbers.add(pr)
        elif branch and op.get("status") == "started":
            branches.append(branch)
    return numbers, branches


def _own_delivery_prs(state: RunState, ops: Ops) -> frozenset[int]:
    """Номера собственных delivery-PR прогона — обе линии опознания (§I7.3).

    Ветка спрашивается ТОЛЬКО у доставок без номера в леджере: на штатном
    входе (все доставки `completed`) сетевых запросов не добавляется
    вовсе, а «слишком слабая идентичность» имени ветки остаётся ровно
    второй линией — она не решает за номер, а закрывает его отсутствие.

    Ветки берутся СВОИ (`branch` намерения ревизии либо `spec/<ws-id>-
    tasks` для v1) и резолвятся в номер `find_pr`, а не сверяются с
    `headRefName` кандидата: ops-примитив `prs_containing_commit` этого
    поля не отдаёт (jq-проекция в `governance/ops.py`), и проверка по
    нему была бы мёртвой. Обратная сторона выбора — delivery-PR ЧУЖОГО
    прогона того же воркстрима здесь не опознаётся: он остаётся вторым
    кандидатом и уводит поиск в fail-closed шага 4 (отказ с подсказкой
    `--approval-pr`), а не в подпись мимо человека.
    """
    numbers, branches = _ledger_delivery_prs(state)
    for branch in branches:
        found = ops.find_pr(state.repo_slug, branch, any_state=True)
        if found is not None:
            numbers.add(found)
    return frozenset(numbers)


def _search_correction_pr(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    base_ref: str,
) -> int:
    """Шаги 1–3 §I7: коммиты файлов активного DAG → номер correction-PR.

    Поиск идёт по КАЖДОМУ файлу DAG, а не от файла терминального узла:
    штатный correction правит upstream-узел и терминального может не
    касаться вовсе (перепиновку downstream §I7 объявил механической и
    отложил до штампа переиздания). Пока стартовали с анкера, последним
    его тронувшим оказывался штамп-коммит НАШЕГО ЖЕ прошлого tasks-PR —
    единственный кандидат, и поузловое правило схлопывалось в
    bundle-wide молча.

    Дедупликация — на обоих уровнях, и она не украшение: узлы бандла
    приезжают в base ОДНИМ коммитом (штамп доставки трогает их разом),
    так что без неё один и тот же SHA уходил бы в сеть по разу на файл,
    а один и тот же PR считался бы кандидатом по разу на коммит и сам по
    себе давал бы «больше одного».
    """
    shas: list[str] = []
    for fname, _ in dag:
        sha = ops.last_commit_touching(
            state.target_dir, f"{state.bundle_dir}/{fname}"
        )
        if sha is not None and sha not in shas:
            shas.append(sha)
    if not shas:
        raise RuntimeError(
            f"ни один файл {state.bundle_dir}/ не менялся в истории "
            f"{base_ref} — correction не найден, подписи взять неоткуда"
        )
    candidates: set[int] = set()
    for sha in shas:
        candidates.update(
            p["number"] for p in ops.prs_containing_commit(state.repo_slug, sha)
            if p.get("state") == "MERGED" and p.get("baseRefName") == base_ref
        )
    own = _own_delivery_prs(state, ops)
    found = sorted(candidates - own)
    if len(found) == 1:
        return found[0]
    listed = ", ".join(f"#{n}" for n in found) or "нет"
    excluded = ", ".join(f"#{n}" for n in sorted(candidates & own)) or "нет"
    raise RuntimeError(
        f"correction-PR не определён однозначно: кандидатов {len(found)} "
        f"({listed}); собственные доставки прогона исключены ({excluded}), "
        f"просмотрено коммитов: {len(shas)} — подписи взять неоткуда, "
        "гадать нельзя. Назовите PR явно: --approval-pr <n>"
    )


def _resolve_correction_pr(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    base_ref: str,
    approval_pr: int | None,
) -> Correction:
    """PR, доставивший correction → провенанс + затронутые узлы DAG.

    §I7 спеки: подпись штампа берётся у correction-PR, а не у исходного
    бандл-PR — иначе штамп утверждает, что текущие байты одобрил человек,
    одобрявший другую версию. `--approval-pr` заменяет ПОИСК (шаги 1–3),
    но не ПРОВЕРКУ (шаг 5), и проверка ниже — общая для обоих путей: PR
    вмержен в `base_ref`, подпись полна, `signed_nodes` (состав файлов PR
    ∩ активный DAG) непуст. Требования «PR менял файл терминального
    узла» в контракте больше НЕТ — оно и схлопывало поузловое правило
    обратно в bundle-wide, самоподтверждаясь штампом нашей же доставки.

    Собственная доставка отвергается и на явном пути — по номерам
    леджера (без сетевого резолва веток: на явном пути его цена не
    окупается, а оператор называет номер, который видел). Иначе флаг
    оставался бы единственной дверью, через которую подпись человека,
    мержившего наш tasks-PR, уходит в штамп как подпись correction'а.
    """
    if approval_pr is None:
        number = _search_correction_pr(state, ops, dag, base_ref)
        where = f"PR #{number}, найденный по коммитам бандла"
    else:
        number = approval_pr
        where = f"--approval-pr {number}"
        if number in _ledger_delivery_prs(state)[0]:
            raise RuntimeError(
                f"{where}: это собственный delivery-PR прогона, а не "
                "correction — его подпись принадлежит мержу нашей же "
                "доставки; назовите PR, доставивший правку бандла"
            )
    # Подпись — отдельным запросом: эндпоинт commits/<sha>/pulls отдаёт
    # merged_by: null даже у вмерженного PR (ревью #164).
    facts = ops.pr_facts(state.repo_slug, number)
    if facts.get("state") != "MERGED":
        raise RuntimeError(
            f"{where}: PR не вмержен (state={facts.get('state')!r}) — "
            "подписи взять неоткуда"
        )
    if facts.get("baseRefName") != base_ref:
        raise RuntimeError(
            f"{where}: нацелен в {facts.get('baseRefName')!r}, а не в "
            f"{base_ref!r} — подписи взять неоткуда"
        )
    merged_by = (facts.get("mergedBy") or {}).get("login")
    merged_at = facts.get("mergedAt")
    if not merged_by or not merged_at:
        raise RuntimeError(
            f"PR #{number}: нет mergedBy/mergedAt — подпись штампа неполна"
        )
    files = set(ops.pr_files(state.repo_slug, number))
    signed = frozenset(
        _node_id(fname) for fname, _ in dag
        if f"{state.bundle_dir}/{fname}" in files
    )
    if not signed:
        # Место снятого требования про терминальный узел: PR, не
        # тронувший НИ ОДНОГО узла активного DAG, correction'ом этого DAG
        # не является. Без этой проверки флаг из «заменяет поиск»
        # превращался бы в «отключает проверку» — подпись любого
        # вмерженного в base_ref PR уходила бы в штамп бандла.
        raise RuntimeError(
            f"{where}: в его составе нет ни одного файла активного DAG "
            f"({state.bundle_dir}/) — подписывать нечего; назовите PR, "
            "доставивший правку бандла: --approval-pr <n>"
        )
    return Correction(number, merged_by, merged_at, signed)


def _previous_dag(
    state: RunState,
    ops: Ops,
    prev_op: dict,
    target_dir: str,
    bundle_dir: str,
    base_sha: str,
) -> tuple[tuple[tuple[str, tuple[str, ...]], ...] | None, str]:
    """DAG предыдущей доставки + откуда он взят (§I8 спеки).

    Запись `dag` в ревизии фиксирует ВЫБОР, не доказательство; для
    легаси-v1 состав выводится ИЗ ДВУХ источников — состава каталога
    бандла и якоря `traces_to` доставленной спеки в base — и обязан
    совпасть ТОЧНО с одним из `_dag_for(None|3|4|5)`. Каталог один не
    различает 5-узловой вариант от 6-узлового, если бандл переавторили
    после доставки; якорь спеки не различает 5 от 6, но отсекает 3 и 4.
    Спеки нет / frontmatter не разобрать / якорь не совпал — вывод НЕ
    удался (§I8 дословно), а не «совпало»: compatibility-случай §6.

    Отсутствие САМОГО каталога бандла — не «вывод не удался», а отказ:
    §I8 зовёт неудачей вывода состояние, где источники есть, но не
    сходятся, а здесь нет предмета переиздания. «unavailable» отменил бы
    сверку §I8 и повёл дальше — через сетевой §I7 к
    `_prospective_anchor`, где отказ пришёл бы про СОСТАВ бандла
    («доавторьте узлы либо передайте --legacy-bundle»), уводя оператора
    мимо причины. Функция трогает каталог первой в переиздании, поэтому
    диагностика (та же, что `_check_bundle_composition` даёт ниже по
    ходу) стоит здесь, а не сырой `FileNotFoundError` из `iterdir()`
    мимо `except RuntimeError` в `main`.
    """
    recorded = prev_op.get("dag")
    if recorded:
        return tuple((f, tuple(u)) for f, u in recorded), "previous_delivery"
    bundle_path = Path(target_dir) / bundle_dir
    if not bundle_path.is_dir():
        raise RuntimeError(
            f"каталога бандла {bundle_dir!r} нет в {target_dir!r} на base "
            f"{base_sha[:7]} — состав DAG предыдущей доставки выводить не "
            "из чего; проверьте bundle_dir в run.json и что бандл вмержен "
            "в base_ref"
        )
    present = {
        p.name for p in bundle_path.iterdir()
        if p.is_file() and p.suffix == ".md"
    }
    matches = [
        _dag_for(v) for v in (None, 3, 4, 5)
        if {f for f, _ in _dag_for(v)} == present
    ]
    if len(matches) != 1:
        return None, "unavailable"
    text = ops.show_file(target_dir, base_sha, f"spec/{state.ws_id}-tasks.md")
    if text is None:
        return None, "unavailable"
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return None, "unavailable"
    traces = meta.get("traces_to")
    if not isinstance(traces, list) or not traces:
        return None, "unavailable"
    if traces[0] != _node_id(matches[0][-1][0]):
        return None, "unavailable"
    return matches[0], "derived_from_spec"


def _reconcile_revision(
    n: int,
    op: dict,
    base_sha: str,
    pr: int | None,
    facts: dict,
    replaced: bool = False,
) -> str:
    """Решение по таблице §I3 спеки. Возврат — имя перехода, не действие.

    Имя ветки НЕ доказывает принадлежность: под ним может лежать чужая
    работа, поэтому у каждого живого PR сверяется `headRefOid` с
    записанным `head_sha` намерения. Отсутствующий `headRefOid` — тоже
    отказ: «идентичность не удалось узнать» §I3 зелёным не объявляет.

    Факты PR принимаются АРГУМЕНТОМ, а не запрашиваются: вызывающий цикл
    и так их получил, а функция запрашивала `find_pr` второй раз и
    `pr_facts` — дважды (минор C-8: четыре сетевых запроса к `gh` на
    ревизию вместо одного, лишний источник флака). Побочно решение по
    §I3 стало чистым — таблица проверяема без стабов ops.

    `replaced` — факт из леджера, а не из сети: более поздняя ревизия
    объявила предложение этой снятым (§I10). Тогда ревизия выбывает из
    разбора переходом `"replaced"` (вызывающий смотрит предыдущую, как на
    `abandoned`) — и при `CLOSED-unmerged` (иначе fail-closed отравил бы
    воркстрим навсегда), и при OPEN.

    OPEN здесь не исключение и не поблажка: вернуть отозванный PR значит
    вручить оператору при RC 0 то самое незамерженное дефектное
    предложение, которое леджер объявил снятым. Прежняя мотивировка «при
    живом OPEN замена не доведена, иначе останется два открытых PR»
    неверна по коду: `_replacement_close` стоит ДО `deliver` на ОБОИХ
    путях (свежем и возобновлении), поэтому к моменту создания нового PR
    отозванный уже закрыт. А если ревизия-замена сама брошена, её
    обязательство перенимает следующая (`_pending_replacement`) — и
    закрывает отозванный PR тем же шагом.

    Единственное состояние, которое `replaced` НЕ покрывает, — `MERGED`:
    отозванное предложение оказалось вмерженным, то есть отзыв
    противоречит факту. Разбирать его обычной таблицей честнее, чем
    молча пропустить: дальше по ходу `_check_replacement_target`
    отказывает безусловным «вмерженное не заменяется».
    """
    branch = op.get("branch")
    pr_state = facts.get("state") if pr is not None else None
    if replaced and pr_state != "MERGED":
        # §I10: и закрытие, и живой OPEN у отозванной ревизии —
        # ожидаемые состояния, если более поздняя запись объявила это
        # предложение снятым (`replaces_revision`/`replaces_pr` = эта
        # ревизия и этот PR). Приём тот же, которым §I3 развязал
        # `abandoned`: смотрим вперёд, запись N не трогаем. Без этого
        # замена была бы одноразовой (на закрытии — fail-closed навсегда)
        # либо самоотменяющейся (на OPEN — возврат отозванного PR при
        # RC 0, то есть приглашение смержить дефектное предложение).
        return "replaced"
    if pr is not None and pr_state not in ("OPEN", "MERGED"):
        raise RuntimeError(
            f"PR #{pr} по ветке {branch} закрыт без мержа — ветка отклонена "
            "человеком; переиздание fail-closed"
        )
    if pr is not None and op.get("head_sha"):
        actual_head = facts.get("headRefOid")
        if not actual_head:
            # Fail-closed на НЕИЗВЕСТНОЙ идентичности: §I3 объявляет
            # зелёной только сошедшуюся сверку, а не «сверить не вышло».
            # Пропустив её, мы завершили бы ревизию чужим PR и приписали
            # ему anchor нашего намерения — дальше §I5 сверялся бы с
            # anchor'ом доставки, которой не было. Соседний fail-open в
            # `_delivered_anchor` (тоже пустой `headRefOid`) сознателен и
            # НЕ парен этому: там факт лишь записывается неизвестным, и
            # переиздание уходит §6-путём, ничего не утверждая.
            raise RuntimeError(
                f"идентичность не проверить: у PR #{pr} нет headRefOid, "
                f"а намерение ревизии {n} стоит на {op['head_sha'][:7]}; "
                f"решите судьбу PR явно: --abandon-revision {n}"
            )
        if actual_head != op["head_sha"]:
            raise RuntimeError(
                f"идентичность не сошлась: PR #{pr} стоит на "
                f"{actual_head[:7]}, намерение ревизии {n} — "
                f"{op['head_sha'][:7]}; под тем же именем ветки чужая работа"
            )
    if op.get("status") == "completed":
        return "return_pr"
    same_base = op.get("base_sha") == base_sha
    if pr is not None and pr_state == "MERGED":
        return "complete"
    if pr is not None and pr_state == "OPEN":
        if same_base:
            return "continue"
        raise RuntimeError(
            f"ревизия {n} начата с другого base, а её PR #{pr} открыт: "
            f"продолжать нельзя (PR выведен из {op.get('base_sha', '?')[:7]}), "
            "и второй открытый PR заводить нельзя. Закройте его явно: "
            f"--abandon-revision {n}"
        )
    return "continue" if same_base else "abandon_and_next"


def _reconcile_v1(state: RunState, ops: Ops, op: dict) -> int | None:
    """§I3 для исторической v1 (`tasks-deliver`) — её PR ещё открыт?

    v1 участвует в переиздании как ПОЛНОЦЕННАЯ доставка (`_last_delivery`
    её учитывает, §I5/§I8 сверяются с ней), значит и первая строка
    таблицы §I3 «`completed` | OPEN → вернуть существующий PR» на неё
    распространяется. Иначе на одну спеку оказывались бы два открытых PR,
    а закрытие старого потом попадало бы под вечный `CLOSED-unmerged` →
    fail-closed — ровно конфликт I3×I4, разобранный в §I3.

    Через `_reconcile_revision` легаси-запись не гоняется: у неё нет ни
    `branch`, ни `base_sha`, ни `head_sha` — сверять идентичность нечем.
    Разбирается ровно то, что в записи есть: номер её PR.

    Возврат: номер PR, если он ещё OPEN (переиздавать нечего); `None` —
    доставка закрыта мержем либо номера PR в записи нет (древняя запись:
    поведение как до supersede).
    """
    pr = op.get("pr")
    if not isinstance(pr, int):
        return None
    pr_state = ops.pr_facts(state.repo_slug, pr).get("state")
    if pr_state == "MERGED":
        return None
    if pr_state != "OPEN":
        raise RuntimeError(
            f"PR #{pr} первой доставки закрыт без мержа "
            f"(state={pr_state!r}) — ветка отклонена человеком; "
            "переиздание fail-closed"
        )
    return pr


def _recover_commit(state: RunState, ops: Ops, n: int, op: dict) -> None:
    """Гвард таблицы §I3.1: коммит и запись head_sha не атомарны.

    Возврата НЕТ намеренно (минор F-03 финального ревью): все строки
    §I3.1, кроме fail-closed, ведут к одному и тому же продолжению —
    детерминированной доставке из намерения. Она сама доводит нужное:
    коммита нет → `commit_paths` создаёт его; коммит уже есть и опознан
    своим → `commit_paths` на неизменившемся файле не создаёт второй, а
    `after_commit` durable пишет head_sha ИМЕННО этого коммита («принять
    его» из §I3.1). Возвращённый SHA было некуда деть: push именно этого
    SHA — не наш примитив (`push_branch` пушит ветку), и прод возврат не
    читал, отчего тесты утверждали значение, которого нет в поведении.

    Бросает на несоответствии факта намерению (под именем ветки ревизии
    лежит чужая работа) и на «`head_sha` записан, ветки в клоне нет» —
    см. ниже.

    Зовётся ТОЛЬКО на пути переиздания («PR ревизии не найден»): при
    живом PR идентичность коммита уже доказана `_reconcile_revision` по
    `headRefOid`, доставка не переигрывается и локальный head в исходе не
    участвует — сверять его там значило бы ломать строку §I3 «started |
    PR OPEN | идентичность сошлась» на локально сдвинутой ветке.

    Отсюда и fail-closed на «`head_sha` записан, ветки в клоне нет»:
    опознать коммит нечем, а вызывающий пошёл бы переиздавать
    детерминированно — `ensure_branch` создала бы ветку ЗАНОВО от base,
    `commit_paths` сделал бы НОВЫЙ коммит (тот же tree, другой committer
    date ⇒ другой SHA), а `after_commit` затёр бы `head_sha` намерения —
    §I3 называет его единственным фактом, по которому ревизию опознают в
    удалённой ветке. Это потеря записи журнала, а не плохая диагностика.
    """
    branch, head = op.get("branch"), op.get("head_sha")
    local = ops.rev_parse(state.target_dir, branch) if branch else None
    if head:
        if local is None:
            raise RuntimeError(
                f"ревизия {n}: head_sha {head[:7]} записан, а ветки "
                f"{branch} в этом клоне нет — коммит уже создан (и, "
                "возможно, запушен), пересоздавать его нельзя: новый "
                "коммит затрёт единственный факт опознания ревизии в "
                f"удалённой ветке. Верните ветку (git fetch origin "
                f"{branch} && git switch {branch}) и повторите запуск "
                f"либо решите судьбу ревизии явно: --abandon-revision {n}"
            )
        if local and local != head:
            # Сверяется ЛОКАЛЬНЫЙ head (`ops.rev_parse` резолвит
            # refs/heads/), не remote: разошедшийся remote отклоняет push
            # как non-ff — сообщение обещать remote-сверку не должно
            # (минор C-3 финального ревью).
            raise RuntimeError(
                f"локальный head ветки {branch} = {local[:7]}, "
                f"намерение — {head[:7]}: ветку двигали снаружи"
            )
        return
    if local is None or local == op.get("base_sha"):
        # Строка §I3.1 «null | подходящего коммита нет». Ветка на base —
        # это именно она, а не чужой коммит: `deliver` создаёт ветку
        # (`ensure_branch` от текущего HEAD, то есть от base) ЗАДОЛГО до
        # `commit_paths`, и падение/Ctrl+C в этом окне оставляет ветку
        # стоящей ровно на `base_sha` без коммита доставки. Без этой
        # ветки сверка ниже брала бы родителя базы, он `base_sha` не
        # равен никогда, и ревизия объявлялась бы неремонтируемой
        # (blocker C-2 финального ревью).
        return
    rel = f"spec/{state.ws_id}-tasks.md"
    parent = ops.commit_parent(state.target_dir, local)
    blob = ops.blob_in_commit(state.target_dir, local, rel)
    if parent == op.get("base_sha") and blob == op.get("tasks_blob"):
        return
    raise RuntimeError(
        f"чужой коммит в ветке {branch}: родитель {parent!r} / блоб "
        f"{blob!r} не отвечают намерению ревизии"
    )


def _previous_tasks_version(state: RunState) -> int:
    """`version:` доставленной tasks-спеки в свежем base (§I6 спеки).

    Читает рабочее дерево `target_dir` НАПРЯМУЮ (не `ops.show_file`) —
    вызывающая сторона (`deliver_superseded`) уже освежила его до
    `base_ref` до вызова. Файла нет либо frontmatter/поле не разобрать —
    fail-closed: монотонность версии гадать нельзя (минор ревью #161).
    """
    rel = Path(state.target_dir) / "spec" / f"{state.ws_id}-tasks.md"
    if not rel.exists():
        raise RuntimeError(
            f"{rel} не найден на базе — версию предыдущей доставки взять "
            "неоткуда"
        )
    try:
        meta, _ = split_frontmatter(rel.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(
            f"{rel}: frontmatter не разобрать ({exc}) — версию предыдущей "
            "доставки взять неоткуда"
        ) from exc
    version = meta.get("version")
    if not isinstance(version, int):
        raise RuntimeError(
            f"{rel}: version={version!r} — не целое число, версию "
            "предыдущей доставки взять неоткуда"
        )
    return version


def _tasks_blob_cb(state: RunState, n: int) -> Callable[[dict], None]:
    """Durable-запись ожидаемого блоба спеки ДО коммита (§I3.1).

    Отдельным действием от `_commit_facts_cb` намеренно: у окна «коммит
    создан, `head_sha` ещё не записан» опознание держится ровно на
    `tasks_blob` — записанный ПОСЛЕ коммита, он в этом окне всегда `null`,
    и `_recover_commit` объявил бы собственный коммит ревизии чужим.
    """

    def _cb(facts: dict) -> None:
        key = f"{_REVISION_PREFIX}{n}"
        state.ops[key] = {**state.ops[key], "tasks_blob": facts["tasks_blob"]}
        save(state)

    return _cb


def _commit_facts_cb(
    state: RunState, ops: Ops, n: int, prospective: str
) -> Callable[[dict], None]:
    """Durable-запись `head_sha` + сверка §I2 между commit и push.

    Отказ внутри колбэка оставляет коммит без push и ревизию в `started` —
    корректное состояние: его разбирает реконсиляция при следующем запуске.
    """

    def _cb(facts: dict) -> None:
        if facts["anchor_blob"] != prospective:
            raise RuntimeError(
                "фактический штамп анкера "
                f"{facts['anchor_blob'][:7]} разошёлся с проспективным "
                f"{prospective[:7]} — апстрим двигали во время доставки"
            )
        key = f"{_REVISION_PREFIX}{n}"
        state.ops[key] = {**state.ops[key], "head_sha": facts["head_sha"]}
        save(state)

    return _cb


@dataclass(frozen=True)
class SupersedeResult:
    """Исход переиздания: номер PR и то, ЧЕМ этот номер является.

    Голый `int` этого не различал, а оператору различие критично:
    реконсиляция §I3 в терминальных исходах возвращает номер уже
    СУЩЕСТВУЮЩЕГО PR (первой доставки либо предыдущей ревизии), и CLI
    печатал поверх её правдивой строки «переизданная tasks-спека
    доставлена: PR #N» — читая последнюю строку при RC 0, оператор шёл
    мержить чужой PR как переиздание, хотя ни ветки
    `spec/<ws-id>-tasks-v<N>`, ни нового PR не создавалось.

    Различие СТРУКТУРНОЕ, а не эвристика на печатающей стороне: «а не
    печатали ли мы уже» — угадывание, которое разъезжается с кодом при
    первом же новом исходе.

    `kind`:

    - `delivered` — доставка выполнена ЭТИМ вызовом (новая ревизия либо
      достройка прерванной), `pr` — её номер;
    - `returned` — вернули существующий PR (§I3), ничего не создано;
    - `noop` — бесследный no-op §I5: апстрим не менялся, `run.json` не
      тронут, PR нет.
    """

    kind: Literal["delivered", "returned", "noop"]
    pr: int | None = None

    def __post_init__(self) -> None:
        if (self.pr is None) != (self.kind == "noop"):
            raise ValueError(
                f"SupersedeResult: kind={self.kind!r} несовместим с "
                f"pr={self.pr!r} — номер PR есть у всех исходов, кроме noop"
            )


def deliver_superseded(
    state: RunState,
    ops: Ops,
    legacy_bundle: int | None = None,
    approval_pr: int | None = None,
    replace: Replacement | None = None,
) -> SupersedeResult:
    """Санкционированное переиздание tasks-спеки (спека 2026-09-09).

    Порядок: синхронизация base → реконсиляция незакрытой ревизии (§I3) →
    разбор PR первой доставки, если последняя доставка — она (§I3 для
    легаси-v1) → вывод/сверка активного DAG предыдущей доставки (§I8) →
    `content_anchor` (§I5) → сверка с записанным `content_anchor`
    предыдущей доставки → ТОЛЬКО при расхождении: provenance
    correction-PR (§I7) → проспективный anchor (§I2) → намерение (§I4,
    write-ahead) → доставка в НОВУЮ ветку `spec/<ws-id>-tasks-v<N>`.

    Сверка §I5 стоит ВЫШЕ разрешения провенанса намеренно (решение
    владельца 2026-09-09). Провенанс ищет ПОСЛЕДНИЙ коммит, менявший
    анкер; после доставки это штамп-коммит нашего же tasks-PR, а его
    подпись — не подпись бандл-PR. Пока §I5 сравнивал пост-штамповый
    anchor, подпись лежала ВНУТРИ сравниваемых байтов, равенство не
    достигалось никогда, и каждый `--supersede` заводил очередную
    ревизию. `content_anchor` подписи не видит и провенанса не требует —
    бесследный no-op происходит ДО того, как переиздание могло бы
    принять СВОЙ ЖЕ tasks-PR за correction.

    Реконсиляция ТЕРМИНАЛЬНА для вызова во всех исходах, кроме
    `abandon_and_next`: `return_pr`/`complete` возвращают PR ревизии, а
    `continue` доводит ЕЁ ЖЕ доставку (в её ветке, по её намерению). Ниже
    реконсиляции код доходит только тогда, когда незакрытых ревизий нет —
    второй открытый PR не заводится ни при каком состоянии леджера (§I3).

    Возврат — `SupersedeResult`: `kind="delivered"` (доставка выполнена
    этим вызовом), `kind="returned"` (вернули существующий PR — исходы
    реконсиляции §I3, включая PR первой доставки) либо `kind="noop"` —
    бесследный no-op §I5: апстрим не менялся с прошлой доставки,
    run.json не трогается.

    `replace` — явный replace-переход (§I10): незамерженное предложение
    названной ревизии снимается со стола и заменяется новым. Порядок
    внутри вызова — владельческий: валидация заменяемой ревизии → durable
    намерение новой с `replaces_*` → закрытие заменяемого PR → доставка →
    удаление ветки заменённой ревизии.

    Замена стоит ВЫШЕ бесследного no-op §I5, и это не оптимизация:
    §I5 спрашивает про АПСТРИМ («менялось ли содержание»), а замена — про
    ПРЕДЛОЖЕНИЕ («лежит ли на столе дефектный PR»). Живой случай
    (devtools#172) — дефект в ПОДПИСИ штампа при неизменном апстриме, а
    `content_anchor` подписи не видит по построению (§I2). Сработай §I5
    первым, замена стала бы недостижима ровно в том классе случаев, ради
    которого заведена.
    """
    if state.status != "completed":
        raise RuntimeError(
            f"run {state.run_id!r} в статусе {state.status!r}, нужен "
            "'completed'"
        )
    if ops.is_dirty(state.target_dir):
        raise RuntimeError(
            f"target_dir {state.target_dir!r} грязный — переиздание не начато"
        )
    base_ref = state.base_ref or "master"
    ops.checkout_and_pull(state.target_dir, base_ref)
    base_sha = ops.rev_parse(state.target_dir, "HEAD")
    if base_sha is None:
        # Fail-closed (минор ревью Task 7, B7): при None сверка «база та
        # же» (`_reconcile_revision`) вырождается в тождество, а сообщение
        # abandon падает TypeError на `None[:7]`.
        raise RuntimeError(
            f"rev_parse HEAD в {state.target_dir!r} не дал SHA — база "
            "неизвестна, переиздание не начато"
        )

    active = _dag_for(legacy_bundle)
    # Валидация заменяемой ревизии — ПЕРВЫМ шагом порядка владельца и до
    # единого эффекта: она read-only и отказывает раньше, чем леджер
    # тронут. На повторе (намерение уже durable) её результат не нужен —
    # шаги замены читаются из намерения, — но проверка всё равно уместна:
    # вмешаться в заменяемый PR могли и между заходами.
    replace_fields = (
        _validate_replacement(state, ops, replace) if replace else {}
    )

    # Незавершённая ревизия реконсилируется ДО любых новых эффектов, и её
    # решение исполняется ЦЕЛИКОМ: игнорировать "continue"/"complete"/
    # "return_pr" значит завести ВТОРОЙ открытый PR, что §I3 запрещает
    # явным разбором конфликта I3×I4 — а состояние ещё и
    # самовоспроизводится (каждый следующий запуск плодит ревизию и PR).
    # Терминальны для вызова все исходы, кроме `abandon_and_next`.
    for n, op in reversed(_revisions(state)):
        status = op.get("status")
        if status not in ("started", "completed"):
            continue          # abandoned — терминальна, смотрим предыдущую
        if replace is not None and n == replace.revision:
            # Заменяемая ревизия не реконсилируется: её предложение
            # снимается со стола этим же вызовом. Без пропуска §I3 вернул
            # бы её PR («completed | OPEN → вернуть существующий»), и
            # замена была бы недостижима — та самая ловушка, ради снятия
            # которой переход и заведён.
            continue
        # PR ревизии и его факты запрашиваются ЗДЕСЬ, по одному разу, и
        # передаются в `_reconcile_revision` аргументом (минор C-8): она
        # имя перехода возвращает, а номер PR нужен и вызывающему.
        pr = (
            ops.find_pr(state.repo_slug, op["branch"], any_state=True)
            if op.get("branch") else None
        )
        facts = ops.pr_facts(state.repo_slug, pr) if pr is not None else {}
        if status == "completed" and (
            pr is None or facts.get("state") == "MERGED"
        ):
            # §I3 «completed | MERGED»: доставка состоялась, переиздание
            # решается ТОЛЬКО по §I5 — реконсилировать нечего. Остальные
            # состояния PR завершённой ревизии (OPEN → вернуть его;
            # CLOSED-unmerged → fail-closed) разбирает _reconcile_revision.
            if pr is not None:
                # PR ревизии существует ⇒ шаг удаления ветки заменённой
                # ревизии разрешён (порядок владельца). Ниже по ходу
                # вызова этот `op` уже не встретится — цикл прерывается.
                _replacement_cleanup(state, ops, op)
            break
        decision = _reconcile_revision(
            n, op, base_sha, pr, facts,
            replaced=_replaced_by(state, n, pr) is not None,
        )
        if decision == "replaced":
            # §I10: закрытие этого PR — ожидаемое состояние, о нём
            # говорит более поздняя запись. Ревизия из разбора выбывает
            # так же, как `abandoned`.
            continue
        if (
            decision in ("complete", "continue")
            and pr is not None
            and not op.get("head_sha")
        ):
            # Идентичность PR сверяется по `head_sha` намерения, а при
            # пустом `head_sha` сверка пропускается — чужой PR под именем
            # нашей ветки завершил бы ревизию. Наш коммит пишет `head_sha`
            # ДО push (§I3), значит «PR есть, head_sha пуст» = PR завёл
            # не этот прогон. Присваивать его нельзя.
            raise RuntimeError(
                f"ревизия {n}: PR #{pr} существует, а head_sha в её "
                "намерении пуст — доставка этого прогона его не создавала; "
                f"решите судьбу PR явно: --abandon-revision {n}"
            )
        if decision == "abandon_and_next":
            _abandon_revision(
                state, n,
                f"base сдвинулся ({op.get('base_sha', '?')[:7]} → "
                f"{base_sha[:7]}), открытого PR нет",
            )
            break
        if decision == "return_pr":
            # Окно «PR новой ревизии создан, ветка заменённой ещё жива»:
            # хвост замены доводится и на возврате существующего PR, иначе
            # он не доводился бы никогда — этот исход терминален.
            _replacement_cleanup(state, ops, op)
            print(
                f"ревизия {n} уже доставлена — PR #{pr}; новая ревизия не "
                "заводится"
            )
            return SupersedeResult("returned", pr)
        if decision == "complete":
            _complete_revision(
                state, n, pr=pr, anchor=op["prospective_anchor"],
                content_anchor=op.get("content_anchor"),
                head_sha=op["head_sha"],   # непуст по гварду выше
            )
            _replacement_cleanup(state, ops, op)
            # Доставка состоялась; нужно ли ещё одно переиздание — решает
            # следующий запуск по §I5.
            print(f"ревизия {n} доставлена ранее — PR #{pr} вмержен")
            return SupersedeResult("returned", pr)
        # "continue": возобновляем ревизию n в ЕЁ ветке, а не заводим новую.
        if _dag_for(legacy_bundle) != tuple(
            (f, tuple(u)) for f, u in op.get("dag", [])
        ):
            raise RuntimeError(
                f"ревизия {n} начата с другим составом DAG — повторите "
                "запуск с тем же --legacy-bundle, что и в её намерении"
            )
        if pr is not None:
            # Доставка дошла до конца: идентичность коммита доказана
            # `_reconcile_revision` (`headRefOid` == `head_sha`), а ветку
            # этот исход не трогает — доставка не переигрывается. Поэтому
            # гвард §I3.1 (`_recover_commit`) здесь НЕ зовётся: он сверяет
            # ЛОКАЛЬНЫЙ head, и уехавшая локальная ветка ломала бы строку
            # §I3 «started | PR OPEN | идентичность сошлась», которая
            # обещает возврат существующего PR.
            _complete_revision(
                state, n, pr=pr, anchor=op["prospective_anchor"],
                content_anchor=op.get("content_anchor"),
                head_sha=op["head_sha"],   # непуст по гварду выше
            )
            _replacement_cleanup(state, ops, op)
            print(f"ревизия {n} доставлена ранее — PR #{pr}")
            return SupersedeResult("returned", pr)
        # PR-а нет: доставка не дошла до последнего шага. Повторяем её
        # ДЕТЕРМИНИРОВАННО из намерения — байты те же, потому что
        # generated_at/version/branch зафиксированы в намерении, а
        # commit_paths на пустом индексе не создаёт второй коммит.
        # Бросает на чужом коммите/ветке и на «head_sha записан, ветки в
        # клоне нет» (там опознавать нечем, а переиздание затёрло бы SHA).
        _recover_commit(state, ops, n, op)
        correction = _resolve_correction_pr(
            state, ops, active, base_ref, op["approval_pr"]
        )
        # §I7: состав подписываемых узлов берётся ИЗ НАМЕРЕНИЯ, а не
        # пересчитывается. Проспективный anchor ревизии посчитан с ним же,
        # и разойдись они — доставка упёрлась бы в гард §I2 на собственном
        # повторе. Намерения, записанные до поузлового §I7, состава не
        # несут: для них — фактический состав correction-PR.
        signed = op.get("signed_nodes")
        restamp = (
            frozenset(signed) if signed is not None
            else correction.signed_nodes
        )
        # Окна A и B замены: намерение durable, а заменяемый PR ещё
        # открыт (A) либо уже закрыт (B). Шаг идемпотентен и читается ИЗ
        # НАМЕРЕНИЯ — повтор доводит его, даже когда запущен без
        # `--replace-revision`.
        _replacement_close(state, ops, op)
        pr = deliver(
            target_dir=state.target_dir,
            repo_slug=state.repo_slug,
            ws_id=state.ws_id,
            subject=state.subject,
            bundle_dir=state.bundle_dir,
            base_ref=base_ref,
            ops=ops,
            approved_by=correction.approved_by,
            approved_at=correction.approved_at,
            generated_at=op["expected_generated_at"],
            legacy_bundle=legacy_bundle,
            profile=state.profile,
            branch=op["branch"],
            version=op["tasks_version"],
            restamp_nodes=restamp,
            # Состояние исполнения — из base ЭТОЙ ревизии, взятого из
            # намерения, а не из текущего `base_sha`: детерминизм повтора
            # не должен зависеть от того, куда с тех пор уехал апстрим.
            # (Исход "continue" достижим только при `same_base`, так что
            # значения совпадают, — но источник назван явно.)
            carry_from=ops.show_file(
                state.target_dir,
                op["base_sha"],
                f"spec/{state.ws_id}-tasks.md",
            ),
            before_commit=_tasks_blob_cb(state, n),
            after_commit=_commit_facts_cb(
                state, ops, n, op["prospective_anchor"]
            ),
        )
        # head_sha уже записан колбэком durable — между коммитом и push.
        _complete_revision(
            state, n, pr=pr, anchor=op["prospective_anchor"],
            content_anchor=op.get("content_anchor"),
        )
        _replacement_cleanup(state, ops, op)
        return SupersedeResult("delivered", pr)

    # Обязательство брошенной ревизии-замены переходит на следующую: PR
    # отозван (или ещё ждёт отзыва), ветка жива, а forward-ссылка обязана
    # продолжать существовать — иначе §I3 снова поймает закрытый PR как
    # «отклонён человеком».
    #
    # Считается ПОСЛЕ реконсиляции, и это не косметика (major ревью
    # PR #174): цикл выше сам переводит ревизию-замену в `abandoned`
    # (`abandon_and_next` на сдвинувшемся base), а до цикла её статус ещё
    # `started` — перенос, посчитанный раньше, брал бы устаревший ответ
    # «переносить нечего». Новая ревизия уходила бы без `replaces_*`,
    # закрывать было бы нечего, и отозванный PR остался бы ОТКРЫТЫМ
    # вторым на ту же спеку.
    # Разрядка отзывов считается ОДИН раз на вызов и питает обе
    # производные: перенос обязательства и выбор предыдущей доставки.
    # Сетевого запроса нет вовсе, если в леджере нет ни одного отзыва.
    discharged = _discharged_replacements(state, ops)
    if not replace_fields:
        replace_fields = _pending_replacement(state, discharged)

    # ПОСЛЕ реконсиляции (дефект 3 ревью Task 7): она может перевести
    # `started` → `completed`, и тогда предыдущая доставка — именно та.
    prev = _last_delivery(
        state, skip=replace_fields.get("replaces_revision"),
        discharged=discharged,
    )
    if prev is None:
        raise RuntimeError(
            "доставок ещё не было — переиздавать нечего; обычная доставка "
            "идёт без --supersede"
        )
    prev_n, prev_op = prev
    if prev_n == 1:
        # §I3 строка 1 для исторической v1: цикл выше её не видит
        # (`_revisions` собирает только `tasks-deliver-v<N>`), и без этой
        # ветки состояние «первая доставка ещё висит открытым PR»
        # проваливалось в `_previous_tasks_version` и отказывало
        # сообщением про версию — RC 1 вместо контрактных RC 0 + возврат
        # PR, и диагностика уводила оператора не туда (major C-1).
        v1_pr = _reconcile_v1(state, ops, prev_op)
        if v1_pr is not None:
            print(
                f"первая доставка уже открыта PR #{v1_pr} — переиздавать "
                "нечего, пока он не вмержен; новая ревизия не заводится"
            )
            return SupersedeResult("returned", v1_pr)

    dag, dag_source = _previous_dag(
        state, ops, prev_op, state.target_dir, state.bundle_dir, base_sha,
    )
    if dag is not None and dag != active:
        raise RuntimeError(
            "состав активного DAG отличается от предыдущей доставки — "
            "это не переиздание, а другая доставка"
        )
    # §I5 — ДО провенанса и ДО любого сетевого вызова: `content_anchor`
    # считается локально по проштампованному дереву и подписи не видит.
    content = _content_anchor(
        state.target_dir, state.bundle_dir, legacy_bundle
    )
    recorded_content = prev_op.get("content_anchor")
    if (
        # Незакрытое обязательство замены снимает бесследный no-op §I5:
        # он отвечает про АПСТРИМ, а замена — про ПРЕДЛОЖЕНИЕ. Живой
        # случай (дефект подписи при неизменном апстриме) даёт равные
        # `content_anchor` по построению — §I5 сработал бы первым и
        # сделал замену недостижимой ровно там, где она и нужна.
        #
        # Условие стоит на `replace_fields`, а не на аргументе `replace`
        # (major ревью PR #174): обязательство бывает и перенятым от
        # брошенной ревизии-замены, и тогда флага в этом запуске нет — а
        # no-op молча испарил бы обязательство при RC 0, оставив
        # отозванный PR открытым, а его ветку живой.
        not replace_fields
        and recorded_content is not None
        and recorded_content == content
    ):
        print(
            "апстрим не менялся — переиздание не требуется "
            f"(content_anchor {content[:7]})"
        )
        return SupersedeResult("noop")

    correction = _resolve_correction_pr(
        state, ops, active, base_ref, approval_pr
    )
    prospective = _prospective_anchor(
        state.target_dir, state.bundle_dir, correction.approved_by,
        correction.approved_at, legacy_bundle,
        restamp_nodes=correction.signed_nodes,
    )
    n = _next_revision(state)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    version = _previous_tasks_version(state) + 1
    branch = f"spec/{state.ws_id}-tasks-v{n}"
    intent = {
        "branch": branch,
        "base_sha": base_sha,
        "prospective_anchor": prospective,
        "content_anchor": content,
        "approval_pr": correction.pr,
        # §I7 поузловой: состав подписываемых узлов durable, а не
        # пересчитываемый. Возобновление берёт его отсюда — иначе
        # штамп повтора разошёлся бы с `prospective_anchor` намерения.
        "signed_nodes": sorted(correction.signed_nodes),
        "tasks_version": version,
        "dag": [[f, list(u)] for f, u in active],
        "dag_source": dag_source,
        "supersedes": prev_n,
        "expected_generated_at": generated_at,
        # Заполняется хуком `before_commit` доставки (`_tasks_blob_cb`) —
        # ДО коммита, отдельно от head_sha. Порознь они не для красоты:
        # пока оба писались одним действием после коммита, состояние
        # «коммит есть, head_sha ещё нет» приходило и с пустым tasks_blob,
        # и строка §I3.1 «null + подходящий коммит → принять его» была
        # недостижима — восстановление объявляло собственный коммит
        # ревизии чужим.
        "tasks_blob": None,
        # Замена (§I10): ДВЕ независимые связи. `supersedes` выше —
        # какую последнюю УСПЕШНО ДОСТАВЛЕННУЮ спецификацию переиздаём;
        # `replaces_*` — какое незамерженное ошибочное ПРЕДЛОЖЕНИЕ
        # снимаем со стола. Пустой словарь на обычном переиздании: полей
        # нет вовсе, а не `None` — «замены не было» и «замена чего-то
        # неизвестного» не одно и то же.
        **replace_fields,
    }
    if recorded_content is None:
        # §6 спеки: сверка была невозможна — фиксируем это В ЖУРНАЛЕ.
        # Считается ровно отсутствие `content_anchor`: `anchor` записи
        # старого образца задним числом не переосмысливается — он отвечал
        # на другой вопрос (§I2), и принять его за содержание значило бы
        # объявить сверку там, где её не было.
        intent["comparison"] = "unavailable"
        print(
            "предыдущая доставка не записала content_anchor — сверка не "
            "производилась (comparison: unavailable)"
        )
    _start_revision(state, n, intent)
    # Порядок владельца: намерение durable → ЗАКРЫТЬ заменяемый PR →
    # доставить. Наоборот нельзя: доставка завела бы второй открытый PR
    # на ту же спеку, а падение между ними не оставило бы следа, по
    # которому повтор понял бы, что закрывать.
    _replacement_close(state, ops, state.ops[f"{_REVISION_PREFIX}{n}"])
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=base_ref,
        ops=ops,
        approved_by=correction.approved_by,
        approved_at=correction.approved_at,
        generated_at=generated_at,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
        branch=branch,
        version=version,
        # §I7: подпись штампа — от correction-PR, а узлы бандла в base уже
        # approved после доставки v1; без этого множества она молча
        # отбрасывалась бы. Подписываются ТОЛЬКО узлы, которые
        # correction-PR действительно менял.
        restamp_nodes=correction.signed_nodes,
        # Переиздают ровно те воркстримы, что уже в работе: состояние
        # исполнения переносится из ДОСТАВЛЕННОЙ спеки в base (тот же
        # источник, что у `_previous_dag`), не из рабочего дерева и не из
        # HEAD — base зафиксирован `base_sha` намерения.
        carry_from=ops.show_file(
            state.target_dir, base_sha, f"spec/{state.ws_id}-tasks.md"
        ),
        before_commit=_tasks_blob_cb(state, n),
        after_commit=_commit_facts_cb(state, ops, n, prospective),
    )
    # head_sha здесь НЕ пишется: он уже записан колбэком durable — между
    # коммитом и push (§I3), а не после создания PR.
    _complete_revision(
        state, n, pr=pr, anchor=prospective, content_anchor=content
    )
    # Последний шаг порядка владельца: ветка заменённой ревизии удаляется
    # только теперь — намерение durable, заменяемый PR закрыт, новый PR
    # создан. Всё, что раньше, оставило бы ошибочный артефакт без ветки
    # при недоведённой замене.
    _replacement_cleanup(state, ops, state.ops[f"{_REVISION_PREFIX}{n}"])
    return SupersedeResult("delivered", pr)


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
        help="переиздать tasks-спеку после correction'а апстрима: новая "
             "ветка spec/<ws-id>-tasks-v<N>, новый PR, отдельная ревизия в "
             "леджере; неизменившееся содержание апстрима "
             "(content_anchor) — успешный no-op без изменений",
    )
    parser.add_argument(
        "--approval-pr", type=int, default=None,
        help="явный correction-PR для подписи штампа, когда автоматика даёт "
             "ноль или несколько кандидатов; проверяется так же",
    )
    parser.add_argument(
        "--approve-node", default=None, metavar="NODE_ID",
        help="одобрить узел активного DAG (§I12): ЕДИНСТВЕННЫЙ переход "
             "draft|stale → approved. Подпись — активный GitHub-логин "
             "(человек), время — момент вызова; downstream уходят в stale "
             "рекурсивно. Доставляется накапливающей веткой "
             "spec/<ws-id>-bundle-approve одним draft-PR на всю волну; "
             "мерж — человеческий",
    )
    parser.add_argument(
        "--abandon-revision", type=int, default=None,
        help="перевести незавершённую ревизию переиздания в терминальный "
             "abandoned (требует --reason)",
    )
    parser.add_argument(
        "--reason", default=None,
        help="причина решения для --abandon-revision либо "
             "--replace-revision; флаг ОДИН на оба перехода, потому что "
             "они взаимоисключающи (гвард ниже) — двусмысленности нет, а "
             "в леджер причина ложится своим полем каждого перехода "
             "(reason / replacement_reason)",
    )
    parser.add_argument(
        "--replace-revision", type=int, default=None,
        help="явный replace-переход (только с --supersede): отозвать "
             "незамерженное предложение названной ревизии — новая "
             "ревизия несёт replaces_revision/replaces_pr, PR "
             "отозванной закрывается механикой, её ветка удаляется; "
             "требует --reason",
    )
    args = parser.parse_args(argv)
    if args.approve_node is not None:
        # Действие в прогоне ОДНО — тот же гвард и по той же причине, что
        # уже разводит остальные флаги между собой: молчаливая победа
        # одного решала бы за оператора, что он имел в виду.
        conflicting = [
            name for name, given in (
                ("--supersede", args.supersede),
                ("--conform-approve", args.conform_approve),
                ("--abandon-revision", args.abandon_revision is not None),
                ("--replace-revision", args.replace_revision is not None),
            ) if given
        ]
        if conflicting:
            parser.error(
                "--approve-node — отдельное действие, несовместимое с "
                + ", ".join(conflicting)
                + ": одобрение узла совершает человек, доставка его только "
                "проверяет — запускайте их отдельными прогонами"
            )
        # Вход — только node-id активного DAG, не путь: произвольный путь
        # позволил бы одобрить файл вне DAG, копию файла, файл чужого
        # бандла — то есть подписать то, чего активный граф не содержит.
        # Промах ловится на РАЗБОРЕ АРГУМЕНТОВ: ничего не пишется вовсе.
        allowed = _dag_files(_dag_for(args.legacy_bundle))
        if args.approve_node not in allowed:
            parser.error(
                f"--approve-node {args.approve_node!r}: не node-id активного "
                "DAG (approve есть акт о позиции в графе, а не о файле на "
                "диске); допустимые: " + ", ".join(allowed)
            )
    if args.abandon_revision is not None and args.supersede:
        # Спека этого сочетания не описывает, а прогон делает ОДНО
        # действие: молчаливая победа второго флага решала бы за
        # оператора, что он имел в виду (minor ревью Task 8).
        parser.error(
            "--supersede и --abandon-revision — разные действия: "
            "сначала абандоньте ревизию, затем запускайте переиздание"
        )
    if args.conform_approve and (
        args.supersede or args.abandon_revision is not None
    ):
        # Та же мотивировка, доведённая до конца (minor C-6): диспетчер
        # ниже проверяет --abandon-revision, затем --supersede, затем
        # --conform-approve, и первый сработавший молча съедал остальные.
        parser.error(
            "--conform-approve — третье отдельное действие: запускайте "
            "его отдельным прогоном, не вместе с --supersede/"
            "--abandon-revision"
        )
    if args.approval_pr is not None and not args.supersede:
        # Флаг осмыслен только внутри переиздания (§I7): без --supersede
        # он никуда не доходит, и выполнялась бы ОБЫЧНАЯ доставка — не
        # то, что просил оператор, и молча (minor C-7).
        parser.error(
            "--approval-pr осмыслен только с --supersede: подпись штампа "
            "берётся при переиздании"
        )
    if (
        args.replace_revision is not None
        and args.abandon_revision is not None
    ):
        # Та же мотивировка, что у пар выше: абандон и замена — разные
        # переходы над разными ревизиями, и молчаливая победа одного
        # решала бы за оператора, что он имел в виду. Гвард стоит ВЫШЕ
        # проверок `--reason`: без него сообщение об одном флаге
        # заслоняло бы то, что оператор попросил ДВА разных перехода —
        # и заодно он тот самый, который делает общий `--reason`
        # однозначным (переходы не сосуществуют, толковать нечего).
        parser.error(
            "--replace-revision и --abandon-revision — разные переходы: "
            "запускайте их отдельными прогонами"
        )
    if args.abandon_revision is not None and not args.reason:
        parser.error("--abandon-revision требует --reason")
    if args.replace_revision is not None and not args.supersede:
        # Замена — часть переиздания (порядок владельца: закрыть
        # предложение и доставить новое одним переходом), сама по себе
        # она ничего не доставляет. Без --supersede прогон выполнил бы
        # ОБЫЧНУЮ доставку и молча — не то, что просил оператор.
        parser.error(
            "--replace-revision осмыслен только с --supersede: замена "
            "снимает старое предложение и доставляет новое одним переходом"
        )
    if args.replace_revision is not None and not args.reason:
        # Причина обязательна (требование владельца): она уходит в
        # леджер полем `replacement_reason` и в комментарий закрываемого
        # PR — единственное место, где потом читается, почему
        # предложение отозвали.
        parser.error("--replace-revision требует --reason")
    if args.reason and (
        args.abandon_revision is None and args.replace_revision is None
    ):
        # Причина без перехода никуда не записывается. Молча съесть её
        # значило бы выполнить ОБЫЧНУЮ доставку под видом решения,
        # которое оператор обосновывал (та же мотивировка, что у
        # `--approval-pr` без `--supersede`).
        parser.error(
            "--reason осмыслен только с --abandon-revision либо "
            "--replace-revision: без перехода причину некуда записать"
        )
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
    if args.approve_node is not None:
        try:
            approve_node_for_run(
                state, ops, args.approve_node,
                legacy_bundle=args.legacy_bundle,
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        return 0
    if args.abandon_revision is not None:
        # Тот же fail-closed-контур, что у --supersede ниже: у оператора
        # бывает опечатка в номере и бывает уже завершённая ревизия (§I4:
        # не мутируется) — оба случая обязаны дать сообщение и ненулевой
        # RC, а не трейсбек (major ревью Task 8).
        try:
            _abandon_revision(state, args.abandon_revision, args.reason)
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        print(f"ревизия {args.abandon_revision} помечена abandoned")
        return 0
    if args.supersede:
        try:
            result = deliver_superseded(
                state, ops, legacy_bundle=args.legacy_bundle,
                approval_pr=args.approval_pr,
                replace=(
                    Replacement(args.replace_revision, args.reason)
                    if args.replace_revision is not None else None
                ),
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        # Печатаем ТОЛЬКО состоявшуюся доставку. `returned` и `noop` уже
        # сказали о себе точнее (какой PR и почему новой ревизии нет), а
        # строка «переизданная доставлена» поверх них противоречила бы им
        # и читалась бы последней — оператор шёл мержить чужой PR.
        if result.kind == "delivered":
            print(f"переизданная tasks-спека доставлена: PR #{result.pr}")
        return 0
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
        pr = deliver_for_run(state, ops, legacy_bundle=args.legacy_bundle)
    except RuntimeError as exc:
        print(f"task_bridge: {exc}")
        return 1
    print(f"draft tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
