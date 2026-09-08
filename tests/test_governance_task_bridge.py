"""Тесты task_bridge: behaviour-spec → draft tasks.md-спека PR-ом.

Шаг 3 плана развития варианта 1 (решение владельца 2026-08-31): замкнуть
цикл «предмет → спецификация → исполнители». Мост читает вмерженный
behaviour-spec бандла и генерирует managed-спеку `spec/<ws-id>-tasks.md`
(status: draft — не исполняется до человеческого approve, инвариант №4).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from governance import decomposition_guard, task_bridge

BEHAVIOUR_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: product
traces_to: [requirements]
---
# Behaviour

## Общие допущения

- фон, не сценарий

#### BEH-01: Просмотр списка
`traces: [FR-01, NFR-02]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/test_x.py`

**Дано** список; **Когда** открытие; **Тогда** видно.

#### BEH-02: Пустое состояние
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: e2e` `owner: qa` \
`target: tests/test_y.py`

Текст сценария.
"""


CHARTER_MD = """\
---
spec_stage: charter
status: draft
version: 1
owner_role: product
---
# Charter

Текст charter.
"""

REQUIREMENTS_MD = """\
---
spec_stage: requirements
status: draft
version: 1
owner_role: product
traces_to: [charter]
upstream_hashes:
  charter: ab00000000000000000000000000000000000000
---
# Requirements

Текст requirements.
"""

DESIGN_MD = """\
---
spec_stage: design
status: draft
version: 1
owner_role: architects
traces_to: [requirements, behaviour-spec]
upstream_hashes:
  requirements: cd00000000000000000000000000000000000000
  behaviour-spec: ef00000000000000000000000000000000000000
---
# Design

Текст design.

#### Q-01 · owner_role: architects · resolution: resolved
Выбран REST — синхронный вызов проще для MVP.

| Вариант | Задержка |
| --- | --- |
| REST | низкая |
| GraphQL | средняя |

- ограничение: без batching на старте

#### Q-03 · owner_role: architects · resolution: deferred
reason: Нужны замеры нагрузки перед выбором шардирования.
"""

# Валидный 25-acceptance.md (Task 7 плана acceptance-node): два верных
# пина upstream (requirements, behaviour-spec), один AC-01 с traces на
# FR-01 (Must-требований в REQUIREMENTS_MD нет вовсе — трасса берёт
# существующий id из behaviour-spec, а не выдуманный), все 4 обязательные
# секции DSL (`governance/ops.py::_AUTHOR_DSL["acceptance"]`). Без этого
# файла ВСЕ full-DAG тесты deliver/stamp/conform падают на
# `_check_bundle_composition` — узел acceptance вошёл в `_BUNDLE_DAG`.
ACCEPTANCE_MD = """\
---
spec_stage: acceptance
status: draft
owner_role: qa
traces_to: [requirements, behaviour-spec]
upstream_hashes:
  requirements: cd00000000000000000000000000000000000000
  behaviour-spec: ef00000000000000000000000000000000000000
---
## Критерии приёмки

Must-требований во входном наборе нет

#### AC-01: Список виден · verification: manual
traces: [FR-01]
Наблюдаемый признак: человек видит список.

## Инварианты покрытия

Must-требования покрыты хотя бы одним AC.

## Порог приёмки

AC-01 обязателен к выполнению.

## Вне объёма

Ничего не исключено.
"""

DECOMPOSITION_MD = """\
---
spec_stage: decomposition
status: draft
version: 1
owner_role: tech-lead
traces_to: [design, acceptance]
upstream_hashes:
  design: 1200000000000000000000000000000000000000
  acceptance: 3400000000000000000000000000000000000000
---
## Задачи

#### DT-01: Реализация · type: implement · owner: dev
scenarios: [BEH-01, BEH-02]
depends_on: []
parallel_group: solo

Проза предмета.

## Инварианты графа

Соблюдены.

## Порядок и параллельность

DT-01 — единственная задача, зависимостей нет.

## Вне объёма

Ничего не исключено.
"""

# Вариант ДО раскатки acceptance-узла (Task 7 плана acceptance-node,
# `--legacy-bundle=5`): decomposition этой эры пинует только design —
# acceptance ещё не существовал.
DECOMPOSITION_MD_LEGACY5 = """\
---
spec_stage: decomposition
status: draft
version: 1
owner_role: tech-lead
traces_to: [design]
upstream_hashes:
  design: 1200000000000000000000000000000000000000
---
## Задачи

#### DT-01: Реализация · type: implement · owner: dev
scenarios: [BEH-01, BEH-02]
depends_on: []
parallel_group: solo

Проза предмета.

## Инварианты графа

Соблюдены.

## Порядок и параллельность

DT-01 — единственная задача, зависимостей нет.

## Вне объёма

Ничего не исключено.
"""


def test_parse_behaviour_extracts_scenarios() -> None:
    scenarios = task_bridge.parse_behaviour(BEHAVIOUR_MD)
    assert [s.beh_id for s in scenarios] == ["BEH-01", "BEH-02"]
    assert scenarios[0].title == "Просмотр списка"
    assert scenarios[0].traces == ("FR-01", "NFR-02")
    assert scenarios[0].checked_kind == "integration"
    assert scenarios[0].checked_target == "tests/test_x.py"


def test_parse_behaviour_empty_is_error() -> None:
    with pytest.raises(ValueError, match="BEH"):
        task_bridge.parse_behaviour("# ничего похожего на DSL\n")


def test_render_tasks_structure() -> None:
    scenarios = task_bridge.parse_behaviour(BEHAVIOUR_MD)
    text = task_bridge.render_tasks(
        ws_id="WS-alpha-7",
        subject="Наблюдаемость (alpha#7)",
        bundle_path="workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-08-31T12:00:00",
        design_blob="ab" * 20,
    )
    assert text.startswith("---\n")
    assert "spec_stage: tasks" in text
    assert "status: draft" in text
    assert "generated_by: fleet-agent" in text
    assert 'generated_at: "2026-08-31T12:00:00"' in text
    assert "## Milestone 1: Наблюдаемость (alpha#7)" in text
    assert "### TASK-001: Просмотр списка" in text
    assert "### TASK-002: Пустое состояние" in text
    assert (
        "Source: workstreams/WS-alpha-7/spec/15-behaviour-spec.md#BEH-01"
        in text
    )
    assert "**Traces to:** [FR-01], [NFR-02]" in text
    # последний чеклист-пункт каждой задачи — проверка, не действие
    assert (
        "проверка группы: tests/test_x.py (kind: integration) "
        "зелёные на BEH-01" in text
    )
    assert (
        "проверка группы: tests/test_y.py (kind: e2e) зелёные на BEH-02"
        in text
    )
    # чеклист с колонки 0 (отступ молча игнорируется парсером spec-runner)
    for line in text.splitlines():
        if "[ ]" in line:
            assert line.startswith("- [ ]")


# --- _render_resolutions_section (devtools#158) --------------------------


def test_render_resolutions_section_carries_table_verbatim() -> None:
    """Секция резолюций несёт тело Q-блока целиком, не первый абзац —
    строка таблицы обязана дойти дословно (живая находка kapelle: Q-05
    несла классификационную таблицу, которая терялась в bullet-рендере)."""
    design_text = (
        "#### Q-01 · owner_role: architects · resolution: resolved\n"
        "Интро-абзац.\n"
        "\n"
        "| Вариант | Задержка |\n"
        "| --- | --- |\n"
        "| REST | низкая |\n"
    )
    lines = task_bridge._render_resolutions_section(design_text)
    text = "\n".join(lines)
    assert "## Решения открытых вопросов (уровень design)" in text
    assert "**Q-01 — resolved:**" in text
    assert "| REST | низкая |" in text


def test_render_resolutions_section_bare_heading_falls_back_to_one_liner() -> None:
    """Пустое тело (голый заголовок, без абзаца/reason:) — старое
    однострочное поведение (`- **Q-NN:** resolved`), не голая шапка."""
    design_text = "#### Q-01 · owner_role: architects · resolution: resolved\n"
    lines = task_bridge._render_resolutions_section(design_text)
    assert "- **Q-01:** resolved" in lines


