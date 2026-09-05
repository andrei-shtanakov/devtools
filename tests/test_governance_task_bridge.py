"""Тесты task_bridge: behaviour-spec → draft tasks.md-спека PR-ом.

Шаг 3 плана развития варианта 1 (решение владельца 2026-08-31): замкнуть
цикл «предмет → спецификация → исполнители». Мост читает вмерженный
behaviour-spec бандла и генерирует managed-спеку `spec/<ws-id>-tasks.md`
(status: draft — не исполняется до человеческого approve, инвариант №4).
"""

from __future__ import annotations

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

#### Q-03 · owner_role: architects · resolution: deferred
reason: Нужны замеры нагрузки перед выбором шардирования.
"""

DECOMPOSITION_MD = """\
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
    assert "generated_at: 2026-08-31T12:00:00" in text
    assert "## Milestone 1: Наблюдаемость (alpha#7)" in text
    assert "### TASK-001: Просмотр списка" in text
    assert "### TASK-002: Пустое состояние" in text
    assert (
        "Source: workstreams/WS-alpha-7/spec/15-behaviour-spec.md#BEH-01"
        in text
    )
    assert "**Traces to:** [FR-01, NFR-02]" in text
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
    # один коммит: штамп пяти файлов бандла (полный DAG до decomposition) +
    # файл спеки
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == (
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "workstreams/WS-alpha-7/spec/20-design.md",
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
    assert "## Решения открытых вопросов (уровень design)" in spec.read_text()
    assert (
        "- **Q-03 (deferred):** reason: Нужны замеры нагрузки перед "
        "выбором шардирования." in spec.read_text()
    )
    # Task 7, low review #2: resolved-ветка несёт обоснование (reason),
    # построчная проверка — не только заголовок секции/deferred-строка.
    assert (
        "- **Q-01:** Выбран REST — синхронный вызов проще для MVP."
        in spec.read_text()
    )


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
    # traces группы — объединение без дублей
    assert "**Traces to:** [FR-01, FR-02]" in text
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
    behaviour-spec). Каждый следующий файл пинует blob предыдущего(-их)
    ПОСЛЕ его штампа."""
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
    decomposition_meta, _ = task_bridge.split_frontmatter(
        (bundle / "30-decomposition.md").read_text(encoding="utf-8")
    )
    assert decomposition_meta["status"] == "approved"
    assert decomposition_meta["upstream_hashes"]["design"] == blob_sha1(
        design_text
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


# --- Task 7 (decomposition-node): узел decomposition в DAG,
# --legacy-bundle=3|4 --------------------------------------------------


def test_bundle_dag_terminates_at_decomposition() -> None:
    assert task_bridge._BUNDLE_DAG[-1] == ("30-decomposition.md", ("design",))
    assert task_bridge._ANCHOR_NODE_ID == "decomposition"


def test_dag_for_none_is_full_dag() -> None:
    assert task_bridge._dag_for(None) == task_bridge._BUNDLE_DAG


def test_dag_for_legacy_values_are_exact_prefixes() -> None:
    assert task_bridge._dag_for(3) == task_bridge._BUNDLE_DAG[:3]
    assert task_bridge._dag_for(4) == task_bridge._BUNDLE_DAG[:4]


def test_dag_for_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="3 или 4"):
        task_bridge._dag_for(5)


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
        task_bridge.main(["--run-id", "r-x", "--legacy-bundle", "5"])


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

    def find_pr(self, repo_slug: str, branch: str) -> int | None:
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


def test_verify_dt_fails_closed_until_spec_runner_367() -> None:
    """DT с type: verify ⇒ render_tasks_dt поднимает RuntimeError с
    "verify-first", "spec-runner#367", "@blocked_by" (fail-closed до
    доставки шва)."""
    scenarios = task_bridge.parse_behaviour(DT_BEHAVIOUR_MD)
    dt_tasks, _ = decomposition_guard.parse_dt_tasks(VERIFY_DT_MD)
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge.render_tasks_dt(
            ws_id="WS-x-1",
            subject="s",
            bundle_path="b/30-decomposition.md",
            scenarios=scenarios,
            dt_tasks=dt_tasks,
            generated_at="2026-09-05T12:00:00",
            anchor_blob="ab" * 20,
        )
    message = str(exc_info.value)
    assert "verify-first" in message
    assert "spec-runner#367" in message
    assert "@blocked_by" in message


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


def test_deliver_verify_dt_fails_closed_before_mutation(
    tmp_path: Path,
) -> None:
    """deliver на DT-пути с verify-DT ⇒ RuntimeError И
    ops.ensure_branch НЕ вызывался, файлы бандла не изменены (verify-first
    spec-runner#367 ещё не доставлен)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_VERIFY_MD)
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
    assert "verify-first" in message
    assert "spec-runner#367" in message
    assert "@blocked_by" in message
    assert not any(c[0] == "ensure_branch" for c in ops.calls)
    assert (
        bundle / "30-decomposition.md"
    ).read_text() == DECOMPOSITION_VERIFY_MD
    assert not (target / "spec" / "WS-alpha-7-tasks.md").exists()


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
