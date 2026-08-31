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

from governance.ops import Ops, RealOps
from governance.run_state import load

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


def render_tasks(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    generated_at: str,
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
    for index, (_key, title, group) in enumerate(groups, start=1):
        beh_ids = [g.beh_id for g in group]
        traces: list[str] = []
        for g in group:
            traces += [t for t in g.traces if t not in traces]
        targets: list[str] = []
        for g in group:
            if g.checked_target and g.checked_target not in targets:
                targets.append(g.checked_target)
        check = (
            f"проверка группы: {', '.join(targets)} зелёные на "
            f"{', '.join(beh_ids)}"
            if targets
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


def deliver(
    target_dir: str,
    repo_slug: str,
    ws_id: str,
    subject: str,
    bundle_dir: str,
    base_ref: str,
    ops: Ops,
    generated_at: str | None = None,
) -> int:
    """Пишет spec/<ws-id>-tasks.md в target и открывает draft-PR.

    Fail-closed по образцу S1 runner'а: грязный target — отказ (иначе
    commit_paths закоммитил бы рядом с чужими правками). База освежается
    ДО создания ветки — спека генерируется из вмерженного бандла, не из
    случайного состояния чекаута.
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
    scenarios = parse_behaviour(behaviour.read_text(encoding="utf-8"))
    stamp = generated_at or datetime.now().isoformat(timespec="seconds")
    text = render_tasks(
        ws_id=ws_id,
        subject=subject,
        bundle_path=f"{bundle_dir}/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at=stamp,
    )
    branch = f"spec/{ws_id}-tasks"
    ops.ensure_branch(target_dir, branch)
    rel = f"spec/{ws_id}-tasks.md"
    out = Path(target_dir) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    ops.commit_paths(
        target_dir, [rel], f"spec: {ws_id} tasks (draft, fleet-agent)"
    )
    ops.push_branch(target_dir, branch)
    body = (
        f"Draft tasks.md-спека из behaviour-spec бандла {ws_id} "
        f"({bundle_dir}/15-behaviour-spec.md), сгенерирована task_bridge.\n\n"
        "Спека managed: `status: draft` НЕ исполняется при strict-governance —"
        " approve (перевод в approved) делает человек, затем "
        f"`spec-runner run --strict --spec-prefix={ws_id}-` в репо-владельце."
    )
    return ops.create_draft_pr(
        target_dir,
        repo_slug,
        branch,
        f"spec: {ws_id} tasks (draft) — {subject}",
        body,
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: параметры доставки берутся из леджера прогона (`run.json`)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
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
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=state.base_ref or "master",
        ops=RealOps(),
    )
    print(f"draft tasks-спека доставлена: PR #{pr} ({state.repo_slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