def test_render_resolutions_section_fallback_bullet_has_blank_line_before_next_entry() -> None:
    """Минор ревью PR #160: fallback-буллет (голый заголовок Q-01, без
    завершающей пустой строки) склеивал шапку следующей записи (Q-02) в
    свой markdown-абзац — ленивое продолжение списка CommonMark/GitHub."""
    design_text = (
        "#### Q-01 · owner_role: architects · resolution: resolved\n"
        "\n"
        "#### Q-02 · owner_role: architects · resolution: resolved\n"
        "Тело Q-02.\n"
    )
    lines = task_bridge._render_resolutions_section(design_text)
    idx = lines.index("- **Q-01:** resolved")
    assert lines[idx + 1] == ""
    assert lines[idx + 2] == "**Q-02 — resolved:**"


class _StubOps:
    """Минимальный стаб Ops-поверхности, которую использует deliver()."""

    def __init__(self, dirty: bool = False) -> None:
        self.dirty = dirty
        self.calls: list[tuple] = []

    def is_dirty(self, target_dir: str) -> bool:
        return self.dirty

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        self.calls.append(("checkout_and_pull", branch))

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("ensure_branch", branch))

    def commit_paths(
        self, target_dir: str, paths: list[str], message: str
    ) -> None:
        self.calls.append(("commit_paths", tuple(paths)))

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", branch))

    def create_draft_pr(
        self,
        target_dir: str,
        repo_slug: str,
        branch: str,
        title: str,
        body: str,
        label: str,
    ) -> int:
        self.calls.append(("create_draft_pr", repo_slug, branch, label))
        self.pr_title = title
        self.pr_body = body
        return 77


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_MD)
    return target


def test_deliver_writes_spec_and_opens_pr(tmp_path: Path) -> None:
    target = _target(tmp_path)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="Наблюдаемость (alpha#7)",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="andrei-shtanakov",
        approved_at="2026-09-02T06:07:39Z",
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    assert spec.exists()
    assert "### TASK-001:" in spec.read_text()
    names = [c[0] for c in ops.calls]
    # база освежается до ветки
    assert names.index("checkout_and_pull") < names.index("ensure_branch")
    # один коммит: штамп шести файлов бандла (полный DAG до decomposition) +
    # файл спеки
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == (
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
        "workstreams/WS-alpha-7/spec/25-acceptance.md",
        "workstreams/WS-alpha-7/spec/30-decomposition.md",
        "spec/WS-alpha-7-tasks.md",
    )
    assert ("push_branch", "spec/WS-alpha-7-tasks") in ops.calls
    assert "draft" in ops.pr_body.lower()
    assert "штамп статусов" in ops.pr_body
    # Пин tasks-спеки — blob decomposition ПОСЛЕ штампа (иначе протух бы в
    # том же PR): decomposition — терминальный узел _BUNDLE_DAG (Task 7).
    from governance.stale_adapter import blob_sha1
    stamped_blob = blob_sha1(
        (target / "workstreams/WS-alpha-7/spec/30-decomposition.md")
        .read_text(encoding="utf-8")
    )
    meta, _body = task_bridge.split_frontmatter(
        spec.read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["decomposition"]
    assert meta["upstream_hashes"] == {"decomposition": stamped_blob}
    # секция резолюций сгенерирована из фикстурного 20-design.md, не
    # рукописным текстом (Task 5, Step 1в)
    spec_text = spec.read_text()
    assert "## Решения открытых вопросов (уровень design)" in spec_text
    assert (
        "**Q-03 — deferred:** reason: Нужны замеры нагрузки перед "
        "выбором шардирования." in spec_text
    )
    # Task 7, low review #2: resolved-ветка несёт обоснование (reason),
    # построчная проверка — не только заголовок секции/deferred-строка.
    assert "**Q-01 — resolved:**" in spec_text
    assert "Выбран REST — синхронный вызов проще для MVP." in spec_text
    # devtools#158: тело Q-блока — целиком, не первый абзац. Таблица и
    # список фикстурного Q-01 (после интро-абзаца) обязаны дойти до
    # доставленной спеки дословно (живая находка kapelle: Q-05 несла
    # классификационную таблицу, которая не доходила до executors).
    assert "| GraphQL | средняя |" in spec_text
    assert "- ограничение: без batching на старте" in spec_text


def test_deliver_default_generated_at_has_utc_offset(tmp_path: Path) -> None:
    """devtools#157: штамп `generated_at` по умолчанию (без явного
    параметра) обязан нести смещение UTC — spec-runner approve пишет
    tz-aware `approved_at`, а naive-локальный `datetime.now().isoformat()`
    делает сравнение двух штампов неопределённым (живая аномалия «approve
    раньше генерации» в kapelle). Явный `generated_at` — не в скоупе этой
    находки, у него своя граница обратной совместимости."""
    target = _target(tmp_path)
    ops = _StubOps()
    task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a",
        approved_at="t",
    )
    spec = target / "spec/WS-alpha-7-tasks.md"
    meta, _body = task_bridge.split_frontmatter(spec.read_text(encoding="utf-8"))
    assert re.search(r"(Z|[+-]\d{2}:\d{2})$", meta["generated_at"])


def test_deliver_dirty_target_refuses(tmp_path: Path) -> None:
    target = _target(tmp_path)
    with pytest.raises(RuntimeError, match="грязный"):
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=_StubOps(dirty=True),
            approved_by="a", approved_at="t",
        )


def test_deliver_missing_behaviour_refuses(tmp_path: Path) -> None:
    target = tmp_path / "alpha"
    target.mkdir()
    with pytest.raises(RuntimeError, match="15-behaviour-spec"):
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=_StubOps(),
            approved_by="a", approved_at="t",
        )


