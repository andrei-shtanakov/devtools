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
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from governance import acceptance_guard, decomposition_guard, design_guard
from governance.ops import Ops, RealOps
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


def _prospective_anchor(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None = None,
) -> str:
    """blob терминального узла ПОСЛЕ штампа, без записи в рабочее дерево.

    §I2 спеки: сравнивать надо те байты, что уйдут в PR и после мержа
    лягут в base, но штамп — эффект. Поэтому бандл копируется во временный
    каталог, штампуется ТАМ, и хеш берётся оттуда; рабочее дерево не
    трогается вовсе (проверяется тестом `..._writes_nothing`).
    """
    dag = _dag_for(legacy_bundle)
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
        )
        anchor_file = shadow / bundle_dir / dag[-1][0]
        return blob_sha1(anchor_file.read_text(encoding="utf-8"))


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
    branch: str | None = None,
    version: int = 1,
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

    Оба завершения op пишут `anchor` — blob терминального узла активного
    DAG после штампа (§I2). Без него `prev_op.get("anchor")` у v1 всегда
    `None`, и ПЕРВОЕ переиздание любого воркстрима уходит в
    compatibility-случай §6 вместо сверки §I5: `--supersede` сразу после
    обычной доставки завёл бы v2 с новой веткой и PR-ом вместо
    бесследного no-op. Источник значения у двух завершений разный —
    новая доставка знает фактический штамп изнутри (`after_commit`),
    реконсиляция читает доставленные байты из head-коммита PR-а
    (`_delivered_anchor`).

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
        """§I2: фактический штамп анкера — те байты, что ушли в PR.

        Хук — единственный момент, когда значение верно: до `deliver()`
        рабочее дерево ещё не синхронизировано с `base_ref`
        (`checkout_and_pull` живёт ВНУТРИ доставки), а после неё дерево
        уже проштамповано, и `_prospective_anchor` считал бы штамп
        поверх штампа. Колбэк ничего не пишет на диск и не может
        отказать — новых путей отказа обычная доставка не получает.
        """
        stamped["anchor"] = commit_facts["anchor_blob"]

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
    op_complete(state, "tasks-deliver", pr=pr, anchor=stamped.get("anchor"))
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


def _last_delivery(state: RunState) -> tuple[int, dict] | None:
    """Последняя ЗАВЕРШЁННАЯ доставка: старшая ревизия либо v1; None — их нет.

    Записи в статусе `abandoned` и `started` пропускаются: ни та, ни
    другая ничего не доставили. `started` — штатный след падения (§I4,
    write-ahead), и принять её за доставку значит сделать §I5 fail-open
    (у `started` нет `anchor`, сверка не срабатывает и переиздание идёт
    даже при неизменившемся апстриме), записать в журнал ложный
    `comparison: unavailable` и направить `supersedes` на недоставку.
    Счёт одинаков для ревизий и исторического ключа v1: `deliver_for_run`
    тоже ведёт свой op write-ahead (`op_start` ДО эффектов), так что
    упавшая ПЕРВАЯ доставка оставляет `tasks-deliver` в `started`.
    """
    for n, op in reversed(_revisions(state)):
        if op.get("status") == "completed":
            return n, op
    v1 = state.ops.get(_V1_KEY)
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


def _abandon_revision(state: RunState, n: int, reason: str) -> None:
    """Терминальный `abandoned` с причиной — причина хранится навсегда."""
    key = f"{_REVISION_PREFIX}{n}"
    if key not in state.ops:
        raise RuntimeError(f"ревизии {n} нет в леджере — нечего абандонить")
    if state.ops[key].get("status") == "completed":
        raise RuntimeError(
            f"ревизия {n} завершена (PR #{state.ops[key].get('pr')}) — "
            "завершённая запись не мутируется"
        )
    state.ops[key] = {**state.ops[key], "status": "abandoned", "reason": reason}
    save(state)


