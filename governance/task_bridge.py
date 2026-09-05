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

from governance import design_guard
from governance.ops import Ops, RealOps
from governance.policy_sources import PREFLIGHT_PROCEDURE_HINT, target_profile_declares
from governance.run_state import load
from governance.stale_adapter import blob_sha1

# DAG бандла в порядке штампа (топологический): каждый узел перечисляет
# node-id своих upstream'ов; штамп идёт по порядку тюпла, и пин(ы) узла
# пересчитываются ПОСЛЕ штампа ВСЕХ его upstream-файлов (иначе пин
# протухает в момент записи). design — единственный узел с ДВУМЯ
# upstream-пинами (Task 5 плана design-узла).
_BUNDLE_DAG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
)


def _node_id(filename: str) -> str:
    """Имя файла бандла → node-id (числовой префикс и `.md` отрезаны)."""
    return filename.rsplit(".", 1)[0].split("-", 1)[1]


# Якорный узел моста — терминальный узел DAG (design). Выводится из
# _BUNDLE_DAG, а не хардкодится второй раз (Task 6): смена терминального
# узла бандла — правка одной строки DAG, не поиск по файлу.
_ANCHOR_FILENAME = _BUNDLE_DAG[-1][0]
_ANCHOR_NODE_ID = _node_id(_ANCHOR_FILENAME)

# Легаси-режим (Task 7 плана design-узла): бандлы, авторенные до раскатки
# design-узла, несут только эти три файла. `--legacy-bundle` усекает DAG до
# этого префикса — терминальный узел становится behaviour-spec, 20-design.md
# не читается вовсе (его в бандле нет по построению, не по ошибке).
_BUNDLE_DAG_LEGACY: tuple[tuple[str, tuple[str, ...]], ...] = _BUNDLE_DAG[:3]
_LEGACY_ANCHOR_FILENAME = _BUNDLE_DAG_LEGACY[-1][0]
_LEGACY_ANCHOR_NODE_ID = _node_id(_LEGACY_ANCHOR_FILENAME)


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