def test_deliver_reads_bundle_only_after_base_checkout(tmp_path: Path) -> None:
    """Существование бандла проверяется ПОСЛЕ checkout_and_pull базы
    (приёмка PR #96): до чекаута дерево могло стоять на произвольной ветке."""
    target = tmp_path / "alpha"
    target.mkdir()
    bundle = target / "workstreams/WS-alpha-7/spec"

    class _LateOps(_StubOps):
        def checkout_and_pull(self, target_dir: str, branch: str) -> None:
            super().checkout_and_pull(target_dir, branch)
            bundle.mkdir(parents=True)
            (bundle / "00-charter.md").write_text(CHARTER_MD)
            (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
            (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
            (bundle / "20-design.md").write_text(DESIGN_MD)
            (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
            (bundle / "30-decomposition.md").write_text(DECOMPOSITION_MD)

    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=_LateOps(),
        approved_by="a", approved_at="t",
    )
    assert pr == 77


def test_cli_refuses_not_completed_run(tmp_path: Path, monkeypatch, capsys) -> None:
    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        target_dir=str(tmp_path),
        bundle_dir="workstreams/WS-alpha-7/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-bridge-wait",
    )
    state.status = "waiting_human_merge"
    rs.save(state)
    assert task_bridge.main(["--run-id", "r-bridge-wait"]) == 1
    assert "completed" in capsys.readouterr().out


FEATURED_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: product
---
# Behaviour

## Feature: Каркас

#### BEH-01: Позитив
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/test_a.py`

#### BEH-02: Пустой корень
`traces: [FR-01, FR-02]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/test_a.py`

## Feature: Безопасность

#### BEH-03: Небезопасный путь
`traces: [FR-03]`
- **checked_by**: `status: planned` `kind: e2e` `owner: qa` \
`target: tests/test_b.py`
"""


def test_render_groups_by_feature_sections() -> None:
    """Группировка по Feature (решение владельца 2026-08-31): одна задача
    на секцию, полный перечень BEH внутри, зависимость цепочкой."""
    scenarios = task_bridge.parse_behaviour(FEATURED_MD)
    assert [s.feature for s in scenarios] == [
        "Каркас", "Каркас", "Безопасность",
    ]
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="workstreams/WS-x-1/spec/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-08-31T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-001: Каркас" in text
    assert "### TASK-002: Безопасность" in text
    assert "### TASK-003:" not in text
    assert "- [ ] реализовать BEH-01: Позитив" in text
    assert "- [ ] реализовать BEH-02: Пустой корень" in text
    assert "**Depends on:** [TASK-001]" in text
    # пер-ссылочные скобки и в DT-пути (minor ревью PR #149): откат
    # второй копии фикса Traces to обязан краснить
    assert "**Traces to:** [FR-01], [FR-02]" in text
    # traces группы — объединение без дублей
    assert "**Traces to:** [FR-01], [FR-02]" in text
    # Source несёт диапазон группы
    assert "#BEH-01 (—BEH-02)" in text


def test_plain_heading_closes_feature_section() -> None:
    """Обычный `##`-заголовок завершает Feature (приёмка PR #100, minor):
    сценарий под ним — отдельная задача 1:1, не хвост предыдущей группы."""
    md = FEATURED_MD + """\

## Особые случаи

#### BEH-04: Вне Feature
`traces: [FR-04]`
- **checked_by**: `status: planned` `kind: manual` `owner: qa` \
`target: docs/manual.md`
"""
    scenarios = task_bridge.parse_behaviour(md)
    assert scenarios[-1].feature is None
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-08-31T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-003: Вне Feature" in text


# --- frontmatter-хелперы и штамп бандла (@id:spec-bridge-approve-conformance)


def test_split_join_frontmatter_roundtrip() -> None:
    meta, body = task_bridge.split_frontmatter(REQUIREMENTS_MD)
    assert meta["spec_stage"] == "requirements"
    assert meta["upstream_hashes"] == {
        "charter": "ab" + "0" * 38
    }
    assert body.startswith("# Requirements")
    rejoined = task_bridge.join_frontmatter(meta, body)
    meta2, body2 = task_bridge.split_frontmatter(rejoined)
    assert meta2 == meta and body2 == body


def test_split_frontmatter_refuses_plain_file() -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        task_bridge.split_frontmatter("# просто markdown\n")


def test_stamp_bundle_approves_and_repins_chain(tmp_path: Path) -> None:
    """Урок 2 ретроспективы: штамп статусов + перепиновка DAG (Task 5:
    цепочка стала DAG — design пинует ОБА upstream, requirements и
    behaviour-spec; Task 7 плана acceptance-node: acceptance — тоже оба
    upstream, decomposition пинует ОБА upstream — design и acceptance).
    Каждый следующий файл пинует blob предыдущего(-их) ПОСЛЕ его штампа."""
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    changed = task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="ai-prosto", approved_at="2026-09-02T10:00:00Z",
    )
    assert changed == [
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
        "workstreams/WS-alpha-7/spec/25-acceptance.md",
        "workstreams/WS-alpha-7/spec/30-decomposition.md",
    ]
    bundle = target / "workstreams/WS-alpha-7/spec"
    charter_meta, _ = task_bridge.split_frontmatter(
        (bundle / "00-charter.md").read_text(encoding="utf-8")
    )
    assert charter_meta["status"] == "approved"
    assert charter_meta["approved_by"] == "ai-prosto"
    assert charter_meta["version"] == 2
    req_text = (bundle / "10-requirements.md").read_text(encoding="utf-8")
    req_meta, _ = task_bridge.split_frontmatter(req_text)
    assert req_meta["upstream_hashes"]["charter"] == blob_sha1(
        (bundle / "00-charter.md").read_text(encoding="utf-8")
    )
    beh_text = (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
    beh_meta, _ = task_bridge.split_frontmatter(beh_text)
    assert beh_meta["status"] == "approved"
    assert beh_meta["upstream_hashes"]["requirements"] == blob_sha1(req_text)
    design_text = (bundle / "20-design.md").read_text(encoding="utf-8")
    design_meta, _ = task_bridge.split_frontmatter(design_text)
    assert design_meta["status"] == "approved"
    assert design_meta["upstream_hashes"]["requirements"] == blob_sha1(
        req_text
    )
    assert design_meta["upstream_hashes"]["behaviour-spec"] == blob_sha1(
        beh_text
    )
    acceptance_text = (bundle / "25-acceptance.md").read_text(
        encoding="utf-8"
    )
    acceptance_meta, _ = task_bridge.split_frontmatter(acceptance_text)
    assert acceptance_meta["status"] == "approved"
    assert acceptance_meta["upstream_hashes"]["requirements"] == blob_sha1(
        req_text
    )
    assert acceptance_meta["upstream_hashes"]["behaviour-spec"] == blob_sha1(
        beh_text
    )
    decomposition_meta, _ = task_bridge.split_frontmatter(
        (bundle / "30-decomposition.md").read_text(encoding="utf-8")
    )
    assert decomposition_meta["status"] == "approved"
    assert decomposition_meta["upstream_hashes"]["design"] == blob_sha1(
        design_text
    )
    assert decomposition_meta["upstream_hashes"]["acceptance"] == blob_sha1(
        acceptance_text
    )


def test_stamp_bundle_is_idempotent(tmp_path: Path) -> None:
    target = _target(tmp_path)
    task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="x", approved_at="t",
    )
    again = task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="y", approved_at="t2",
    )
    assert again == []


# --- Task 7 (acceptance-node): узел acceptance в DAG,
# --legacy-bundle=3|4|5 --------------------------------------------------


def test_bundle_dag_has_acceptance_and_two_pin_decomposition() -> None:
    """Узел acceptance вошёл в `_BUNDLE_DAG` перед decomposition;
    decomposition — терминальный узел с ДВУМЯ upstream-пинами (design,
    acceptance), как design несёт два пина (requirements, behaviour-spec).
    Объединяет прежний `test_bundle_dag_terminates_at_decomposition`
    (декомпозиция теперь пинует не только design)."""
    assert (
        "25-acceptance.md", ("requirements", "behaviour-spec"),
    ) in task_bridge._BUNDLE_DAG
    assert task_bridge._BUNDLE_DAG[-1] == (
        "30-decomposition.md", ("design", "acceptance"),
    )
    assert task_bridge._ANCHOR_NODE_ID == "decomposition"


def test_dag_for_none_is_full_dag() -> None:
    assert task_bridge._dag_for(None) == task_bridge._BUNDLE_DAG


def test_dag_for_legacy_values_are_exact_prefixes() -> None:
    assert task_bridge._dag_for(3) == task_bridge._BUNDLE_DAG[:3]
    assert task_bridge._dag_for(4) == task_bridge._BUNDLE_DAG[:4]


def test_dag_for_5_is_the_old_five_node_variant_not_a_slice() -> None:
    """`--legacy-bundle=5` — бандл до раскатки acceptance-узла:
    `_BUNDLE_DAG_LEGACY5` — ЛИТЕРАЛЬНЫЙ отдельный кортеж, не срез нового
    `_BUNDLE_DAG` (тот несёт 25-acceptance.md на позиции 4, срез[:5] дал
    бы состав без 30-decomposition.md — не тот легаси-каталог)."""
    dag5 = task_bridge._dag_for(5)
    assert [f for f, _ in dag5] == [
        "00-charter.md", "10-requirements.md", "15-behaviour-spec.md",
        "20-design.md", "30-decomposition.md",
    ]
    # решённая ДО acceptance-эры декомпозиция пинует только design
    assert dag5[-1] == ("30-decomposition.md", ("design",))


def test_dag_for_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="3, 4 или 5"):
        task_bridge._dag_for(6)


def test_legacy_5_exact_composition(tmp_path: Path) -> None:
    """Каталог с ровно 5 узлами (00/10/15/20/30, эра ДО раскатки
    acceptance-узла): `legacy_bundle=5` штампует по
    `_BUNDLE_DAG_LEGACY5`; `legacy_bundle=4` отказывает (лишний
    30-decomposition.md в каталоге); `legacy_bundle=None` (полный DAG)
    отказывает — недостаёт 25-acceptance.md, текст называет и файл, и
    процедуру `--legacy-bundle`; тот же 5-узловой каталог + добавленный
    25-acceptance.md (6 узлов) с `legacy_bundle=5` тоже отказывает —
    лишний узел (запрет «по самому длинному существующему», спека §4)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_MD_LEGACY5)

    changed = task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="a", approved_at="t", legacy_bundle=5,
    )
    assert changed == [
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
        "workstreams/WS-alpha-7/spec/30-decomposition.md",
    ]

    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t", legacy_bundle=4,
        )
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t",
        )
    message = str(exc_info.value)
    assert "25-acceptance.md" in message
    assert "--legacy-bundle=3|4|5" in message

    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t", legacy_bundle=5,
        )


