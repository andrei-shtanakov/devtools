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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from governance import acceptance_guard, decomposition_guard, design_guard
from governance.approve_node import (
    approve_node,
    read_dag_state,
    reconcile_wave_after_approved_dag,
)
# Состав DAG живёт в `bundle_dag` (переезд ради §I12: механика одобрения
# спрашивает его, а импортировать мост нельзя — часть 3 сделает мост
# потребителем гейта). Алиасы сохраняют прежние имена, поэтому ни один
# вызов ниже и ни один тест не знают о переезде.
from governance.bundle_dag import (
    ANCHOR_NODE_ID as _ANCHOR_NODE_ID,
    # Оба кортежа переэкспортируются намеренно: код моста ходит через
    # `dag_for`, но состав DAG под прежними именами читают его тесты —
    # снять их значило бы спрятать переезд ценой характеризации.
    BUNDLE_DAG as _BUNDLE_DAG,  # noqa: F401
    BUNDLE_DAG_LEGACY5 as _BUNDLE_DAG_LEGACY5,  # noqa: F401
    check_bundle_composition as _check_bundle_composition,
    dag_for as _dag_for,
    node_id as _node_id,
)
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import Ops, RealOps
from governance.policy_sources import PREFLIGHT_PROCEDURE_HINT, target_profile_declares
from governance.run_state import RunState, load, op_complete, op_start, save
from governance.stale_adapter import blob_sha1

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


#: Ключи frontmatter, которые КАНОНИЧЕСКОЕ представление узла вырезает:
#: то, что описывает approval-ПРОЦЕСС, а не содержание (§I2, §I5).
#: `approved_by`/`approved_at` — кто и когда совершил акт; `version` —
#: какое это поколение акта (правка 2026-09-11). `status` НЕ вырезается и
#: не по недосмотру: он влияет на допуск доставки, §I5 стоит перед гейтом,
#: и откат узла в долг обязан двигать величину — иначе бесследный no-op
#: скрыл бы непройденный гейт.
_CANON_CUT_KEYS = ("approved_by", "approved_at", "version")