def _resolve_correction_pr(
    state: RunState,
    ops: Ops,
    anchor_rel: str,
    base_ref: str,
    approval_pr: int | None,
) -> tuple[int, str, str]:
    """PR, доставивший correction → (номер, approved_by, approved_at).

    §I7 спеки: подпись штампа берётся у correction-PR, а не у исходного
    бандл-PR — иначе штамп утверждает, что текущие байты одобрил человек,
    одобрявший другую версию. Ноль или больше одного кандидатов — отказ:
    гадать нельзя. `--approval-pr` заменяет ПОИСК (шаги 1–3), но не
    ПРОВЕРКУ (шаг 4).
    """
    if approval_pr is None:
        sha = ops.last_commit_touching(state.target_dir, anchor_rel)
        if sha is None:
            raise RuntimeError(
                f"{anchor_rel} не менялся в истории {base_ref} — "
                "correction не найден, подписи взять неоткуда"
            )
        candidates = [
            p for p in ops.prs_containing_commit(state.repo_slug, sha)
            if p.get("state") == "MERGED" and p.get("baseRefName") == base_ref
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"коммит {sha[:7]} связан с {len(candidates)} вмерженными "
                f"PR в {base_ref} — подписи взять неоткуда; назовите PR "
                "явно: --approval-pr <n>"
            )
        number = candidates[0]["number"]
        # Подпись — отдельным запросом: эндпоинт commits/<sha>/pulls отдаёт
        # merged_by: null даже у вмерженного PR (ревью #164).
        facts = ops.pr_facts(state.repo_slug, number)
        if facts.get("state") != "MERGED":
            raise RuntimeError(
                f"PR #{number} найден по коммиту, но его состояние "
                f"{facts.get('state')!r} — подписи взять неоткуда"
            )
        merged_by = (facts.get("mergedBy") or {}).get("login")
        merged_at = facts.get("mergedAt")
    else:
        number = approval_pr
        facts = ops.pr_facts(state.repo_slug, number)
        if facts.get("state") != "MERGED":
            raise RuntimeError(
                f"--approval-pr {number}: PR не вмержен "
                f"(state={facts.get('state')!r}) — проверка та же, что у "
                "автоматического поиска"
            )
        if facts.get("baseRefName") != base_ref:
            raise RuntimeError(
                f"--approval-pr {number}: нацелен в "
                f"{facts.get('baseRefName')!r}, а не в {base_ref!r}"
            )
        if anchor_rel not in ops.pr_files(state.repo_slug, number):
            # Третье условие шага 4 §I7, без которого флаг из «заменяет
            # поиск» превращался бы в «отключает проверку»: подпись
            # ЛЮБОГО вмерженного в base_ref PR уходила бы в штамп, и штамп
            # утверждал бы, что байты анкера одобрил человек, который их
            # не видел (major C-5 финального ревью). В автоматической
            # ветке это условие держится по построению: PR ищется среди
            # содержащих коммит самого анкера.
            raise RuntimeError(
                f"--approval-pr {number}: PR не менял {anchor_rel} — "
                "подписи взять неоткуда; флаг заменяет поиск (шаги 1–3), "
                "но не проверку"
            )
        merged_by = (facts.get("mergedBy") or {}).get("login")
        merged_at = facts.get("mergedAt")
    if not merged_by or not merged_at:
        raise RuntimeError(
            f"PR #{number}: нет mergedBy/mergedAt — подпись штампа неполна"
        )
    return number, merged_by, merged_at


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
    """
    recorded = prev_op.get("dag")
    if recorded:
        return tuple((f, tuple(u)) for f, u in recorded), "previous_delivery"
    present = {
        p.name for p in (Path(target_dir) / bundle_dir).iterdir()
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
    n: int, op: dict, base_sha: str, pr: int | None, facts: dict
) -> str:
    """Решение по таблице §I3 спеки. Возврат — имя перехода, не действие.

    Имя ветки НЕ доказывает принадлежность: под ним может лежать чужая
    работа, поэтому у каждого живого PR сверяется `headRefOid` с
    записанным `head_sha` намерения.

    Факты PR принимаются АРГУМЕНТОМ, а не запрашиваются: вызывающий цикл
    и так их получил, а функция запрашивала `find_pr` второй раз и
    `pr_facts` — дважды (минор C-8: четыре сетевых запроса к `gh` на
    ревизию вместо одного, лишний источник флака). Побочно решение по
    §I3 стало чистым — таблица проверяема без стабов ops.
    """
    branch = op.get("branch")
    pr_state = facts.get("state") if pr is not None else None
    if pr is not None and pr_state not in ("OPEN", "MERGED"):
        raise RuntimeError(
            f"PR #{pr} по ветке {branch} закрыт без мержа — ветка отклонена "
            "человеком; переиздание fail-closed"
        )
    if pr is not None and op.get("head_sha"):
        actual_head = facts.get("headRefOid")
        if actual_head and actual_head != op["head_sha"]:
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


def _recover_commit(state: RunState, ops: Ops, op: dict) -> None:
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

    Бросает только на несоответствии факта намерению: под именем ветки
    ревизии лежит чужая работа.
    """
    branch, head = op.get("branch"), op.get("head_sha")
    local = ops.rev_parse(state.target_dir, branch) if branch else None
    if head:
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