def test_legacy_bundle_exact_composition(tmp_path: Path) -> None:
    """Каталог с ровно 4 узлами (00/10/15/20, без 30-decomposition.md):
    legacy_bundle=4 штампует и якорит на design; legacy_bundle=3 и
    legacy_bundle=None (полный DAG) отказывают — состав не совпал точно
    (запрет «по самому длинному существующему», спека §4)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)

    changed = task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="a", approved_at="t", legacy_bundle=4,
    )
    assert changed == [
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
    ]

    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t", legacy_bundle=3,
        )
    with pytest.raises(RuntimeError, match=r"--legacy-bundle=3\|4"):
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t",
        )


def test_legacy_flag_requires_value() -> None:
    with pytest.raises(SystemExit):
        task_bridge.main(["--run-id", "r-x", "--legacy-bundle"])


def test_legacy_flag_rejects_out_of_range_value() -> None:
    with pytest.raises(SystemExit):
        task_bridge.main(["--run-id", "r-x", "--legacy-bundle", "6"])


# --- Task 7: переходный режим легаси-бандлов (без узла design) -----------


def _target_legacy(tmp_path: Path) -> Path:
    """Бандл из трёх узлов (charter/requirements/behaviour-spec) — БЕЗ
    20-design.md, как несли соседние репо до раскатки design-узла."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    return target


def test_stamp_bundle_without_design_refuses_without_legacy_flag(
    tmp_path: Path,
) -> None:
    """Step 1(а): 3-узловой бандл без флага ⇒ RuntimeError, а не сырой
    traceback от `read_text` — текст называет файл и обе процедуры
    (доавторить design; --legacy-bundle)."""
    target = _target_legacy(tmp_path)
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.stamp_bundle_approved(
            str(target), "workstreams/WS-alpha-7/spec",
            approved_by="a", approved_at="t",
        )
    message = str(exc_info.value)
    assert "20-design.md" in message
    assert "design" in message.lower()
    assert "--legacy-bundle" in message


def test_stamp_bundle_legacy_mode_stamps_three_node_prefix(
    tmp_path: Path,
) -> None:
    """Step 1(б): `legacy_bundle=3` ⇒ штамп только по 3-узловому
    префиксу DAG, никакого чтения 20-design.md."""
    target = _target_legacy(tmp_path)
    changed = task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="a", approved_at="t", legacy_bundle=3,
    )
    assert changed == [
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
    ]
    beh_meta, _ = task_bridge.split_frontmatter(
        (target / "workstreams/WS-alpha-7/spec/15-behaviour-spec.md")
        .read_text(encoding="utf-8")
    )
    assert beh_meta["status"] == "approved"


def test_conform_legacy_normalizes_to_behaviour_spec_no_design_read(
    tmp_path: Path,
) -> None:
    """Step 3b: `conform_approved(..., legacy_bundle=3)` якорит на
    behaviour-spec и не читает 20-design.md (бандл его не несёт вовсе —
    отсутствие файла не должно всплыть traceback'ом)."""
    from governance.stale_adapter import blob_sha1

    target = _target_legacy(tmp_path)
    spec_dir = target / "spec"
    spec_dir.mkdir()
    (spec_dir / "WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nstatus: approved\nversion: 2\n"
        "traces_to:\n- design\nupstream_hashes:\n  design: " + "2" * 40 + "\n"
        "---\n\n## Milestone 1: s\n",
        encoding="utf-8",
    )
    changed = task_bridge.conform_approved(
        str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec",
        legacy_bundle=3,
    )
    assert changed is True
    meta, _ = task_bridge.split_frontmatter(
        (spec_dir / "WS-alpha-7-tasks.md").read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["behaviour-spec"]
    assert meta["upstream_hashes"] == {
        "behaviour-spec": blob_sha1(
            (target / "workstreams/WS-alpha-7/spec/15-behaviour-spec.md")
            .read_text(encoding="utf-8")
        )
    }


def test_conform_legacy_bundle_without_flag_refuses(tmp_path: Path) -> None:
    """Без флага на легаси-бандле (approved tasks-спека, но 20-design.md
    нет) — тот же RuntimeError с процедурой, не сырой traceback."""
    target = _target_legacy(tmp_path)
    spec_dir = target / "spec"
    spec_dir.mkdir()
    (spec_dir / "WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nstatus: approved\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.conform_approved(
            str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec",
        )
    message = str(exc_info.value)
    assert "20-design.md" in message
    assert "--legacy-bundle" in message


def test_deliver_missing_design_refuses_before_branch_creation(
    tmp_path: Path,
) -> None:
    """Step 3b: `deliver` на бандле без 20-design.md падает ДО создания
    ветки — не только не мержится, ветка вовсе не заводится."""
    target = _target_legacy(tmp_path)
    ops = _StubOps()
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=ops,
            approved_by="a", approved_at="t",
        )
    message = str(exc_info.value)
    assert "20-design.md" in message
    assert "--legacy-bundle" in message
    assert not any(c[0] == "ensure_branch" for c in ops.calls)


def test_deliver_legacy_bundle_writes_spec_anchored_on_behaviour(
    tmp_path: Path,
) -> None:
    """`deliver(legacy_bundle=3)` доставляет спеку без design: анкер —
    behaviour-spec, штамп — только 3-узловой префикс DAG."""
    target = _target_legacy(tmp_path)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
        legacy_bundle=3,
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    meta, _ = task_bridge.split_frontmatter(spec.read_text(encoding="utf-8"))
    assert meta["traces_to"] == ["behaviour-spec"]
    assert "design" not in meta["upstream_hashes"]
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == (
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "spec/WS-alpha-7-tasks.md",
    )
    # секция резолюций design не рендерится вовсе — легаси-бандл design
    # текста не несёт
    assert "Решения открытых вопросов" not in spec.read_text()