_BEH_HEADER = re.compile(r"^####\s+(BEH-\d+):\s*(.+?)\s*$")
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

    Потребляет ``design_guard.parse_design_resolutions`` (Task 4) — не
    рукописный пересказ, а генерация из фактического 20-design.md.
    resolved несёт обоснование (по DSL — первый непустой абзац блока;
    строка ``reason:`` тоже принимается, если встретится); deferred несёт
    причину явным префиксом. Пустой набор
    резолюций (design без единого блока ``#### Q-NN``) — секция не
    рендерится вовсе.
    """
    resolutions = design_guard.parse_design_resolutions(design_text)
    if not resolutions:
        return []
    lines = ["## Решения открытых вопросов (уровень design)", ""]
    for qid, (state, reason) in resolutions.items():
        if state == "deferred":
            lines.append(f"- **{qid} (deferred):** reason: {reason}")
        elif reason:
            lines.append(f"- **{qid}:** {reason}")
        else:
            lines.append(f"- **{qid}:** resolved")
    lines.append("")
    return lines


def render_tasks(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    generated_at: str,
    design_blob: str,
    design_text: str = "",
    anchor_node_id: str = _ANCHOR_NODE_ID,
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

    Якорь traces_to/upstream_hashes — design (Task 5 плана design-узла):
    design пинует ОБА upstream (requirements, behaviour-spec), так что
    один пин design транзитивно покрывает весь бандл — traces_to дальше
    ведёт на behaviour-spec незачем.

    `anchor_node_id` — Task 7 (легаси-режим): вызывающий (`deliver`) передаёт
    `behaviour-spec` вместо дефолтного `design`, когда `legacy_bundle=True`
    (3-узловой бандл без design) — сам рендер об этом режиме не знает,
    только про то, ЧТО именно является якорем.
    """
    lines = [
        "---",
        "spec_stage: tasks",
        "status: draft",
        "version: 1",
        "generated_by: fleet-agent",
        f"generated_at: {generated_at}",
        'source_prompt_version: ""',
        'validation: ""',
        'approved_by: ""',
        # Форма активного governance-профиля сразу при рождении (урок 1
        # ретроспективы): traces_to/upstream_hashes переживают `spec
        # approve` (он мержит traces и не трогает существующий пин), так
        # что рукам после approve остаётся только нормализация
        # `--conform-approve`.
        "traces_to:",
        f"- {anchor_node_id}",
        "upstream_hashes:",
        f"  {anchor_node_id}: {design_blob}",
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
            f"**Traces to:** [{', '.join(traces)}]" if traces else "",
            "",
        ]
    return "\n".join(line for line in lines if line is not None) + "\n"


def stamp_bundle_approved(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: bool = False,
) -> list[str]:
    """Штамп статусов вмерженного бандла + перепиновка цепочки; → rel-пути.

    `legacy_bundle=True` (Task 7) усекает обход до 3-узлового префикса DAG
    (`_BUNDLE_DAG_LEGACY`) — бандлы, авторенные до раскатки design-узла, не
    несут 20-design.md вовсе, и штамп не должен его искать. Без флага на
    таком бандле — явный RuntimeError (текст называет файл и обе процедуры:
    доавторить design ЛИБО передать `--legacy-bundle`), а не сырой
    traceback от `path.read_text()` на отсутствующем файле.

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
    changed: list[str] = []
    stamped_blobs: dict[str, str] = {}
    base = Path(target_dir) / bundle_dir
    dag = _BUNDLE_DAG_LEGACY if legacy_bundle else _BUNDLE_DAG
    for name, upstream_ids in dag:
        path = base / name
        if not path.exists():
            raise RuntimeError(
                f"{path} не найден — штамп бандла остановлен. Либо "
                "доавторьте design (узел `design` профиля team-exp, "
                "`make behaviour-tasks` ждёт вмерженный 20-design.md), "
                "либо, если бандл легаси (авторен до раскатки design-узла "
                "и design туда не входит), передайте `--legacy-bundle` — "
                "штамп пойдёт по 3-узловому префиксу "
                "charter→requirements→behaviour-spec"
            )
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
    legacy_bundle: bool = False,
) -> bool:
    """Нормализация frontmatter tasks-спеки ПОСЛЕ `spec approve` владельца.

    Якорь — терминальный узел `_BUNDLE_DAG` (design, Task 6): не
    хардкодится второй раз, выводится из DAG (`_ANCHOR_NODE_ID` /
    `_ANCHOR_FILENAME`), так что смена терминального узла бандла правит
    DAG в одном месте, не эту функцию. Нормализация возвращает форму
    активного governance-профиля: traces_to ровно [<anchor>], пин — на
    ТЕКУЩИЙ blob вмерженного файла анкера (independent от того, что туда
    дописал/недописал `spec approve` — lite-профиль spec-runner не знает
    про наш DAG). Строгий run проверяет только status — правка
    безопасна. Возвращает, менялся ли файл.

    `legacy_bundle=True` (Task 7): якорь — терминальный узел УСЕЧЁННОГО
    DAG (`behaviour-spec`, `_LEGACY_ANCHOR_NODE_ID`/`_LEGACY_ANCHOR_FILENAME`)
    — 20-design.md не читается вовсе, того файла в легаси-бандле нет.
    Отсутствие файла-анкера (design без флага на легаси-бандле) — тот же
    явный RuntimeError, что у `stamp_bundle_approved`, не сырой traceback.
    """
    anchor_filename = _LEGACY_ANCHOR_FILENAME if legacy_bundle else _ANCHOR_FILENAME
    anchor_node_id = _LEGACY_ANCHOR_NODE_ID if legacy_bundle else _ANCHOR_NODE_ID
    rel = Path(target_dir) / "spec" / f"{ws_id}-tasks.md"
    meta, body = split_frontmatter(rel.read_text(encoding="utf-8"))
    if meta.get("status") != "approved":
        raise RuntimeError(
            f"{rel.name}: status={meta.get('status')!r} — нормализация идёт "
            "ПОСЛЕ человеческого `spec approve` (инвариант №4), сначала он"
        )
    anchor = Path(target_dir) / bundle_dir / anchor_filename
    if not anchor.exists():
        raise RuntimeError(
            f"{anchor} не найден — нормализация остановлена. Либо "
            "доавторьте design (узел `design` профиля team-exp), либо, "
            "если бандл легаси (design туда не входит), передайте "
            "`--legacy-bundle` — якорь станет behaviour-spec"
        )
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
    legacy_bundle: bool = False,
    profile: str | None = None,
) -> int:
    """Штампует бандл + пишет spec/<ws-id>-tasks.md; один draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.

    Порядок «штамп бандла → пин design → рендер tasks» жёсткий: штамп
    меняет байты 20-design.md (терминальный узел `_BUNDLE_DAG`), и пин,
    взятый до штампа, протух бы в том же PR
    (@id:spec-bridge-approve-conformance).

    `legacy_bundle=True` (Task 7): бандл без узла design (авторен до
    раскатки design-узла) — штамп и якорь идут по 3-узловому префиксу DAG
    (`behaviour-spec`), 20-design.md не читается вовсе.

    `profile` (Task 8, опционально): путь профиля относительно
    `target_dir` — тот же, что получит `gate_check_candidate` в раннере
    (`state.profile`), не захардкоженный `profiles/team-exp.yaml`. Когда
    передан и `legacy_bundle=False`, доставка отказывает, если
    ФАКТИЧЕСКИЙ профиль target-репо не декларирует узел `design` — та же
    процедура, что у `stopped_preflight` раннера
    (`governance.policy_sources.target_profile_declares`): соседний репо
    может нести старую копию файла того же имени без design. `profile=None`
    (дефолт) — проверка пропускается; CLI (`main`) всегда передаёт
    `state.profile`.
    """
    if ops.is_dirty(target_dir):
        raise RuntimeError(
            f"target_dir {target_dir!r} грязный — доставка спеки не начата"
        )
    ops.checkout_and_pull(target_dir, base_ref)
    # Существование и чтение бандла — строго ПОСЛЕ чекаута базы (приёмка
    # PR #96, major): до него чекаут мог стоять на произвольной ветке, и
    # спека сгенерировалась бы из невмерженной ревизии бандла. Тот же
    # порядок — для гарда design ниже (инвариант приёмки PR #96, Task 7):
    # ПОСЛЕ checkout_and_pull, ДО ensure_branch.
    behaviour = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
    if not behaviour.exists():
        raise RuntimeError(
            f"{behaviour} не найден на {base_ref} — бандл не вмержен "
            "или путь неверен"
        )
    design_path = Path(target_dir) / bundle_dir / _ANCHOR_FILENAME
    if not legacy_bundle:
        if not design_path.exists():
            raise RuntimeError(
                f"{design_path} не найден на {base_ref} — доставка "
                "остановлена. Либо доавторьте design (узел `design` "
                "профиля team-exp) до доставки задач, либо, если бандл "
                "легаси (design туда не входит), передайте "
                "`--legacy-bundle`"
            )
        # Preflight (Task 8): та же проверка, что стопит раннер
        # `stopped_preflight`'ом — target-профиль может не декларировать
        # design вовсе (старая копия того же имени у соседнего репо), и
        # доставка не имеет права молча анкериться на design, которого
        # активный профиль этого репо не признаёт.
        if profile is not None and not target_profile_declares(
            target_dir, profile, "design"
        ):
            raise RuntimeError(
                f"{Path(target_dir) / profile} не декларирует узел "
                f"'design' — {PREFLIGHT_PROCEDURE_HINT}"
            )
    branch = f"spec/{ws_id}-tasks"
    ops.ensure_branch(target_dir, branch)
    stamped = stamp_bundle_approved(
        target_dir, bundle_dir, approved_by, approved_at,
        legacy_bundle=legacy_bundle,
    )
    if legacy_bundle:
        # Якорь — behaviour-spec (терминальный узел усечённого DAG): design
        # в легаси-бандле нет вовсе, секция резолюций не рендерится.
        anchor_node_id = _LEGACY_ANCHOR_NODE_ID
        behaviour_text = behaviour.read_text(encoding="utf-8")
        design_text = ""
        design_blob = blob_sha1(behaviour_text)
        scenarios = parse_behaviour(behaviour_text)
    else:
        anchor_node_id = _ANCHOR_NODE_ID
        # design — терминальный узел _BUNDLE_DAG: читаем ПОСЛЕ штампа, иначе
        # пин и текст резолюций взяты из уже стухшего blob'а.
        design_text = design_path.read_text(encoding="utf-8")
        design_blob = blob_sha1(design_text)
        scenarios = parse_behaviour(behaviour.read_text(encoding="utf-8"))
    stamp = generated_at or datetime.now().isoformat(timespec="seconds")
    text = render_tasks(
        ws_id=ws_id,
        subject=subject,
        bundle_path=f"{bundle_dir}/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at=stamp,
        design_blob=design_blob,
        design_text=design_text,
        anchor_node_id=anchor_node_id,
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
            + (
                "charter→requirements→behaviour-spec (легаси-бандл, "
                "--legacy-bundle)"
                if legacy_bundle
                else "charter→requirements→behaviour-spec→design"
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


def deliver_conform(
    target_dir: str,
    repo_slug: str,
    ws_id: str,
    bundle_dir: str,
    ops: Ops,
    legacy_bundle: bool = False,
) -> int:
    """Нормализация после approve владельца → номер PR (нового или уже
    открытого).

    Глобального dirty-гарда здесь НЕТ намеренно: approve-штамп владельца
    (`spec approve`) живёт в рабочем дереве незакоммиченным — он и есть
    груз этого PR. commit_paths берёт только tasks-файл.

    Идемпотентность (приёмка PR #117, круги 1–2): при уже открытом PR
    ветки повторный запуск НЕ создаёт второй PR (`gh pr create` упал бы),
    но по-прежнему доставляет текущее содержимое — свежий незакоммиченный
    approve-штамп владельца коммитится и пушится В ТУ ЖЕ ветку (пустой
    индекс/актуальный push — no-op у RealOps).
    """
    anchor_node_id = _LEGACY_ANCHOR_NODE_ID if legacy_bundle else _ANCHOR_NODE_ID
    anchor_filename = _LEGACY_ANCHOR_FILENAME if legacy_bundle else _ANCHOR_FILENAME
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
            "_BUNDLE_DAG, либо его легаси-префикс при --legacy-bundle; "
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
        "--legacy-bundle", action="store_true",
        help="бандл авторен до раскатки design-узла (без 20-design.md) — "
        "штамп/якорь идут по 3-узловому префиксу DAG "
        "charter→requirements→behaviour-spec; применимо в обоих режимах "
        "(доставка и --conform-approve)",
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
    # approved_by/at — факт мержа бандл-PR (решение владельца devtools#110:
    # инициированный мерж = approve; mergedBy — различитель agent/human,
    # ADR-ECO-011). Отсутствие факта — стоп, не выдуманное значение.
    if state.pr is None:
        print("task_bridge: в леджере нет номера бандл-PR — штамп невозможен")
        return 1
    facts = ops.pr_facts(state.repo_slug, state.pr)
    merged_by = (facts.get("mergedBy") or {}).get("login")
    merged_at = facts.get("mergedAt")
    if not merged_by or not merged_at:
        print(
            f"task_bridge: у PR #{state.pr} нет mergedBy/mergedAt — "
            "бандл не вмержен или API не отдал факт мержа; стоп"
        )
        return 1
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
        legacy_bundle=args.legacy_bundle,
        profile=state.profile,
    )
    print(f"draft tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