#: Отметка версии канонизации в самой величине. Часть значения, а не
#: соседнее поле: поле можно забыть записать, и тогда его отсутствие стало
#: бы неотличимо от отсутствия правила. Всё, что префикса не несёт, есть
#: v1 по построению — fail-closed, потому что любое иное чтение объявляет
#: несопоставимое сопоставимым.
_CANON_VERSION = "v2"


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

    Что НЕ вырезается: `status`. Он влияет на допуск доставки, а §I5
    стоит ПЕРЕД гейтом — вырежи его, и бесследный no-op скрыл бы
    непройденный гейт вместо того, чтобы дать ему отказать. `version`,
    наоборот, вырезается (правка 2026-09-11): поколение approval-акта
    содержанием апстрима не является, и пока оно входило в хеш,
    переодобрение тех же тел заводило ревизию на ровном месте.

    Величина несёт ОТМЕТКУ ЭПОХИ (`v2:<хеш>`): процедура менялась, и
    записи двух эпох несопоставимы — ни как равные, ни как содержательно
    разные (`_comparable_anchors`).
    """
    canon: dict[str, str] = {}
    lines: list[str] = []
    base = Path(target_dir) / bundle_dir
    for fname, upstream_ids in dag:
        meta, body = split_frontmatter(
            (base / fname).read_text(encoding="utf-8")
        )
        for key in _CANON_CUT_KEYS:
            meta.pop(key, None)
        if upstream_ids:
            meta["upstream_hashes"] = {u: canon[u] for u in upstream_ids}
        node_id = _node_id(fname)
        canon[node_id] = blob_sha1(join_frontmatter(meta, body))
        lines.append(f"{node_id} {canon[node_id]}")
    return f"{_CANON_VERSION}:{blob_sha1(chr(10).join(lines) + chr(10))}"


def _canon_epoch(value: str) -> str:
    """Эпоха канонизации величины — ЯВНО, а не побочным эффектом `split`.

    У v2 префикс есть, у v1 его нет вовсе, и `split(":", 1)[0]` на v1
    отдаёт весь хэш целиком. Сравнение таких «префиксов» отвечает не на
    вопрос об эпохе: две РАЗНЫЕ записи одной и той же v1 оно объявляло бы
    несопоставимыми, то есть подменяло «содержание изменилось» на «сверить
    не удалось». Сегодня обе ветки ведут к переизданию, и поведение от
    подмены не менялось, — врала диагностика, а расхождение между делом и
    рассказом о нём мы за этот прогон оплачивали дважды.

    Отсутствие префикса есть v1, и это то же fail-closed, что в самой
    отметке: «не помечено» читается как старейшая эпоха, а не как «наверное
    текущая».
    """
    return value.split(":", 1)[0] if ":" in value else "v1"


def _comparable_anchors(recorded: object, current: str) -> bool:
    """Сопоставимы ли записанный и текущий `content_anchor` (§I2, v1/v2).

    Правка канонизации 2026-09-11 (`version` ушёл в вырезаемые) сделала
    величины двух эпох НЕСОПОСТАВИМЫМИ — а это не то же самое, что
    «содержание различается». Объявить их равными нельзя, объявить
    содержательно разными — тоже: ни одно из двух утверждений не
    установлено, и притворяться, будто сверка состоялась, запрещено тем же
    правилом, которым свёрнутый исход не открывает дверь.

    Отметка эпохи живёт В САМОЙ ВЕЛИЧИНЕ (`v2:<хеш>`), поэтому запись без
    префикса есть v1 ВСЕГДА. Соседнее поле можно забыть записать, и его
    отсутствие стало бы неотличимо от отсутствия правила; префикс забыть
    нельзя — он часть того, что сравнивают.

    Исход одноразовый ПО ЗАПИСИ, а не по прогону: переиздание, прошедшее
    через `unavailable`, кладёт свой anchor уже v2, и следующая сверка
    обычная. Повтор упавшей доставки вечным `unavailable` это не делает —
    прежняя запись остаётся v1 ровно до тех пор, пока доставка не дошла до
    записи новой.
    """
    if not isinstance(recorded, str) or not recorded:
        return False
    return _canon_epoch(recorded) == _canon_epoch(current)


def _content_anchor(
    target_dir: str,
    bundle_dir: str,
    legacy_bundle: int | None = None,
) -> str:
    """`content_anchor` §I5: канонический хэш активного DAG в base.

    Отвечает на вопрос «менялось ли содержание апстрима», и только на
    него. Артефактный `anchor` (§I2) на него ответить не может: он —
    точный blob одного терминального узла, а вопрос про весь активный DAG.

    Считается по БАЙТАМ В BASE напрямую. Теневого проштампованного дерева
    здесь больше нет и быть не может: доставка бандла не касается вовсе
    (§I7), значит байты, с которыми она работает, — ровно те, что уже
    лежат в синхронизированном base, и «преобразования, которые уйдут в
    PR» — пустое множество. Прежняя редакция считала обе величины по
    теневой копии именно потому, что штамп эти байты менял; со снятием
    штампа исчезла и копия.

    §I5 от этого остаётся истинным, и это стоит проговорить: равенство
    по-прежнему означает «апстрим не менялся с прошлой доставки», потому
    что обе стороны сверки теперь считаются одним и тем же правилом по
    одному и тому же состоянию — base. Записи прежней эпохи, посчитанные
    по проштампованному дереву, с сегодняшними НЕ сойдутся, и это не
    ложное расхождение, а честное: те байты и правда были другими.
    Воркстрим такой эпохи переиздаётся один раз обычным путём и дальше
    живёт по общему правилу; отсутствие записи по-прежнему даёт
    `comparison: unavailable` (§6), а не выдуманное равенство.
    """
    return _canonical_dag_hash(
        target_dir, bundle_dir, _dag_for(legacy_bundle)
    )


def _prospective_anchor(
    target_dir: str,
    bundle_dir: str,
    legacy_bundle: int | None = None,
) -> str:
    """Артефактный anchor §I2: blob терминального узла активного DAG в base.

    Чтение, а не вычисление — по той же причине, что и у
    `content_anchor`: бандл доставка не меняет, значит байты анкера, с
    которыми она работает, уже лежат в base. Имя «проспективный»
    осталось от эпохи штампа, когда величину приходилось предсказывать по
    теневой копии; предсказывать больше нечего.

    Отметки эпохи В ЗНАЧЕНИИ у этой величины НЕТ, и это не асимметрия с
    `content_anchor`, а разница предметов: тот же блоб уходит ПИНОМ в
    саму tasks-спеку (`upstream_hashes`), и префикс в нём испортил бы
    артефакт, который читает spec-runner. Смысл величины тоже сменился со
    штампом (был блоб проштампованного узла, стал блоб узла в base),
    поэтому эпоха у неё распознаётся ГВАРДОМ — по полям намерения, а не по
    форме значения (`_require_resumable_epoch`).
    """
    dag = _dag_for(legacy_bundle)
    path = Path(target_dir) / bundle_dir / dag[-1][0]
    return blob_sha1(path.read_text(encoding="utf-8"))


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
    generated_at: str | None = None,
    legacy_bundle: int | None = None,
    profile: str | None = None,
    branch: str | None = None,
    version: int = 1,
    expected_base_sha: str | None = None,
    carry_from: str | None = None,
    before_commit: Callable[[dict], None] | None = None,
    after_commit: Callable[[dict], None] | None = None,
) -> int:
    """Пишет spec/<ws-id>-tasks.md и открывает один draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.

    Порядок «чтение анкера → рендер tasks» прежний по смыслу, хотя штампа
    в нём больше нет: пин обязан считаться с тех байтов, что лежат в base
    к моменту доставки. Раньше между ними стоял штамп
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

    УДАЛЕНО вместе со штампом (§I7): `approved_by`/`approved_at`,
    `restamp_nodes`, `--approval-pr`. Доставка бандла не касается вовсе —
    ни одного его файла нет в её коммите, и подписи она не ставит.
    Исторически здесь было: множество node-id,
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
      `push_branch` с `head_sha` и `anchor_blob` (фактический блоб
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
    if expected_base_sha is not None:
        # Гейт §I12 обязан судить ТО ЖЕ состояние base, которое уйдёт в
        # доставку. Между его чтением и этим pull'ом база могла проехать —
        # и тогда гейт судил не то, что доставляют: в одну сторону он
        # пропустил бы DAG, который уже откатился в долг, в другую отказал
        # бы по состоянию, которого в base больше нет. Пин закрывает окно
        # положительной сверкой, а не надеждой на то, что между двумя
        # чтениями ничего не произошло.
        actual = ops.rev_parse(target_dir, base_ref)
        if actual != expected_base_sha:
            raise RuntimeError(
                f"база {base_ref} уехала между гейтом и доставкой: гейт "
                f"судил {expected_base_sha}, сейчас {actual}. Ничего не "
                "создано; повторите вызов — гейт пересчитается по "
                "актуальной базе"
            )
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
    # доставки: отказ (невалидный граф DT) не должен
    # оставлять target на чужой ветке с незакоммиченными правками —
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
    # Анкер — терминальный узел АКТИВНОГО DAG (не хардкод design/behaviour).
    # Читается как есть: доставка бандла НЕ КАСАЕТСЯ (§I7), поэтому байты
    # анкера — ровно те, что лежат в base, и «стухнуть» им уже не от чего.
    # design_text
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
        # парсинг тела DT-задач и джойн BEH →
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
    # Коммитом уходит РОВНО tasks-спека: файлы бандла в diff доставки не
    # входят вовсе (§I7). Это и есть наблюдаемое следствие, по которому
    # снаружи видно, что доставка проверяет одобренность, а не создаёт её.
    ops.commit_paths(
        target_dir,
        [rel],
        f"spec: {ws_id} tasks (draft) (fleet-agent)",
    )
    if after_commit is not None:
        # Между коммитом и push (§I3): падение здесь оставляет коммит
        # опознаваемым. `anchor_blob` — ФАКТИЧЕСКИЙ блоб терминального
        # узла, тот же, что ушёл в пин спеки.
        after_commit({
            "head_sha": ops.rev_parse(target_dir, "HEAD"),
            "anchor_blob": design_blob,
        })
    ops.push_branch(target_dir, branch)
    body = (
        f"Draft tasks.md-спека из behaviour-spec бандла {ws_id} "
        f"({bundle_dir}/15-behaviour-spec.md), сгенерирована task_bridge.\n\n"
        "Файлы бандла этот PR не трогает: одобренность узлов доставка "
        "только ПРОВЕРЯЕТ (§I7, §I12), а одобряет их человек мержем "
        "candidate-PR.\n\n"
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

    Реконсиляция принимает доставку, которую этот вызов НЕ делал: её
    байты лежат в коммите PR-а, а после мержа лягут в base. Значит anchor
    не надо пересчитывать — его надо ПРОЧИТАТЬ там, где он существует.

    Пересчитать его по сегодняшнему дереву было бы НЕВЕРНО: считался бы
    ТЕКУЩИЙ
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
    там, где оно существует. Текущий base сюда не годится по другой
    причине, чем раньше: со снятием штампа байты бандла доставка не
    меняет вовсе, зато апстрим мог уехать вперёд ПОСЛЕ той доставки — и
    §I5 объявил бы «содержание не менялось» по состоянию, которого та
    доставка не видела.

    Читается СОДЕРЖИМОЕ (`ops.show_file`), а не blob-хеши: канонизация
    работает с текстом frontmatter, хеша ей мало.

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


def _approved_dag_or_refuse(
    state: RunState, ops: Ops, legacy_bundle: int | None
) -> str:
    """Гейт §I12 и реконсиляция волны — общее начало трёх доставок.

    Доставок ровно три, и контракт называет их поимённо: первая
    (`deliver_for_run`), `--supersede` и `--replace-revision` (обе
    последние входят через `deliver_superseded`). `--conform-approve`
    сюда НЕ входит — он нормализует frontmatter tasks-спеки и узлов
    бандла не читает.


    Нормативный порядок §I2 целиком, и границу между шагами 2 и 3 стирать
    нельзя:

    1. гейт зовёт `read_dag_state` — И ТОЛЬКО ЕГО. Предикат здесь не
       применяется и второй его редакции не заводится: понадобится
       изменить, что считается честной одобренностью, — менять надо одно
       место, и оба потребителя обязаны прочитать новое без правки у
       себя;
    2. не сошёлся — отказ БЕЗ единой записи. Диагностика собирается из
       того, что вернул гейт: каждый непроходящий узел со статусом и
       процедурой, а неустановленный факт — отдельной формулировкой, без
       приписывания причины;
    3. сошёлся — реконсилируется открытая волна, и только ПОСЛЕ её
       durable-записи начинаются delivery-эффекты.

    Совпадение intent частью approval-гейта НЕ является: гейт судит о DAG
    (одобрены ли узлы), реконсиляция — о записи прежнего прохода (чем он
    кончился). Разные предметы, разное время, и статус волны в решении о
    допуске не участвует вовсе — доставка над воркстримом без единой
    записи волны проходит ровно так же.

    Права одобрять доставке это не возвращает: она ЧИТАЕТ чужой предикат
    и записывает вывод из него. Записать вывод — бухгалтерия, а не
    одобрение; §I7 запрещает первое и о втором не говорит.

    Возвращает SHA базы, КОТОРУЮ СУДИЛ, — доставка пинуется им. Гейт
    читает узлы в base (`git show <base>:<путь>`), а доставка читает
    рабочее дерево после своего `checkout_and_pull`; пока между ними может
    проехать pull, гейт судит не то, что доставляют.

    Неизвестная база — отказ, а не пустой пин: `deliver()` пропускает
    сверку при `None`, то есть молчащий `rev_parse` СНИМАЛ бы защиту
    ровно в тот момент, когда о состоянии базы ничего не известно
    (fail-open, минор ревью #191, круг 2). Поэтому возврат — `str`, и
    отсутствие SHA терминально здесь.
    """
    verdict = read_dag_state(state, ops, _dag_for(legacy_bundle))
    if verdict.evidence is None:
        if verdict.unresolved:
            raise RuntimeError(
                "состояние активного DAG не установлено, доставка не "
                f"начата: {verdict.unresolved}. Ничего не записано; "
                "повторите вызов"
            )
        raise RuntimeError(
            "активный DAG не одобрен целиком — доставка не начата "
            "(§I12; одобрение узла совершает человек мержем "
            "candidate-PR):\n"
            + "\n".join(f"- {debt.render()}" for debt in verdict.debts)
        )
    reconcile_wave_after_approved_dag(state, verdict.evidence)
    # SHA базы, которую гейт СУДИЛ: доставка пинуется им и отказывает, если
    # между двумя чтениями база проехала.
    base_ref = state.base_ref or "master"
    judged = ops.rev_parse(state.target_dir, base_ref)
    if judged is None:
        raise RuntimeError(
            f"rev_parse {base_ref} в {state.target_dir!r} не дал SHA — "
            "база, которую судил гейт §I12, неизвестна, и запинить "
            "доставку нечем; доставка не начата"
        )
    return judged


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

    - `anchor` — точный blob терминального узла в base (§I2):
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
    # Гард грязного дерева стоит ПЕРЕД pull'ом, а не после (минор ревью
    # #191, круг 2): `checkout_and_pull` по грязному target_dir — сам по
    # себе эффект, и он либо упирается в конфликт посреди чекаута, либо
    # уносит чужую незакоммиченную работу. Тот же порядок у переиздания
    # (`deliver_superseded`), и расходиться им незачем: гард внутри
    # `deliver()` ниже стоит позади СВОЕГО pull'а и этот, первый, не
    # прикрывает.
    if ops.is_dirty(state.target_dir):
        raise RuntimeError(
            f"target_dir {state.target_dir!r} грязный — доставка не начата"
        )
    # База освежается ПЕРЕД гейтом: он читает узлы в base
    # (`git show <base>:<путь>`), и на протухшем клоне судил бы состояние,
    # которого в репозитории уже нет. Сам себя такой вызов не лечил бы —
    # он отказывает раньше, чем дошёл бы до единственного pull'а внутри
    # доставки. Порядок тот же, что у переиздания и у `--approve-node`.
    ops.checkout_and_pull(state.target_dir, state.base_ref or "master")
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
    # Фактов мержа бандл-PR здесь больше не спрашивают: мерж бандл-PR
    # одобрением НЕ является (§I7, перечень «что одобрением не
    # является»). Решение devtools#110, приравнивавшее инициированный
    # мерж к approve, пересмотрено: мерж вносит байты бандла в base и
    # только. Одобряет узел человек мержем candidate-PR, и гейт выше уже
    # проверил, что это состоялось.
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
        который потом нечем было бы объяснить. На диск колбэк не пишет
        ничего — обе величины читаются из дерева, которое доставка не
        меняет (бандла она не касается вовсе, §I7).
        """
        stamped["anchor"] = commit_facts["anchor_blob"]
        stamped["content_anchor"] = _content_anchor(
            state.target_dir, state.bundle_dir, legacy_bundle
        )

    # Гейт §I12 — непосредственно перед ПЕРВЫМ delivery-эффектом, а не
    # выше идемпотентного шортката: он защищает доставку, а не отчёт о
    # ней. Стой он раньше, повтор кнопки после correction'а отказывал бы
    # там, где доставка давно состоялась, а прогон, упавший между
    # созданием PR и записью его номера, не смог бы дописать номер в
    # леджер — обе ветки выше уже вернулись бы.
    #
    # Его отказ на свежем воркстриме штатен: бандл лежит в base целиком
    # `draft`, потому что штамповать его больше некому. Это новый порядок
    # работы («сгенерировать DAG → одобрить узлы топологически →
    # deliver_for_run»), а не регресс.
    judged_base = _approved_dag_or_refuse(state, ops, legacy_bundle)
    op_start(state, "tasks-deliver")
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=state.base_ref or "master",
        ops=ops,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
        expected_base_sha=judged_base,
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


def _require_resumable_epoch(n: int, op: dict) -> None:
    """Намерение ЧУЖОЙ эпохи не возобновляется — отказ ДО единого эффекта.

    `prospective_anchor` сменил смысл вместе со снятием штампа: был блоб
    проштампованного терминального узла, стал блоб узла в base. Ревизия,
    записанная прежней эпохой и оставшаяся `started`, при возобновлении
    даёт ГАРАНТИРОВАННОЕ расхождение — и до этой правки объясняла его
    неверно («апстрим двигали во время доставки»), причём уже ПОСЛЕ
    коммита и без процедуры.

    Эпоха распознаётся ПОЛОЖИТЕЛЬНО — по полям, которых у нынешних
    намерений нет вовсе (`approval_pr`, `signed_nodes`): они несли
    провенанс штампа и исчезли вместе с ним. Это не догадка по форме
    значения и не «наверное старое»: поле либо записано, либо нет.
    Распознаватель назвал сам §I4, и процедуру тоже — свернуть ревизию
    явно и запустить переиздание заново.

    Отметки в значении здесь быть не может: тот же блоб уходит пином в
    tasks-спеку, и префикс испортил бы артефакт.
    """
    epoch_fields = [f for f in ("approval_pr", "signed_nodes") if f in op]
    if not epoch_fields:
        return
    raise RuntimeError(
        f"ревизия {n} записана эпохой штампа (намерение несёт "
        f"{', '.join(epoch_fields)}): её prospective_anchor посчитан по "
        "ПРОШТАМПОВАННОМУ дереву, а нынешний — по байтам base, и сойтись "
        "они не могут. Возобновлять такое намерение нельзя: продолжить "
        "значит доставить штамп, которого контракт больше не разрешает, а "
        "не продолжить — разойтись с записанным anchor'ом. Процедура: "
        f"--abandon-revision {n} --reason \"намерение эпохи штампа\", "
        "затем обычный --supersede"
    )


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
        if status == "started":
            _require_resumable_epoch(n, op)
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
        # §I7: состав подписываемых узлов берётся ИЗ НАМЕРЕНИЯ, а не
        # пересчитывается. Проспективный anchor ревизии посчитан с ним же,
        # и разойдись они — доставка упёрлась бы в гард §I2 на собственном
        # повторе. Намерения, записанные до поузлового §I7, состава не
        # несут: для них — фактический состав correction-PR.
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
            generated_at=op["expected_generated_at"],
            legacy_bundle=legacy_bundle,
            profile=state.profile,
            branch=op["branch"],
            version=op["tasks_version"],
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
    comparable = _comparable_anchors(recorded_content, content)
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
        and comparable
        and recorded_content == content
    ):
        print(
            "апстрим не менялся — переиздание не требуется "
            f"(content_anchor {content[:7]})"
        )
        return SupersedeResult("noop")

    # Гейт §I12 — ПОСЛЕ §I5 и до записи ревизии: там, где доставки не
    # будет, защищать нечего, поэтому воркстрим с неодобренными узлами и
    # неизменившимся апстримом завершается бесследным no-op'ом выше, а не
    # отказом. Здесь же — первая проверка переиздания, способная отказать.
    judged_base = _approved_dag_or_refuse(state, ops, legacy_bundle)

    prospective = _prospective_anchor(
        state.target_dir, state.bundle_dir, legacy_bundle
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
    if not comparable:
        # §6 спеки: сверка была невозможна — фиксируем это В ЖУРНАЛЕ.
        # Случая два, и оба дают ОДИН исход: величины нет вовсе либо она
        # посчитана канонизацией другой эпохи. Ни в одном из двух сверки
        # не было, и притворяться, будто она состоялась, нельзя ни в одну
        # сторону. `anchor` записи старого образца задним числом тоже не
        # переосмысливается — он отвечал на другой вопрос (§I2).
        intent["comparison"] = "unavailable"
        # Причина выводится ТЕМ ЖЕ правилом, что и сопоставимость: иначе
        # рядом с верным решением поселится неверное объяснение. Эпохи
        # называются обе — как обе величины у разошедшегося пина.
        if not isinstance(recorded_content, str) or not recorded_content:
            why = "предыдущая доставка не записала content_anchor"
        else:
            why = (
                "её content_anchor посчитан канонизацией эпохи "
                f"{_canon_epoch(recorded_content)}, текущая — "
                f"{_canon_epoch(content)}"
            )
        print(f"сверка §I5 не производилась (comparison: unavailable): {why}")
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
        generated_at=generated_at,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
        branch=branch,
        version=version,
        expected_base_sha=judged_base,
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
    state: RunState,
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
    target_dir = state.target_dir
    repo_slug = state.repo_slug
    ws_id = state.ws_id
    bundle_dir = state.bundle_dir
    dag = _dag_for(legacy_bundle)
    _check_bundle_composition(target_dir, bundle_dir, dag)
    # Гейта §I12 здесь НЕТ, и это не упущение. Контракт перечисляет
    # гейтируемые пути поимённо — «первая доставка, `--supersede`,
    # `--replace-revision`» (§I12, таблица) — и отдельно предупреждает не
    # путать с `--conform-approve`: тот нормализует frontmatter
    # TASKS-СПЕКИ по штампу владельца и узлов бандла не читает вовсе.
    # Гейт судит об одобренности узлов DAG; нормализация спеки к этому
    # предмета не имеет, а поставленный сюда он запирал бы приведение
    # спеки в порядок долгом совсем другого артефакта.
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
        "--approve-node", default=None, metavar="NODE-ID",
        help="одобрение узла бандла — человеческий акт (§I12): вынести "
             "узел активного DAG на одобрение candidate-PR-ом, а после "
             "мержа этого PR человеком записать подпись финализирующим "
             "PR-ом. Принимается ТОЛЬКО node-id активного DAG (не путь): "
             "approve есть акт о позиции в графе, а не о файле на диске. "
             "Повтор над честно одобренным узлом — бесследный no-op; "
             "approved-узел с разошедшимися пинами — отказ",
    )
    parser.add_argument(
        "--supersede", action="store_true",
        help="переиздать tasks-спеку после correction'а апстрима: новая "
             "ветка spec/<ws-id>-tasks-v<N>, новый PR, отдельная ревизия в "
             "леджере; неизменившееся содержание апстрима "
             "(content_anchor) — успешный no-op без изменений",
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
    others = {
        "--supersede": args.supersede,
        "--conform-approve": args.conform_approve,
        "--abandon-revision": args.abandon_revision is not None,
        "--replace-revision": args.replace_revision is not None,
    }
    if args.approve_node is not None and any(others.values()):
        # Действие в прогоне ОДНО, и гвард тот же, что уже разводит между
        # собой остальные флаги, — по той же причине: молчаливая победа
        # одного решала бы за оператора, что он имел в виду. Одобрение
        # узла к тому же ничего не доставляет, и съесть его доставкой
        # значило бы выполнить не ту работу молча.
        parser.error(
            "--approve-node — отдельное действие: запускайте его "
            "отдельным прогоном, не вместе с "
            + ", ".join(name for name, used in others.items() if used)
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
        # Механика целиком в `approve_node`; здесь обвязка и диагностика.
        # Подпись узла берётся из фактов мержа candidate-PR, а не из
        # активного логина и не из аргумента: логин, который команда
        # сообщает о себе сама, не проверяем никем (§I12).
        try:
            outcome = approve_node(
                state, ops, args.approve_node,
                legacy_bundle=args.legacy_bundle,
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        print(outcome.message)
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
        try:
            pr = deliver_conform(
                state, ops, legacy_bundle=args.legacy_bundle
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
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
