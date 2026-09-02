"""Тесты task_bridge: behaviour-spec → draft tasks.md-спека PR-ом.

Шаг 3 плана развития варианта 1 (решение владельца 2026-08-31): замкнуть
цикл «предмет → спецификация → исполнители». Мост читает вмерженный
behaviour-spec бандла и генерирует managed-спеку `spec/<ws-id>-tasks.md`
(status: draft — не исполняется до человеческого approve, инвариант №4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import task_bridge

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
        behaviour_blob="ab" * 20,
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
    # один коммит: штамп трёх файлов бандла + файл спеки
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == (
        "workstreams/WS-alpha-7/spec/00-charter.md",
        "workstreams/WS-alpha-7/spec/10-requirements.md",
        "workstreams/WS-alpha-7/spec/15-behaviour-spec.md",
        "spec/WS-alpha-7-tasks.md",
    )
    assert ("push_branch", "spec/WS-alpha-7-tasks") in ops.calls
    assert "draft" in ops.pr_body.lower()
    assert "штамп статусов" in ops.pr_body
    # Пин tasks-спеки — blob behaviour-spec ПОСЛЕ штампа (иначе протух бы
    # в том же PR)
    from governance.stale_adapter import blob_sha1
    stamped_blob = blob_sha1(
        (target / "workstreams/WS-alpha-7/spec/15-behaviour-spec.md")
        .read_text(encoding="utf-8")
    )
    meta, _body = task_bridge.split_frontmatter(
        spec.read_text(encoding="utf-8")
    )
    assert meta["traces_to"] == ["behaviour-spec"]
    assert meta["upstream_hashes"] == {"behaviour-spec": stamped_blob}


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
        behaviour_blob="ab" * 20,
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
        behaviour_blob="ab" * 20,
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
    """Урок 2 ретроспективы: штамп статусов + перепиновка цепочки. Каждый
    следующий файл пинует blob предыдущего ПОСЛЕ его штампа."""
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
    beh_meta, _ = task_bridge.split_frontmatter(
        (bundle / "15-behaviour-spec.md").read_text(encoding="utf-8")
    )
    assert beh_meta["status"] == "approved"
    assert beh_meta["upstream_hashes"]["requirements"] == blob_sha1(req_text)


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


def test_conform_normalizes_after_approve(tmp_path: Path) -> None:
    """Урок 1: `spec approve` дописывает design из lite-профиля —
    нормализация возвращает форму governance-профиля и свежий пин."""
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
        "traces_to:\n- behaviour-spec\n- design\n"
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
    assert meta["traces_to"] == ["behaviour-spec"]
    assert meta["upstream_hashes"] == {
        "behaviour-spec": blob_sha1(
            (target / "workstreams/WS-alpha-7/spec/15-behaviour-spec.md")
            .read_text(encoding="utf-8")
        )
    }
    # поля approve владельца не тронуты
    assert meta["status"] == "approved"
    assert meta["approved_by"] == "andrei-shtanakov"
    assert "## Milestone 1: s" in body
    # идемпотентность
    assert task_bridge.conform_approved(
        str(target), "WS-alpha-7", "workstreams/WS-alpha-7/spec"
    ) is False


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
    assert meta["traces_to"] == ["behaviour-spec"]


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
        behaviour_blob="ab" * 20,
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
        behaviour_blob="ab" * 20,
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
        behaviour_blob="ab" * 20,
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
        behaviour_blob="ab" * 20,
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
        behaviour_blob="ab" * 20,
    )
    assert "### TASK-001: Файл A (+3 смежных BEH)" in text
    assert "### TASK-002:" not in text