def _target_legacy_4(tmp_path: Path) -> Path:
    """Бандл из четырёх узлов (charter/requirements/behaviour-spec/design)
    — БЕЗ 30-decomposition.md, как несли соседние репо до раскатки
    decomposition-узла (продакшн затронет этот переход первым: design уже
    раскатан, decomposition — ещё нет)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    return target


def test_deliver_legacy_bundle_4_writes_spec_anchored_on_design(
    tmp_path: Path,
) -> None:
    """Находка 1 финального ревью (непокрытые легаси-пути, которые
    продакшн затронет первыми): зеркало
    `test_deliver_legacy_bundle_writes_spec_anchored_on_behaviour` для
    `legacy_bundle=4` — 4-узловой бандл с design, анкер — design (не
    behaviour-spec), пин — blob фактического (уже проштампованного)
    20-design.md, секция резолюций присутствует (design несёт Q-*),
    DT-провенанса нет — легаси-путь идёт через `render_tasks`, не
    `render_tasks_dt`."""
    from governance.stale_adapter import blob_sha1

    target = _target_legacy_4(tmp_path)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
        legacy_bundle=4,
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    meta, _body = task_bridge.split_frontmatter(
        spec.read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["design"]
    stamped_design = (
        target / "workstreams/WS-alpha-7/spec/20-design.md"
    ).read_text(encoding="utf-8")
    assert meta["upstream_hashes"] == {"design": blob_sha1(stamped_design)}
    text = spec.read_text()
    assert "## Решения открытых вопросов (уровень design)" in text
    assert "(DT-" not in text
    assert "30-decomposition.md" not in text


def test_deliver_reads_design_only_after_base_checkout(tmp_path: Path) -> None:
    """Позиция гарда design/composition (Task 7): по образцу
    `test_deliver_reads_bundle_only_after_base_checkout` — весь бандл
    (вкл. 30-decomposition.md) появляется ТОЛЬКО внутри `checkout_and_pull`;
    гард обязан увидеть его там и НЕ упасть. Пре-чекаутная позиция гарда
    красит этот тест."""
    target = tmp_path / "alpha"
    target.mkdir()
    bundle = target / "workstreams/WS-alpha-7/spec"

    class _LateOps(_StubOps):
        def checkout_and_pull(self, target_dir: str, branch: str) -> None:
            super().checkout_and_pull(target_dir, branch)
            bundle.mkdir(parents=True)
            (bundle / "00-charter.md").write_text(CHARTER_MD)
            (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
            (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
            (bundle / "20-design.md").write_text(DESIGN_MD)
            (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
            (bundle / "30-decomposition.md").write_text(DECOMPOSITION_MD)

    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=_LateOps(),
        approved_by="a", approved_at="t",
    )
    assert pr == 77


def test_conform_normalizes_after_approve(tmp_path: Path) -> None:
    """Task 7: якорь — decomposition (терминальный узел `_BUNDLE_DAG`), не
    behaviour-spec. Регрессия: изменённый вручную (или унаследованный от
    старого поведения) `traces_to: [behaviour-spec]` нормализуется К
    decomposition, а НЕ откатывается обратно к behaviour-spec."""
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    spec_dir = target / "spec"
    spec_dir.mkdir()
    (spec_dir / "WS-alpha-7-tasks.md").write_text(
        "---\n"
        "spec_stage: tasks\n"
        "status: approved\n"
        "version: 2\n"
        "approved_by: andrei-shtanakov\n"
        "traces_to:\n- behaviour-spec\n"
        "upstream_hashes:\n  behaviour-spec: " + "1" * 40 + "\n"
        "---\n\n## Milestone 1: s\n",
        encoding="utf-8",
    )
    changed = task_bridge.conform_approved(
        str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec"
    )
    assert changed is True
    meta, body = task_bridge.split_frontmatter(
        (spec_dir / "WS-alpha-7-tasks.md").read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["decomposition"]
    assert meta["upstream_hashes"] == {
        "decomposition": blob_sha1(
            (target / "workstreams/WS-alpha-7/spec/30-decomposition.md")
            .read_text(encoding="utf-8")
        )
    }
    # поля approve владельца не тронуты
    assert meta["status"] == "approved"
    assert meta["approved_by"] == "andrei-shtanakov"
    assert "## Milestone 1: s" in body
    # идемпотентность: второй прогон НЕ трогает уже нормализованный якорь
    assert task_bridge.conform_approved(
        str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec"
    ) is False
    meta2, _ = task_bridge.split_frontmatter(
        (spec_dir / "WS-alpha-7-tasks.md").read_text(encoding="utf-8")
    )
    assert meta2["traces_to"] == ["decomposition"]


def test_conform_refuses_draft(tmp_path: Path) -> None:
    """Инвариант №4: нормализация — ПОСЛЕ человеческого approve, не вместо."""
    target = _target(tmp_path)
    spec_dir = target / "spec"
    spec_dir.mkdir()
    (spec_dir / "WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nstatus: draft\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="approve"):
        task_bridge.conform_approved(
            str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec"
        )


class _ConformOps(_StubOps):
    def __init__(self, existing_pr: int | None = None) -> None:
        super().__init__()
        self.existing_pr = existing_pr

    def find_pr(
        self, repo_slug: str, branch: str, *, any_state: bool = False
    ) -> int | None:
        self.calls.append(("find_pr", branch))
        return self.existing_pr


def _approved_tasks(target: Path) -> None:
    spec_dir = target / "spec"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nstatus: approved\nversion: 2\n"
        "traces_to:\n- behaviour-spec\n- design\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )


def test_deliver_conform_opens_pr(tmp_path: Path) -> None:
    target = _target(tmp_path)
    _approved_tasks(target)
    ops = _ConformOps()
    pr = task_bridge.deliver_conform(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        bundle_dir="workstreams/WS-alpha-7/spec",
        ops=ops,
    )
    assert pr == 77
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == ("spec/WS-alpha-7-tasks.md",)
    assert ("push_branch", "spec/WS-alpha-7-tasks-approve") in ops.calls


def test_deliver_conform_rerun_updates_existing_pr(tmp_path: Path) -> None:
    """Приёмка PR #117, круги 1–2: при открытом PR ветки второй PR не
    создаётся, но свежий незакоммиченный approve-штамп ДОСТАВЛЯЕТСЯ —
    нормализация, коммит и push идут в ту же ветку."""
    target = _target(tmp_path)
    _approved_tasks(target)
    ops = _ConformOps(existing_pr=88)
    pr = task_bridge.deliver_conform(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        bundle_dir="workstreams/WS-alpha-7/spec",
        ops=ops,
    )
    assert pr == 88
    names = [c[0] for c in ops.calls]
    assert names == ["find_pr", "ensure_branch", "commit_paths", "push_branch"]
    # содержимое действительно нормализовано, не только найден PR
    meta, _ = task_bridge.split_frontmatter(
        (target / "spec/WS-alpha-7-tasks.md").read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["decomposition"]


def test_deliver_conform_legacy_mismatch_refuses_before_ops(
    tmp_path: Path,
) -> None:
    """Находка 1 финального ревью: `--legacy-bundle` с несовпадающим
    фактическим составом отказывает RuntimeError'ом по составу И до
    любых вызовов ops (find_pr/ensure_branch) — `_check_bundle_composition`
    стоит в начале `deliver_conform`, до side-эффектов."""
    target = _target(tmp_path)  # полный 5-узловой бандл (с decomposition)
    _approved_tasks(target)
    ops = _ConformOps()
    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge.deliver_conform(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            bundle_dir="workstreams/WS-alpha-7/spec",
            ops=ops,
            legacy_bundle=3,
        )
    assert not any(c[0] == "find_pr" for c in ops.calls)
    assert not any(c[0] == "ensure_branch" for c in ops.calls)


# --- группировка по файлу цели (@id:task-bridge-beh-grouping, урок 8) -------

SAME_FILE_MD = """\
---
spec_stage: behaviour-spec
status: draft
---
# Behaviour

#### BEH-01: Открытие ханка
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/core/test_osc.py::test_open`

#### BEH-02: Закрытие ханка
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/core/test_osc.py::test_close`

#### BEH-03: Продолжение разбора
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/core/test_osc.py::test_continue`

#### BEH-04: Ошибка декодирования
`traces: [FR-03]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/runtime/test_purity.py::test_decode`
"""


def test_featureless_scenarios_merge_by_target_file() -> None:
    """Урок 8 (WS-disputatio-57: 7/15 red-unverifiable): смежные
    бес-Feature сценарии одного файла цели — одна задача; pytest-селектор
    `::…` при сравнении отброшен."""
    scenarios = task_bridge.parse_behaviour(SAME_FILE_MD)
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-09-03T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-001: Открытие ханка (+2 смежных BEH)" in text
    assert "- [ ] реализовать BEH-01" in text
    assert "- [ ] реализовать BEH-02" in text
    assert "- [ ] реализовать BEH-03" in text
    assert "### TASK-002: Ошибка декодирования" in text
    assert "### TASK-003:" not in text


def test_nonconsecutive_same_file_merges_into_owner_task() -> None:
    """Ревью disputatio#86 (контракт workstream-setup: один task-owner на
    тест-файл): НЕсмежная группа с тем же файлом вливается в задачу
    первого вхождения — иначе поздняя задача не выполнит RED-фазу из-за
    byte-lock ранней (класс TASK-014/015 WS-57)."""
    md = SAME_FILE_MD + """\

#### BEH-05: Снова про ханки
`traces: [FR-04]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/core/test_osc.py::test_again`
"""
    scenarios = task_bridge.parse_behaviour(md)
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-09-03T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-001: Открытие ханка (+3 смежных BEH)" in text
    assert "- [ ] реализовать BEH-05" in text
    assert "### TASK-002: Ошибка декодирования" in text
    assert "### TASK-003:" not in text


def test_transitive_file_chain_shares_single_owner() -> None:
    """Транзитивность: группа с файлами {A,B} связывает последующих
    владельцев обоих файлов в одну задачу."""
    md = """\
---
spec_stage: behaviour-spec
status: draft
---
# Behaviour

#### BEH-01: База
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::t1`