def deliver_superseded(
    state: RunState,
    ops: Ops,
    legacy_bundle: int | None = None,
    approval_pr: int | None = None,
) -> int | None:
    """Санкционированное переиздание tasks-спеки (спека 2026-09-09).

    Порядок: синхронизация base → реконсиляция незакрытой ревизии (§I3) →
    разбор PR первой доставки, если последняя доставка — она (§I3 для
    легаси-v1) → вывод/сверка активного DAG предыдущей доставки (§I8) →
    provenance correction-PR (§I7) → проспективный anchor (§I2) → сверка
    с записанным anchor'ом предыдущей доставки → намерение (§I4,
    write-ahead) → доставка в НОВУЮ ветку `spec/<ws-id>-tasks-v<N>`.

    Реконсиляция ТЕРМИНАЛЬНА для вызова во всех исходах, кроме
    `abandon_and_next`: `return_pr`/`complete` возвращают PR ревизии, а
    `continue` доводит ЕЁ ЖЕ доставку (в её ветке, по её намерению). Ниже
    реконсиляции код доходит только тогда, когда незакрытых ревизий нет —
    второй открытый PR не заводится ни при каком состоянии леджера (§I3).

    Возврат `None` — бесследный no-op (§I5): апстрим не менялся с
    прошлой доставки, run.json не трогается.
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
    anchor_rel = f"{state.bundle_dir}/{active[-1][0]}"

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
            break
        decision = _reconcile_revision(n, op, base_sha, pr, facts)
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
            print(
                f"ревизия {n} уже доставлена — PR #{pr}; новая ревизия не "
                "заводится"
            )
            return pr
        if decision == "complete":
            _complete_revision(
                state, n, pr=pr, anchor=op["prospective_anchor"],
                head_sha=op["head_sha"],   # непуст по гварду выше
            )
            # Доставка состоялась; нужно ли ещё одно переиздание — решает
            # следующий запуск по §I5.
            print(f"ревизия {n} доставлена ранее — PR #{pr} вмержен")
            return pr
        # "continue": возобновляем ревизию n в ЕЁ ветке, а не заводим новую.
        if _dag_for(legacy_bundle) != tuple(
            (f, tuple(u)) for f, u in op.get("dag", [])
        ):
            raise RuntimeError(
                f"ревизия {n} начата с другим составом DAG — повторите "
                "запуск с тем же --legacy-bundle, что и в её намерении"
            )
        _recover_commit(state, ops, op)   # бросает на чужом коммите/ветке
        if pr is not None:
            _complete_revision(
                state, n, pr=pr, anchor=op["prospective_anchor"],
                head_sha=op["head_sha"],   # непуст по гварду выше
            )
            print(f"ревизия {n} доставлена ранее — PR #{pr}")
            return pr
        # PR-а нет: доставка не дошла до последнего шага. Повторяем её
        # ДЕТЕРМИНИРОВАННО из намерения — байты те же, потому что
        # generated_at/version/branch зафиксированы в намерении, а
        # commit_paths на пустом индексе не создаёт второй коммит.
        _approval, approved_by, approved_at = _resolve_correction_pr(
            state, ops, anchor_rel, base_ref, op["approval_pr"]
        )
        pr = deliver(
            target_dir=state.target_dir,
            repo_slug=state.repo_slug,
            ws_id=state.ws_id,
            subject=state.subject,
            bundle_dir=state.bundle_dir,
            base_ref=base_ref,
            ops=ops,
            approved_by=approved_by,
            approved_at=approved_at,
            generated_at=op["expected_generated_at"],
            legacy_bundle=legacy_bundle,
            profile=state.profile,
            branch=op["branch"],
            version=op["tasks_version"],
            before_commit=_tasks_blob_cb(state, n),
            after_commit=_commit_facts_cb(
                state, ops, n, op["prospective_anchor"]
            ),
        )
        # head_sha уже записан колбэком durable — между коммитом и push.
        _complete_revision(
            state, n, pr=pr, anchor=op["prospective_anchor"],
        )
        return pr

    # ПОСЛЕ реконсиляции (дефект 3 ревью Task 7): она может перевести
    # `started` → `completed`, и тогда предыдущая доставка — именно та.
    prev = _last_delivery(state)
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
            return v1_pr

    dag, dag_source = _previous_dag(
        state, ops, prev_op, state.target_dir, state.bundle_dir, base_sha,
    )
    if dag is not None and dag != active:
        raise RuntimeError(
            "состав активного DAG отличается от предыдущей доставки — "
            "это не переиздание, а другая доставка"
        )
    approval, approved_by, approved_at = _resolve_correction_pr(
        state, ops, anchor_rel, base_ref, approval_pr
    )
    prospective = _prospective_anchor(
        state.target_dir, state.bundle_dir, approved_by, approved_at,
        legacy_bundle,
    )
    recorded_anchor = prev_op.get("anchor")
    if recorded_anchor is not None and recorded_anchor == prospective:
        print(
            "апстрим не менялся — переиздание не требуется "
            f"(anchor {prospective[:7]})"
        )
        return None

    n = _next_revision(state)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    version = _previous_tasks_version(state) + 1
    branch = f"spec/{state.ws_id}-tasks-v{n}"
    intent = {
        "branch": branch,
        "base_sha": base_sha,
        "prospective_anchor": prospective,
        "approval_pr": approval,
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
    }
    if recorded_anchor is None:
        # §6 спеки: сверка была невозможна — фиксируем это В ЖУРНАЛЕ.
        intent["comparison"] = "unavailable"
        print(
            "предыдущая доставка не записала anchor — сверка не "
            "производилась (comparison: unavailable)"
        )
    _start_revision(state, n, intent)
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=base_ref,
        ops=ops,
        approved_by=approved_by,
        approved_at=approved_at,
        generated_at=generated_at,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
        branch=branch,
        version=version,
        before_commit=_tasks_blob_cb(state, n),
        after_commit=_commit_facts_cb(state, ops, n, prospective),
    )
    # head_sha здесь НЕ пишется: он уже записан колбэком durable — между
    # коммитом и push (§I3), а не после создания PR.
    _complete_revision(state, n, pr=pr, anchor=prospective)
    return pr


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
             "леджере; равный anchor — успешный no-op без изменений",
    )
    parser.add_argument(
        "--approval-pr", type=int, default=None,
        help="явный correction-PR для подписи штампа, когда автоматика даёт "
             "ноль или несколько кандидатов; проверяется так же",
    )
    parser.add_argument(
        "--abandon-revision", type=int, default=None,
        help="перевести незавершённую ревизию переиздания в терминальный "
             "abandoned (требует --reason)",
    )
    parser.add_argument(
        "--reason", default=None, help="причина для --abandon-revision"
    )
    args = parser.parse_args(argv)
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
    if args.abandon_revision is not None and not args.reason:
        parser.error("--abandon-revision требует --reason")
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
            pr = deliver_superseded(
                state, ops, legacy_bundle=args.legacy_bundle,
                approval_pr=args.approval_pr,
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        if pr is None:
            return 0
        print(f"переизданная tasks-спека доставлена: PR #{pr}")
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
