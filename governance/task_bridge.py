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

from governance.ops import Ops, RealOps
from governance.run_state import load
from governance.stale_adapter import blob_sha1

# Цепочка бандла в порядке штампа: каждый следующий пинует blob ПРЕДЫДУЩЕГО
# после его штампа (иначе пин протухает в момент записи).
_BUNDLE_CHAIN = (
    ("00-charter.md", None),
    ("10-requirements.md", "charter"),
    ("15-behaviour-spec.md", "requirements"),
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


def render_tasks(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    generated_at: str,
    behaviour_blob: str,
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
        "- behaviour-spec",
        "upstream_hashes:",
        f"  behaviour-spec: {behaviour_blob}",
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
) -> list[str]:
    """Штамп статусов вмерженного бандла + перепиновка цепочки; → rel-пути.

    Урок 2 ретроспективы (devtools#110): после мержа бандла charter /
    requirements / behaviour-spec остаются `status: draft` — «никто не
    проштамповал». Approve-событие уже состоялось: по решению владельца
    (devtools#110, 2026-09-02) инициированный им мерж = человеческий
    approve, а агентский мерж легитимен по DarkFactory (ADR-ECO-011).
    Штамп записывает ЭТОТ факт: `approved_by` = mergedBy бандл-PR (честный
    различитель agent/human), `approved_at` = mergedAt.

    Перепиновка идёт по цепочке: штамп меняет байты файла, поэтому каждый
    следующий пинует blob предыдущего ПОСЛЕ его штампа. Идемпотентно:
    уже approved файл с верным пином не трогается и в результат не входит.
    """
    changed: list[str] = []
    prev_blob: str | None = None
    base = Path(target_dir) / bundle_dir
    for name, upstream in _BUNDLE_CHAIN:
        path = base / name
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        dirty = False
        if meta.get("status") != "approved":
            meta["status"] = "approved"
            meta["approved_by"] = approved_by
            meta["approved_at"] = approved_at
            meta["version"] = int(meta.get("version") or 1) + 1
            dirty = True
        if upstream is not None and prev_blob is not None:
            pins = meta.get("upstream_hashes")
            pins = dict(pins) if isinstance(pins, dict) else {}
            if pins.get(upstream) != prev_blob:
                pins[upstream] = prev_blob
                meta["upstream_hashes"] = pins
                dirty = True
        if dirty:
            path.write_text(join_frontmatter(meta, body), encoding="utf-8")
            changed.append(f"{bundle_dir}/{name}")
        prev_blob = blob_sha1(path.read_text(encoding="utf-8"))
    return changed


def conform_approved(target_dir: str, ws_id: str, bundle_dir: str) -> bool:
    """Нормализация frontmatter tasks-спеки ПОСЛЕ `spec approve` владельца.

    Урок 1 ретроспективы: `spec approve` деривит traces_to из вшитого
    lite-профиля (tasks ← design; других профилей у spec-runner нет —
    upstream-плечо заведено отдельно) и дописывает `design` к нашему
    `behaviour-spec`. Нормализация возвращает форму активного
    governance-профиля: traces_to ровно [behaviour-spec], пин — на
    ТЕКУЩИЙ blob вмерженного 15-behaviour-spec.md. Строгий run проверяет
    только status — правка безопасна. Возвращает, менялся ли файл.
    """
    rel = Path(target_dir) / "spec" / f"{ws_id}-tasks.md"
    meta, body = split_frontmatter(rel.read_text(encoding="utf-8"))
    if meta.get("status") != "approved":
        raise RuntimeError(
            f"{rel.name}: status={meta.get('status')!r} — нормализация идёт "
            "ПОСЛЕ человеческого `spec approve` (инвариант №4), сначала он"
        )
    behaviour = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
    pin = blob_sha1(behaviour.read_text(encoding="utf-8"))
    changed = False
    if meta.get("traces_to") != ["behaviour-spec"]:
        meta["traces_to"] = ["behaviour-spec"]
        changed = True
    want = {"behaviour-spec": pin}
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
) -> int:
    """Штампует бандл + пишет spec/<ws-id>-tasks.md; один draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.

    Порядок «штамп бандла → пин behaviour-spec → рендер tasks» жёсткий:
    штамп меняет байты 15-behaviour-spec.md, и пин, взятый до штампа,
    протух бы в том же PR (@id:spec-bridge-approve-conformance).
    """
    if ops.is_dirty(target_dir):
        raise RuntimeError(
            f"target_dir {target_dir!r} грязный — доставка спеки не начата"
        )
    ops.checkout_and_pull(target_dir, base_ref)
    # Существование и чтение бандла — строго ПОСЛЕ чекаута базы (приёмка
    # PR #96, major): до него чекаут мог стоять на произвольной ветке, и
    # спека сгенерировалась бы из невмерженной ревизии бандла.
    behaviour = Path(target_dir) / bundle_dir / "15-behaviour-spec.md"
    if not behaviour.exists():
        raise RuntimeError(
            f"{behaviour} не найден на {base_ref} — бандл не вмержен "
            "или путь неверен"
        )
    branch = f"spec/{ws_id}-tasks"
    ops.ensure_branch(target_dir, branch)
    stamped = stamp_bundle_approved(
        target_dir, bundle_dir, approved_by, approved_at
    )
    behaviour_blob = blob_sha1(behaviour.read_text(encoding="utf-8"))
    scenarios = parse_behaviour(behaviour.read_text(encoding="utf-8"))
    stamp = generated_at or datetime.now().isoformat(timespec="seconds")
    text = render_tasks(
        ws_id=ws_id,
        subject=subject,
        bundle_path=f"{bundle_dir}/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at=stamp,
        behaviour_blob=behaviour_blob,
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
            "перепиновка цепочки charter→requirements→behaviour-spec).\n\n"
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
    branch = f"spec/{ws_id}-tasks-approve"
    existing = ops.find_pr(repo_slug, branch)
    ops.ensure_branch(target_dir, branch)
    changed = conform_approved(target_dir, ws_id, bundle_dir)
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
            "traces_to ровно [behaviour-spec] (lite-профиль spec-runner "
            "дописывает design — других профилей у него нет, upstream-плечо "
            "заведено), пин upstream_hashes — на текущий blob вмерженного "
            f"{bundle_dir}/15-behaviour-spec.md."
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
    )
    print(f"draft tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