#### BEH-02: Мост
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_b.py::t2`

#### BEH-03: Через мост к базе
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::t3`

#### BEH-04: Хвост второго файла
`traces: [FR-03]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_b.py::t4`
"""
    scenarios = task_bridge.parse_behaviour(md)
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-09-03T12:00:00",
        design_blob="ab" * 20,
    )
    # BEH-01 (A) и BEH-03 (A) — один владелец; BEH-02 (B) — своя задача,
    # BEH-04 (B) вливается к ней
    assert "### TASK-001: База (+1 смежных BEH)" in text
    assert "### TASK-002: Мост (+1 смежных BEH)" in text
    assert "### TASK-003:" not in text


def test_featureless_does_not_merge_into_feature_group() -> None:
    """Feature-группировка владельца приоритетна: бес-Feature сценарий не
    вливается в Feature-группу даже при общем файле цели."""
    md = """\
---
spec_stage: behaviour-spec
status: draft
---
# Behaviour

## Feature: Каркас

#### BEH-01: Внутри Feature
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::test_one`

## Особые случаи

#### BEH-02: Вне Feature
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::test_two`
"""
    scenarios = task_bridge.parse_behaviour(md)
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-09-03T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-001: Каркас" in text
    assert "### TASK-002: Вне Feature" in text


def test_bridge_group_unions_two_existing_owners() -> None:
    """Приёмка PR #119 (minor): группа-«мост» с файлами {A, B} объединяет
    И уже разных владельцев A и B — у каждого файла ровно один владелец."""
    md = """\
---
spec_stage: behaviour-spec
status: draft
---
# Behaviour

#### BEH-01: Файл A
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::t1`

#### BEH-02: Файл B
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_b.py::t2`

#### BEH-03: Мост A
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_a.py::t3`

#### BEH-03: Мост B
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_b.py::t4`
"""
    scenarios = task_bridge.parse_behaviour(md)
    text = task_bridge.render_tasks(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/15-behaviour-spec.md",
        scenarios=scenarios,
        generated_at="2026-09-03T12:00:00",
        design_blob="ab" * 20,
    )
    assert "### TASK-001: Файл A (+3 смежных BEH)" in text
    assert "### TASK-002:" not in text


def test_render_deferred_without_reason_has_no_python_none() -> None:
    """Minor PR-ревью #145: deferred без reason: не рендерит литерал
    «None» в человеко-читаемую tasks-спеку (легаси/ручные бандлы, где S4
    не стоял на пути)."""
    from governance.task_bridge import _render_resolutions_section

    lines = _render_resolutions_section(
        "#### Q-07 · owner_role: architects · resolution: deferred\n"
    )
    joined = "\n".join(lines)
    assert "None" not in joined
    assert "Q-07" in joined and "не указана" in joined


# --- Task 8: render_tasks_dt — 1 DT = 1 задача, deliver на DT-пути --------

DT_BEHAVIOUR_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: product
---
# Behaviour

#### BEH-01: Первый
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/test_a.py`

#### BEH-02: Второй
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: integration` `owner: qa` \
`target: tests/test_a.py`

#### BEH-03: Третий
`traces: [FR-02]`
- **checked_by**: `status: planned` `kind: e2e` `owner: qa` \
`target: tests/test_c.py`
"""

DT_TWO_MD = """\
#### DT-01: Ядро · type: implement · owner: dev
scenarios: [BEH-01, BEH-02]
depends_on: []
parallel_group: core

Реализовать ядро.

#### DT-02: Расширение · type: implement · owner: dev
scenarios: [BEH-03]
depends_on: [DT-01]
parallel_group: core
"""


def test_render_dt_one_task_per_dt_with_bindings_and_edges() -> None:
    """DT-01 (BEH-01+BEH-02, group core), DT-02 (BEH-03, depends_on
    [DT-01]) ⇒ ровно TASK-001, TASK-002; TASK-001 несёт оба BEH и биндинг
    группы; TASK-002 несёт Depends on; DT-01 (без depends_on) — БЕЗ
    искусственной цепочки."""
    scenarios = task_bridge.parse_behaviour(DT_BEHAVIOUR_MD)
    dt_tasks, findings = decomposition_guard.parse_dt_tasks(DT_TWO_MD)
    assert findings == []
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="workstreams/WS-x-1/spec/30-decomposition.md",
        scenarios=scenarios,
        dt_tasks=dt_tasks,
        generated_at="2026-09-05T12:00:00",
        anchor_blob="ab" * 20,
    )
    assert "### TASK-001: Ядро" in text
    assert "### TASK-002: Расширение" in text
    assert "### TASK-003:" not in text
    assert "- [ ] реализовать BEH-01: Первый" in text
    assert "- [ ] реализовать BEH-02: Второй" in text
    assert (
        "проверка группы: tests/test_a.py (kind: integration) зелёные "
        "на BEH-01, BEH-02" in text
    )
    assert "**Depends on:** [TASK-001]" in text
    task1_block = text.split("### TASK-001:")[1].split("### TASK-002:")[0]
    assert "Depends on" not in task1_block


def test_render_dt_frontmatter_traces_decomposition_from_birth() -> None:
    """Рендер (не conform!) сразу пишет traces_to: [decomposition] и
    upstream_hashes: {decomposition: "<blob 30-decomposition.md>"}."""
    scenarios = task_bridge.parse_behaviour(DT_BEHAVIOUR_MD)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(DT_TWO_MD)
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="workstreams/WS-x-1/spec/30-decomposition.md",
        scenarios=scenarios,
        dt_tasks=dt_tasks,
        generated_at="2026-09-05T12:00:00",
        anchor_blob="cd" * 20,
    )
    meta, _body = task_bridge.split_frontmatter(text)
    assert meta["traces_to"] == ["decomposition"]
    assert meta["upstream_hashes"] == {"decomposition": "cd" * 20}


VERIFY_DT_MD = """\
#### DT-01: Проверка · type: verify · owner: qa
scenarios: [BEH-01]
depends_on: [DT-02]
delivered_by: [DT-02]
parallel_group: core

#### DT-02: Реализация · type: implement · owner: dev
scenarios: [BEH-02]
depends_on: []
parallel_group: core
"""


def test_verify_dt_renders_with_verify_first_mode() -> None:
    """verify-first доставлен (spec-runner#367 закрыт): DT с type: verify
    рендерится задачей с `**Mode:** verify_first` и `**Verifies:**` из
    checked_by-целей сценариев — spec-runner начнёт её живым прогоном
    группы. Fail-closed отказ эпохи блокера снят
    (@id:decomposition-verify-first-unblock)."""
    scenarios = task_bridge.parse_behaviour(DT_BEHAVIOUR_MD)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(VERIFY_DT_MD)
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1",
        subject="s",
        bundle_path="b/30-decomposition.md",
        scenarios=scenarios,
        dt_tasks=dt_tasks,
        generated_at="2026-09-05T12:00:00",
        anchor_blob="ab" * 20,
    )
    verify_block = text.split("### TASK-001:")[1].split("### TASK-002:")[0]
    assert "**Mode:** verify_first" in verify_block
    assert "**Verifies:** tests/test_a.py" in verify_block
    assert "Проверить сценарии BEH-01" in verify_block
    assert "- [ ] проверить BEH-01" in verify_block
    assert "реализовать BEH-01" not in verify_block
    # implement-задача режим не несёт
    implement_block = text.split("### TASK-002:")[1]
    assert "**Mode:**" not in implement_block
    assert "Реализовать сценарии BEH-02" in implement_block


def test_verify_dt_without_checked_by_targets_refuses() -> None:
    """Minor ревью PR #152: verify-DT, чьи сценарии не дают ни одной
    checked_by-цели, — отказ (нечего прогонять), не молчаливый Mode без
    Verifies."""
    beh_no_targets = (
        "---\nspec_stage: behaviour-spec\nstatus: draft\n"
        "owner_role: product\n---\n# B\n\n"
        "#### BEH-01: Без биндинга\n`traces: [FR-01]`\n"
    )
    dt = (
        "#### DT-01: V · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: []\n"
        "delivered_by: []\nparallel_group: solo\n"
    )
    scenarios = task_bridge.parse_behaviour(beh_no_targets)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(dt)
    with pytest.raises(RuntimeError, match="нечего прогонять"):
        task_bridge.render_tasks_dt(
            ws_id="WS-x-1", subject="s",
            bundle_path="b/30-decomposition.md",
            scenarios=scenarios, dt_tasks=dt_tasks,
            generated_at="2026-09-05T12:00:00", anchor_blob="ab" * 20,
        )


DECOMPOSITION_SHARED_FILE_MD = """\
---
spec_stage: decomposition
status: draft
version: 1
owner_role: tech-lead
traces_to: [design]
upstream_hashes:
  design: """ + "12" * 20 + """
---
## Задачи

#### DT-01: Первый · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo

#### DT-02: Второй · type: implement · owner: dev
scenarios: [BEH-02]
depends_on: []
parallel_group: solo
"""

SHARED_FILE_BEHAVIOUR_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: product
---
# Behaviour

#### BEH-01: Первый
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_shared.py::t1`

#### BEH-02: Второй
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: atp` `owner: qa` \
`target: tests/test_shared.py::t2`
"""


def test_dt_path_skips_merge_featureless(tmp_path: Path) -> None:
    """Два DT с checked_by-целями в одном файле НЕ сливаются мостом
    (_merge_featureless_by_target_file на DT-пути не применяется) — такой
    вход обязан быть отвергнут graph_findings ДО рендера: deliver зовёт
    graph_findings и поднимает RuntimeError со списком."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(SHARED_FILE_BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(
        DECOMPOSITION_SHARED_FILE_MD
    )
    ops = _StubOps()
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=ops,
            approved_by="a", approved_at="t",
        )
    message = str(exc_info.value)
    assert "single-owner" in message
    assert "tests/test_shared.py" in message
    assert not any(c[0] == "ensure_branch" for c in ops.calls)
    assert (
        bundle / "30-decomposition.md"
    ).read_text() == DECOMPOSITION_SHARED_FILE_MD


DECOMPOSITION_VERIFY_MD = """\
---
spec_stage: decomposition
status: draft
version: 1
owner_role: tech-lead
traces_to: [design]
upstream_hashes:
  design: """ + "12" * 20 + """
---
## Задачи

#### DT-01: Реализация · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo

#### DT-02: Проверка · type: verify · owner: qa
scenarios: [BEH-02]
depends_on: [DT-01]
delivered_by: [DT-01]
parallel_group: solo
"""


def test_deliver_verify_dt_now_delivers(
    tmp_path: Path,
) -> None:
    """verify-first доставлен: deliver на DT-пути с verify-DT больше не
    отказывает — tasks-спека рождается с verify_first-задачей."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_VERIFY_MD)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
    )
    assert pr is not None
    spec_text = (target / "spec" / "WS-alpha-7-tasks.md").read_text()
    assert "**Mode:** verify_first" in spec_text
    assert any(c[0] == "ensure_branch" for c in ops.calls)


def _target_legacy_5(
    tmp_path: Path, behaviour_md: str, decomposition_md: str
) -> Path:
    """Бандл из пяти узлов (00/10/15/20/30) — эра ДО раскатки
    acceptance-узла: соседний репо уже несёт design и decomposition, но
    acceptance ещё не раскатан (Task 7 плана acceptance-node)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(behaviour_md)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "30-decomposition.md").write_text(decomposition_md)
    return target


def test_legacy_5_invalid_dt_graph_refuses(tmp_path: Path) -> None:
    """Major круга 1 ревью спеки: `--legacy-bundle=5` идёт DT-путём, не
    молчаливым легаси-рендером `render_tasks` — 5-узловой бандл с
    невалидным графом DT (single-owner нарушен, по образцу
    `test_dt_path_skips_merge_featureless`) отказывает RuntimeError из
    `graph_findings` ДО создания ветки."""
    target = _target_legacy_5(
        tmp_path, SHARED_FILE_BEHAVIOUR_MD, DECOMPOSITION_SHARED_FILE_MD,
    )
    ops = _StubOps()
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.deliver(
            target_dir=str(target),
            repo_slug="owner/alpha",
            ws_id="WS-alpha-7",
            subject="s",
            bundle_dir="workstreams/WS-alpha-7/spec",
            base_ref="master",
            ops=ops,
            approved_by="a", approved_at="t",
            legacy_bundle=5,
        )
    message = str(exc_info.value)
    assert "single-owner" in message
    assert not any(c[0] == "ensure_branch" for c in ops.calls)


def test_legacy_5_goes_dt_path_with_graph_validation(tmp_path: Path) -> None:
    """Валидный граф DT на `--legacy-bundle=5` ⇒ доставка идёт
    `render_tasks_dt` — DT-провенанс в тексте, Mode: verify_first у
    verify-DT (по образцу `test_deliver_verify_dt_now_delivers`), якорь —
    decomposition как на полном DAG."""
    target = _target_legacy_5(tmp_path, BEHAVIOUR_MD, DECOMPOSITION_VERIFY_MD)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
        legacy_bundle=5,
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    spec_text = spec.read_text()
    assert "**Mode:** verify_first" in spec_text
    assert "(DT-" in spec_text
    meta, _body = task_bridge.split_frontmatter(spec_text)
    assert meta["traces_to"] == ["decomposition"]
    # 5-узловой легаси-состав: 25-acceptance.md НЕ входит в штамп/коммит —
    # этой эры узел ещё не существовал.
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == (
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
        "workstreams/WS-alpha-7/spec/30-decomposition.md",
        "spec/WS-alpha-7-tasks.md",
    )


def test_deliver_full_dag_renders_via_render_tasks_dt(tmp_path: Path) -> None:
    """Полный DAG идёт через render_tasks_dt (не render_tasks): рендер
    несёт DT-провенанс (dt_id, parallel_group) в задаче."""
    target = _target(tmp_path)
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
    )
    assert pr == 77
    text = (target / "spec/WS-alpha-7-tasks.md").read_text()
    assert "### TASK-001: Реализация" in text
    assert "(DT-01, группа solo)" in text
    assert "Source: workstreams/WS-alpha-7/spec/30-decomposition.md#DT-01" \
        in text


def test_parse_behaviour_reads_letter_suffixed_beh_id() -> None:
    """PR spec-runner#369 (major): BEH-18a молча выпадал из декомпозиции,
    а его checked_by приклеивался к предыдущему сценарию."""
    text = (
        "#### BEH-18: Обычный\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_x`\n\n"
        "#### BEH-18a: Вставленный ревью\n**checked_by** `kind: e2e` "
        "`target: tests/test_b.py::test_y`\n"
    )
    scenarios = task_bridge.parse_behaviour(text)
    assert [s.beh_id for s in scenarios] == ["BEH-18", "BEH-18a"]
    assert scenarios[0].checked_target == "tests/test_a.py::test_x"
    assert scenarios[1].checked_target == "tests/test_b.py::test_y"


def test_tasks_frontmatter_carries_owner_role() -> None:
    """Minor PR spec-runner#369: узел tasks профиля объявляет
    owner_role: stream-owner, approve его не дописывает — рендер обязан
    нести ключ с рождения (прецедент WS-341)."""
    from governance.task_bridge import _render_header

    lines = _render_header(
        "WS-X", "s", "2026-01-01T00:00:00Z", "0" * 40,
        anchor_node_id="decomposition",
    )
    assert "owner_role: stream-owner" in lines


def test_traces_to_renders_each_ref_in_own_brackets() -> None:
    """Major ревью PR spec-runner#369 (круг 2): [FR-02, FR-03] одной
    скобкой — парсер spec-runner (re.findall(r"\\[([A-Z]+-\\d+)\\]"))
    теряет трассируемость; формат FORMAT.md — [FR-02], [FR-03]."""
    import re

    scenarios = task_bridge.parse_behaviour(
        "#### BEH-01: Один\n`traces: [FR-02, FR-03]`\n"
        "**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_x`\n"
    )
    text = task_bridge.render_tasks(
        "WS-X", "s", "b/15-behaviour-spec.md", scenarios,
        "2026-01-01T00:00:00Z", "0" * 40,
    )
    assert "**Traces to:** [FR-02], [FR-03]" in text
    refs = re.findall(r"\[([A-Z]+-\d+)\]", text.split("**Traces to:**")[1])
    assert refs[:2] == ["FR-02", "FR-03"]


# --- Task 8 (acceptance-node): _render_acceptance_section, wired into
# deliver/render_tasks_dt -------------------------------------------------


def test_acceptance_section_lists_criteria() -> None:
    from governance.task_bridge import _render_acceptance_section

    acc = (
        "#### AC-01: Прогон первым · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\nпроза\n\n"
        "#### AC-02: Бюджет · verification: metric\n"
        "traces: [NFR-01]\nпроза\n"
    )
    lines = _render_acceptance_section(acc)
    joined = "\n".join(lines)
    assert "Критерии приёмки (уровень acceptance)" in joined
    assert "- **AC-01** (test): Прогон первым" in joined
    assert "- **AC-02** (metric): Бюджет" in joined


def test_acceptance_section_empty_input_renders_nothing() -> None:
    from governance.task_bridge import _render_acceptance_section

    assert _render_acceptance_section("") == []


def test_deliver_full_dag_embeds_acceptance_section(tmp_path: Path) -> None:
    """Полный DAG (acceptance в активном dag): tasks-спека несёт секцию
    AC из вмерженного 25-acceptance.md."""
    target = _target(tmp_path)
    ops = _StubOps()
    task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
        approved_by="a", approved_at="t",
    )
    text = (target / "spec/WS-alpha-7-tasks.md").read_text()
    assert "## Критерии приёмки (уровень acceptance)" in text
    assert "- **AC-01** (manual): Список виден" in text


def test_deliver_legacy_5_has_no_acceptance_section(tmp_path: Path) -> None:
    """`--legacy-bundle=5` (acceptance ещё не существовал этой эры) — БЕЗ
    секции критериев приёмки в tasks-спеке."""
    target5 = _target_legacy_5(tmp_path, BEHAVIOUR_MD, DECOMPOSITION_MD_LEGACY5)
    ops5 = _StubOps()
    task_bridge.deliver(
        target_dir=str(target5),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops5,
        approved_by="a", approved_at="t",
        legacy_bundle=5,
    )
    text5 = (target5 / "spec/WS-alpha-7-tasks.md").read_text()
    assert "Критерии приёмки" not in text5


# --- deliver_for_run: durable reconciliation (кнопка spec-loop) -------------


class _ReconOps(_StubOps):
    """Стаб с PR-поверхностью: find_pr/pr_facts поверх deliver-стаба."""

    def __init__(self, existing_pr: int | None = None) -> None:
        super().__init__()
        self.existing_pr = existing_pr

    def find_pr(
        self, repo_slug: str, branch: str, *, any_state: bool = False
    ) -> int | None:
        self.calls.append(("find_pr", branch))
        return self.existing_pr

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts", pr))
        return {
            "state": "MERGED",
            "mergedBy": {"login": "ai-prosto"},
            "mergedAt": "2026-09-07T00:00:00Z",
        }


def _recon_state(tmp_path: Path, monkeypatch, **kw):
    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    target = _target(tmp_path)
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        target_dir=str(target),
        bundle_dir="workstreams/WS-alpha-7/spec",
        profile=None,
        run_id="r-recon",
    )
    state.status = kw.get("status", "completed")
    state.pr = kw.get("pr", 5)
    state.base_ref = "master"
    if "op" in kw:
        state.ops["tasks-deliver"] = kw["op"]
    rs.save(state)
    return state


def test_deliver_for_run_write_ahead_op_and_completion(
    tmp_path: Path, monkeypatch
) -> None:
    """Свежий прогон: op tasks-deliver пишется started ДО create_draft_pr,
    completed с номером PR — после; повторный вызов не создаёт PR снова."""
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ReconOps()
    pr = task_bridge.deliver_for_run(state, ops)
    assert pr == 77
    saved = rs.load("r-recon")
    assert saved.ops["tasks-deliver"] == {"status": "completed", "pr": 77}
    # повтор: op completed → ни одного нового эффекта
    ops2 = _ReconOps()
    assert task_bridge.deliver_for_run(rs.load("r-recon"), ops2) == 77
    assert ops2.calls == []


def test_deliver_for_run_adopts_existing_pr_by_branch(
    tmp_path: Path, monkeypatch
) -> None:
    """PR по ветке spec/<ws-id>-tasks уже есть (op потерян/started) —
    принимается как доставка, новый PR не создаётся."""
    from governance import run_state as rs

    state = _recon_state(
        tmp_path, monkeypatch, op={"status": "started"}
    )
    ops = _ReconOps(existing_pr=91)
    assert task_bridge.deliver_for_run(state, ops) == 91
    assert ("find_pr", "spec/WS-alpha-7-tasks") in ops.calls
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)
    assert rs.load("r-recon").ops["tasks-deliver"] == {
        "status": "completed", "pr": 91,
    }


def test_deliver_for_run_refuses_non_completed_status(
    tmp_path: Path, monkeypatch
) -> None:
    state = _recon_state(tmp_path, monkeypatch, status="waiting_human_merge")
    with pytest.raises(RuntimeError, match="completed"):
        task_bridge.deliver_for_run(state, _ReconOps())


def test_deliver_for_run_adopts_merged_pr_not_only_open(
    tmp_path: Path, monkeypatch
) -> None:
    """major терм. ревью #156: PR по ветке уже ВМЕРЖЕН (открытых нет) —
    доставка была; повтор обязан принять её, а не перегенерировать спеку
    с откатом approve в draft вторым PR-ом."""
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch)

    class _MergedOps(_ReconOps):
        def find_pr(
            self, repo_slug: str, branch: str, *, any_state: bool = False
        ) -> int | None:
            self.calls.append(("find_pr", branch, any_state))
            return 91 if any_state else None

    ops = _MergedOps()
    assert task_bridge.deliver_for_run(state, ops) == 91
    assert ("find_pr", "spec/WS-alpha-7-tasks", True) in ops.calls
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)
    assert rs.load("r-recon").ops["tasks-deliver"] == {
        "status": "completed", "pr": 91,
    }


def test_deliver_for_run_closed_unmerged_pr_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    state = _recon_state(tmp_path, monkeypatch)

    class _ClosedOps(_ReconOps):
        def __init__(self) -> None:
            super().__init__(existing_pr=91)

        def pr_facts(self, repo_slug: str, pr: int) -> dict:
            return {"state": "CLOSED", "mergedBy": None, "mergedAt": None}

    with pytest.raises(RuntimeError, match="закрыт"):
        task_bridge.deliver_for_run(state, _ClosedOps())
