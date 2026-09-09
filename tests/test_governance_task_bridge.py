"""Тесты task_bridge: behaviour-spec → draft tasks.md-спека PR-ом.

Шаг 3 плана развития варианта 1 (решение владельца 2026-08-31): замкнуть
цикл «предмет → спецификация → исполнители». Мост читает вмерженный
behaviour-spec бандла и генерирует managed-спеку `spec/<ws-id>-tasks.md`
(status: draft — не исполняется до человеческого approve, инвариант №4).
"""

from __future__ import annotations

import re
import shutil
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
        # Коммит двигает HEAD — до и после `commit_paths` `rev_parse`
        # обязан отвечать РАЗНОЕ, иначе тест на записанный `head_sha`
        # не отличает «взяли SHA свежего коммита» от «взяли базу».
        self.committed = False

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
        self.committed = True

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

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        # Дефолт для стабов, у которых живого git нет (Task 7 supersede):
        # переопределяющие подклассы (_recover_commit-тесты) уже несут
        # собственный rev_parse, этот — только чтобы не падать AttributeError.
        # "HEAD" отвечает синтетическим SHA (Task 7b): base_sha теперь
        # fail-closed при None (§I2), и «живого git нет» для HEAD означало
        # бы отказ ещё до предмета теста; прочие ref'ы (ветка ревизии) —
        # по-прежнему None, то есть «коммита ещё нет». ПОСЛЕ коммита SHA
        # другой (фикс-круг 2): иначе «head_sha = SHA коммита» и
        # «head_sha = база» неотличимы.
        if ref != "HEAD":
            return None
        return "commit-sha-1" if self.committed else "base-sha-1"

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        # Тот же смысл: _previous_dag дёргает show_file на пути «легаси-v1
        # без записи dag» — без живого git ответ «спеки в base нет».
        return None


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


def test_split_frontmatter_normalizes_yaml_error_to_value_error() -> None:
    """Битый YAML ВНУТРИ корректных разделителей — тоже `ValueError`.

    `yaml.YAMLError` не подкласс `ValueError`, поэтому вызывающие,
    ловящие «frontmatter не разобрать» (§I6 `_previous_tasks_version`,
    §I8 `_previous_dag`), мимо него проваливались сырым трейсбеком
    PyYAML — мимо `except RuntimeError` в `main`. Разделители здесь
    целые: ветка «нет frontmatter вовсе» тут ни при чём."""
    broken = "---\nspec_stage: tasks\nversion: [1, 2\n---\n\nbody\n"
    with pytest.raises(ValueError, match="невалидный YAML"):
        task_bridge.split_frontmatter(broken)


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


def test_prospective_anchor_matches_real_stamp(tmp_path: Path) -> None:
    """Проспективный anchor равен тому, что даст фактический штамп."""
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    prospective = task_bridge._prospective_anchor(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z", None,
    )
    task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z",
    )
    actual = blob_sha1(
        (target / "workstreams/WS-alpha-7/spec/30-decomposition.md")
        .read_text(encoding="utf-8")
    )
    assert prospective == actual


def test_prospective_anchor_writes_nothing(tmp_path: Path) -> None:
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    anchor_file = target / "workstreams/WS-alpha-7/spec/30-decomposition.md"
    before = blob_sha1(anchor_file.read_text(encoding="utf-8"))
    task_bridge._prospective_anchor(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z", None,
    )
    after = blob_sha1(anchor_file.read_text(encoding="utf-8"))
    assert before == after


def test_prospective_anchor_refuses_incomplete_bundle(tmp_path: Path) -> None:
    """Легаси-бандл + забытый `--legacy-bundle=5` ⇒ RuntimeError с процедурой.

    Проспективный штамп копирует в теневой каталог файлы ЗАЯВЛЕННОГО DAG:
    без проверки фактического состава в `target_dir` недостающий узел
    ронял сырой `FileNotFoundError` из `read_text`, а `main` ловит только
    RuntimeError — оператор получал трейсбек вместо диагностики. Перенести
    проверку внутрь теневого каталога бесполезно: там лежит ровно
    заявленное подмножество, и она вырождается в тождество.
    """
    target = _target_legacy_5(tmp_path, BEHAVIOUR_MD, DECOMPOSITION_MD_LEGACY5)
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge._prospective_anchor(
            str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
            "2026-09-09T05:00:00Z", None,
        )
    message = str(exc_info.value)
    assert "25-acceptance.md" in message
    assert "--legacy-bundle=3|4|5" in message


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


def test_verify_dt_renders_union_of_checked_by_and_verifies() -> None:
    """Major ревью PR #161, round 6 (контракт владельца): verify-DT
    ВЛАДЕЕТ своими checked_by-целями (scenarios) И НАБЛЮДАЕТ файлы из
    verifies — **Verifies:** обязана нести ОБЕ группы (объединение,
    дедуп), иначе собственный тест-файл DT молча выпадает из verify_first-
    прогона, хотя чек-лист той же задачи требует его зелёным. Порядок
    детерминирован: СНАЧАЛА собственные checked_by-цели (порядок
    scenarios), ПОТОМ verifies (порядок объявления)."""
    scenarios = task_bridge.parse_behaviour(DT_BEHAVIOUR_MD)
    dt = (
        "#### DT-01: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: []\n"
        "delivered_by: []\nparallel_group: solo\n"
        "verifies:\n"
        "  - tests/test_z.py\n"
        "  - tests/test_y.py\n"
        "  - tests/test_z.py\n"
    )
    dt_tasks, findings = decomposition_guard.parse_dt_tasks(dt)
    assert findings == []
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1", subject="s", bundle_path="b/30-decomposition.md",
        scenarios=scenarios, dt_tasks=dt_tasks,
        generated_at="2026-09-05T12:00:00", anchor_blob="ab" * 20,
    )
    # tests/test_a.py — собственная checked_by-цель BEH-01 (DT_BEHAVIOUR_MD)
    # — ОБЯЗАНА присутствовать, идёт ПЕРВОЙ; verifies — следом, в порядке
    # объявления, с дедупом повторного tests/test_z.py.
    assert (
        "**Verifies:** tests/test_a.py, tests/test_z.py, tests/test_y.py"
        in text
    )


def test_verify_dt_union_dedups_by_full_selector_not_by_file() -> None:
    """Major ревью PR #161, round 11 — откат round-8/9 «дедупа по файлу»
    (major-регресс, признан ошибкой владельца): дедуп union'а обязан
    идти ПО ПОЛНОЙ СТРОКЕ селектора. checked_by-цель сценария несёт
    полный pytest-селектор (tests/test_a.py::test_five); verifies
    объявляет тот же файл ГОЛЫМ путём — это РАЗНАЯ строка, ОБЕ остаются
    в **Verifies:** (spec-runner резолвит пересечение сам); срез `::`
    для сверки владения — дело только гарда, не рендера."""
    scenarios = task_bridge.parse_behaviour(
        "#### BEH-05: Пять\n`traces: [FR-01]`\n"
        "**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_five`\n\n"
        "#### BEH-06: Шесть\n`traces: [FR-01]`\n"
        "**checked_by** `kind: integration` "
        "`target: tests/test_b.py::test_six`\n"
    )
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-05]\ndepends_on: []\n"
        "delivered_by: []\nparallel_group: solo\n"
        "verifies:\n  - tests/test_a.py\n  - tests/test_b.py\n"
    )
    dt_tasks, findings = decomposition_guard.parse_dt_tasks(dt)
    assert findings == []
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1", subject="s", bundle_path="b/30-decomposition.md",
        scenarios=scenarios, dt_tasks=dt_tasks,
        generated_at="2026-09-05T12:00:00", anchor_blob="ab" * 20,
    )
    assert (
        "**Verifies:** tests/test_a.py::test_five, tests/test_a.py, "
        "tests/test_b.py" in text
    )


def test_verify_dt_union_keeps_both_selectors_of_own_scenarios_sharing_a_file() -> None:
    """Major ревью PR #161, round 11 (регресс, введённый round-8/9
    «дедупом по файлу»): single-owner (decomposition_guard) требует, чтобы
    ДВА сценария в ОДНОМ файле с РАЗНЫМИ селекторами принадлежали ОДНОЙ
    задаче (форма SHARED_FILE_BEHAVIOUR_MD — уже прожита в
    test_dt_path_skips_merge_featureless). Для verify-DT с такими двумя
    сценариями чек-лист требует ОБА селектора зелёными — **Verifies:**
    обязана нести оба, дедуп по файлу молча ронял бы второй."""
    scenarios = task_bridge.parse_behaviour(
        "#### BEH-01: Один\n`traces: [FR-01]`\n"
        "**checked_by** `kind: atp` `target: tests/test_shared.py::t1`\n\n"
        "#### BEH-02: Два\n`traces: [FR-01]`\n"
        "**checked_by** `kind: atp` `target: tests/test_shared.py::t2`\n"
    )
    dt_verify = (
        "#### DT-02: A · type: verify · owner: qa\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "delivered_by: []\nparallel_group: solo\n"
    )
    dt_tasks_verify, findings_verify = decomposition_guard.parse_dt_tasks(
        dt_verify
    )
    assert findings_verify == []
    text = task_bridge.render_tasks_dt(
        ws_id="WS-x-1", subject="s", bundle_path="b/30-decomposition.md",
        scenarios=scenarios, dt_tasks=dt_tasks_verify,
        generated_at="2026-09-05T12:00:00", anchor_blob="ab" * 20,
    )
    assert (
        "**Verifies:** tests/test_shared.py::t1, tests/test_shared.py::t2"
        in text
    )


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
verifies: [tests/test_y.py]
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


def test_deliver_does_not_check_verifies_path_existence_on_disk(
    tmp_path: Path,
) -> None:
    """Round 11 ревью PR #161 — откат ошибочного round-10 решения:
    deliver() НЕ проверяет, существует ли путь из verifies на диске
    target_dir. По конструкции closure-инварианта (graph_findings)
    владелец файла из verifies создаёт его СВОЕЙ задачей ПОСЛЕ доставки
    tasks-спеки, не до неё — проверка существования на момент deliver()
    ломала бы доставку ЛЮБОГО бандла, где verify-DT наблюдает файл более
    поздней задачи. Владение гарантирует graph closure-инвариант;
    существование в МОМЕНТ ПРОГОНА — забота spec-runner (spec-runner#402),
    не гейта доставки."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_VERIFY_MD)
    # НЕ создаём tests/test_y.py — verifies указывает на файл, которого
    # ещё нет на диске (его создаст владелец DT позже, при исполнении).
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
    assert "tests/test_y.py" in spec_text


# decomposition авторенный ДО раскатки поля verifies (round 3 ревью
# PR #161, finding 4): DT-02 (type: verify) БЕЗ verifies вовсе — ровно тот
# легаси-вход, что fatal-находка формы (round 1) отказывала бы на
# graph_findings ДО рендера, делая заявленный checked_by-fallback
# render_tasks_dt недостижимым.
DECOMPOSITION_VERIFY_LEGACY_MD = """\
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


def test_deliver_legacy_verify_dt_without_verifies_still_delivers(
    tmp_path: Path,
) -> None:
    """Round 3/7 ревью PR #161 (контракт владельца): legacy decomposition
    с type: verify DT, авторенным ДО раскатки verifies (поле отсутствует
    вовсе, но собственная checked_by-цель ЕСТЬ — легаси-форма, см.
    test_verify_with_checked_by_target_but_without_verifies_is_legacy_ok
    в tests/test_governance_decomposition_guard.py), обязан по-прежнему
    доставляться — не fatal-ит deliver(), и render_tasks_dt рендерит
    **Verifies:** из checked_by-цели сценария (fallback), не из
    structural verifies."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(
        DECOMPOSITION_VERIFY_LEGACY_MD
    )
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
    # BEH-02 (BEHAVIOUR_MD) checked_by target — tests/test_y.py: fallback
    # взял его из сценария, раз структурного verifies на DT-02 нет.
    assert "**Verifies:** tests/test_y.py" in spec_text


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
    """Стаб с PR-поверхностью: find_pr/pr_facts поверх deliver-стаба.

    `pr_state` — настраиваемое состояние PR, которое отдаёт `pr_facts`
    (дефолт "MERGED" сохраняет прежнее поведение старых вызовов без
    аргумента); фикс-круг 1 ревью Task 6, находка minor #2 — раньше было
    захардкожено, и тест на completed+OPEN не мог отличить срабатывание
    шортката `status == "completed"` от реального опроса PR.
    """

    def __init__(
        self, existing_pr: int | None = None, pr_state: str = "MERGED"
    ) -> None:
        super().__init__()
        self.existing_pr = existing_pr
        self.pr_state = pr_state

    def find_pr(
        self, repo_slug: str, branch: str, *, any_state: bool = False
    ) -> int | None:
        self.calls.append(("find_pr", branch))
        return self.existing_pr

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts", pr))
        return {
            "state": self.pr_state,
            "mergedBy": {"login": "ai-prosto"},
            "mergedAt": "2026-09-07T00:00:00Z",
        }


def _recon_state(tmp_path: Path, monkeypatch, **kw):
    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    target = _target(tmp_path)
    # Базовая доставленная tasks-спека v1 (Task 7 supersede): по конструкции
    # фикстуры v1 уже "доставлена" (`state.pr = 5`), значит на base уже лежит
    # spec/<ws-id>-tasks.md — без него _previous_tasks_version не из чего
    # читать монотонность версии. Тесты, которым нужна другая версия
    # (например, test_supersede_version_is_monotonic), перезаписывают файл
    # сами.
    (target / "spec").mkdir(parents=True, exist_ok=True)
    (target / "spec/WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nstatus: draft\nversion: 1\n---\n\nbody\n",
        encoding="utf-8",
    )
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
    # Предмет теста — write-ahead и номер PR; полная форма записи (включая
    # anchor, §I2) утверждается отдельно — `..._records_anchor_of_stamp`.
    op = saved.ops["tasks-deliver"]
    assert (op["status"], op["pr"]) == ("completed", 77)
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
    # Предмет — принятие существующего PR как доставки; anchor принятого PR
    # утверждается отдельно (`..._reconciled_pr_records_anchor_from_head`).
    op = rs.load("r-recon").ops["tasks-deliver"]
    assert (op["status"], op["pr"]) == ("completed", 91)


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
    op = rs.load("r-recon").ops["tasks-deliver"]
    assert (op["status"], op["pr"]) == ("completed", 91)


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


def test_deliver_for_run_records_anchor_of_stamp(
    tmp_path: Path, monkeypatch
) -> None:
    """Новая v1 сохраняет `pr`, `anchor` (§I2) и `content_anchor` (§I5).

    Без `content_anchor` сверка §I5 у первого переиздания невозможна, и
    любой воркстрим уходит compatibility-путём §6: `--supersede` сразу
    после обычной доставки завёл бы v2 без единого изменения апстрима.
    Два поля здесь именно ДВА: `anchor` — точные доставленные байты,
    `content_anchor` — содержание без провенанса.
    """
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch)
    # Ожидание считается ДО доставки, на НЕпроштампованном дереве и тем же
    # способом, каким его посчитает переиздание (§I2). Подпись — та, что
    # `_ReconOps.pr_facts` отдаёт для бандл-PR: другой штамп дал бы другие
    # байты анкера, и совпадение ничего бы не значило.
    expected = task_bridge._prospective_anchor(
        state.target_dir, state.bundle_dir, "ai-prosto",
        "2026-09-07T00:00:00Z", None,
    )
    # А вот `content_anchor` от подписи не зависит вовсе — считается без неё.
    expected_content = task_bridge._content_anchor(
        state.target_dir, state.bundle_dir, None
    )
    assert task_bridge.deliver_for_run(state, _ReconOps()) == 77
    assert rs.load("r-recon").ops["tasks-deliver"] == {
        "status": "completed", "pr": 77, "anchor": expected,
        "content_anchor": expected_content,
    }


def test_deliver_for_run_reconciled_pr_records_anchor_from_head(
    tmp_path: Path, monkeypatch
) -> None:
    """Восстановленная v1 (принят существующий PR) тоже получает оба якоря.

    Пересчитывать штамп на этом пути нечем (`approved_by`/`approved_at` не
    вычислены) и незачем: байты, ушедшие в PR, уже лежат в его
    head-коммите — `anchor` читается ОТТУДА, с пути анкера активного DAG.
    `content_anchor` — оттуда же, но по СОДЕРЖИМОМУ всех узлов DAG
    (`show_file`): канонизации нужен текст frontmatter, blob-хеша ей мало.

    Значение обязано совпасть с тем, которое записала бы новая доставка
    того же дерева, — иначе первое же переиздание после реконсиляции
    объявило бы содержание изменившимся на пустом месте.
    """
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch, op={"status": "started"})

    class _HeadOps(_ReconOps):
        def __init__(self) -> None:
            super().__init__(existing_pr=91)
            self.asked: list[tuple[str, str]] = []
            self.shown: list[tuple[str, str]] = []

        def pr_facts(self, repo_slug: str, pr: int) -> dict:
            facts = super().pr_facts(repo_slug, pr)
            return {**facts, "headRefOid": "head-91"}

        def blob_in_commit(
            self, target_dir: str, sha: str, rel_path: str
        ) -> str | None:
            self.asked.append((sha, rel_path))
            return "блоб-анкера-из-PR"

        def show_file(
            self, target_dir: str, ref: str, path: str
        ) -> str | None:
            # head-коммит PR-а несёт то же дерево бандла, что и рабочее:
            # состав узлов реалистичен, а аргументы сверяются — спросив
            # не тот ref, прод получил бы None и молча ушёл в §6.
            self.shown.append((ref, path))
            if ref != "head-91":
                return None
            return (Path(target_dir) / path).read_text(encoding="utf-8")

    ops = _HeadOps()
    expected_content = task_bridge._content_anchor(
        state.target_dir, state.bundle_dir, None
    )
    assert task_bridge.deliver_for_run(state, ops) == 91
    assert ops.asked == [
        ("head-91", "workstreams/WS-alpha-7/spec/30-decomposition.md")
    ]
    assert ops.shown == [
        ("head-91", f"workstreams/WS-alpha-7/spec/{fname}")
        for fname, _ in task_bridge._BUNDLE_DAG
    ]
    assert rs.load("r-recon").ops["tasks-deliver"] == {
        "status": "completed", "pr": 91, "anchor": "блоб-анкера-из-PR",
        "content_anchor": expected_content,
    }


def test_deliver_for_run_reconciled_pr_without_head_stays_open(
    tmp_path: Path, monkeypatch
) -> None:
    """Байты PR-а недоступны (`headRefOid` не отдан) → оба якоря `None`.

    Fail-open намеренно: переиздание уйдёт §6-путём, как и до записи
    якорей, а сегодня успешно реконсилируемая доставка не получает
    нового отказа.
    """
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch, op={"status": "started"})
    op = task_bridge.deliver_for_run(state, _ReconOps(existing_pr=91))
    assert op == 91
    assert rs.load("r-recon").ops["tasks-deliver"] == {
        "status": "completed", "pr": 91, "anchor": None,
        "content_anchor": None,
    }


# --- ревизии в леджере: нумерация, чтение, запись намерения (Task 2) -----


def test_revision_numbering_starts_at_two_and_grows(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    assert tb._next_revision(state) == 2
    tb._complete_revision(state, 2, pr=10, anchor="a")
    assert tb._next_revision(state) == 3
    # ТРЕТЬЯ запись обязательна (F-07): пока в леджере одна ревизия,
    # `revs[-1]` и `revs[0]` совпадают, и «нумеруем от старшей» не
    # проверено — следующий номер повторял бы уже занятый.
    tb._complete_revision(state, 3, pr=11, anchor="b")
    assert tb._next_revision(state) == 4
    # abandoned тоже занимает номер: заводить v3 поверх брошенной v3
    # значит переписать терминальную запись (§I4).
    tb._start_revision(state, 4, {"branch": "b4", "base_sha": "s"})
    tb._abandon_revision(state, 4, "оператор закрыл PR")
    assert tb._next_revision(state) == 5


def test_last_delivery_prefers_highest_revision(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    # v1 явно засеян через "op" (тот же конвенция, что у соседних
    # deliver_for_run тестов) — дефолт _recon_state оставляет ops пустым,
    # и трогать этот дефолт нельзя: три соседних deliver_for_run теста
    # полагаются на «свежий», ещё не completed op.
    state = _recon_state(
        tmp_path, monkeypatch, op={"status": "completed", "pr": 5}
    )
    n, op = tb._last_delivery(state)
    assert (n, op["pr"]) == (1, 5)
    tb._complete_revision(state, 2, pr=10, anchor="a")
    n2, op2 = tb._last_delivery(state)
    assert (n2, op2["pr"]) == (2, 10)
    # ТРЕТЬЯ запись (F-07): при ОДНОЙ ревизии в леджере обход по
    # возрастанию и по убыванию неразличимы, и «предпочитает старшую» не
    # было проверено ничем. Регрессия здесь направила бы `supersedes` и
    # сверку §I5 на устаревшую ревизию, начиная с третьего переиздания.
    tb._complete_revision(state, 3, pr=11, anchor="b")
    n3, op3 = tb._last_delivery(state)
    assert (n3, op3["pr"]) == (3, 11)


def test_last_delivery_prefers_highest_completed_over_abandoned(
    tmp_path, monkeypatch
):
    """Старшая ЗАВЕРШЁННАЯ, а не старшая вообще: v4 брошена, доставка — v3.

    Штатный след операторского выхода `--abandon-revision N`: над
    завершённой v3 висит терминальная v4. Взять её за предыдущую доставку
    значит уйти в §I5 без `anchor` (fail-open) и записать `supersedes` на
    недоставку."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(
        tmp_path, monkeypatch, op={"status": "completed", "pr": 5}
    )
    tb._complete_revision(state, 2, pr=10, anchor="a")
    tb._complete_revision(state, 3, pr=11, anchor="b")
    tb._start_revision(state, 4, {"branch": "b4", "base_sha": "s"})
    tb._abandon_revision(state, 4, "оператор закрыл PR вручную")
    n, op = tb._last_delivery(state)
    assert (n, op["pr"]) == (3, 11)


def test_last_delivery_skips_started(tmp_path, monkeypatch):
    """`started`-ревизия ничего не доставила — предыдущей ДОСТАВКОЙ не является.

    Иначе §I5 становится fail-open (у `started` нет `anchor`, сверка не
    срабатывает), в журнал уходит ложный `comparison: unavailable`, а
    `supersedes` указывает на недоставку."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(
        tmp_path, monkeypatch, op={"status": "completed", "pr": 5}
    )
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    n, op = tb._last_delivery(state)
    assert (n, op["pr"]) == (1, 5)


def test_last_delivery_skips_started_v1(tmp_path, monkeypatch):
    """Тот же счёт и историческому ключу `tasks-deliver` (фикс-круг 1).

    `deliver_for_run` ведёт op write-ahead (`op_start` ДО эффектов), так
    что упавшая ПЕРВАЯ доставка оставляет v1 в `started` — принять её за
    доставку значит тот же fail-open §I5, только на легаси-ключе."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch, op={"status": "started"})
    assert tb._last_delivery(state) is None
    with pytest.raises(RuntimeError, match="переиздавать нечего"):
        tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))


def test_start_revision_records_full_intent(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {
        "branch": "spec/WS-alpha-7-tasks-v2",
        "base_sha": "deadbeef",
        "prospective_anchor": "anchor2",
        "approval_pr": 77,
        "tasks_version": 3,
        "dag": ["00-charter.md"],
        "dag_source": "previous_delivery",
        "supersedes": 1,
        "expected_generated_at": "2026-09-09T10:00:00+03:00",
        "tasks_blob": "blob2",
    })
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert saved["revision"] == 2
    assert saved["head_sha"] is None
    assert saved["supersedes"] == 1
    assert saved["base_sha"] == "deadbeef"


def test_abandon_revision_is_terminal_and_keeps_reason(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    tb._abandon_revision(state, 2, "оператор закрыл PR #99")
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "abandoned"
    assert "PR #99" in saved["reason"]


def test_abandon_revision_refuses_on_already_abandoned(tmp_path, monkeypatch):
    """§I3: терминальны `completed` И `abandoned` — гвард сверяется с обоими.

    Повторный `--abandon-revision` по брошенной ревизии — ровно мутация
    журнала, запрещённая §I4: `save` пишет run.json целиком, и первая
    причина исчезает безвозвратно, а она — ЕДИНСТВЕННЫЙ след того, почему
    ревизия брошена.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    tb._abandon_revision(state, 2, "оператор закрыл PR #99")
    with pytest.raises(RuntimeError, match="терминальная запись"):
        tb._abandon_revision(state, 2, "передумал")
    # Запись цела: и в памяти прогона, и на диске — отказ не тронул её.
    assert state.ops["tasks-deliver-v2"]["reason"] == "оператор закрыл PR #99"
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "abandoned"
    assert saved["reason"] == "оператор закрыл PR #99"


def test_completed_v1_op_is_never_rewritten(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(
        tmp_path, monkeypatch, op={"status": "completed", "pr": 5}
    )
    before = dict(state.ops["tasks-deliver"])
    tb._complete_revision(state, 2, pr=10, anchor="a")
    assert rs.load("r-recon").ops["tasks-deliver"] == before


# --- _resolve_correction_pr (§I7: подпись штампа берётся у correction-PR) --


_ANCHOR_REL = "workstreams/WS-alpha-7/spec/30-decomposition.md"
#: Активный DAG фикстуры — аргумент `_resolve_correction_pr`: анкер и
#: состав подписываемых узлов выводятся ИЗ НЕГО, а не подаются отдельно.
_DAG = task_bridge._BUNDLE_DAG
#: node-id узлов, которые тронул correction-PR по умолчанию фикстуры
#: (`_ProvOps.files` = только анкер) — §I7 поузловой.
_ANCHOR_NODES = frozenset({"decomposition"})


def _pr_signature(pr: int) -> tuple[str, str]:
    """Подпись мержа, РАЗЛИЧАЮЩАЯ номера PR: (mergedBy.login, mergedAt).

    Пока стаб отдавал одну подпись на любой номер, бандл-PR и correction-PR
    были в тестах НЕРАЗЛИЧИМЫ, и регрессия §I5 «переиздание сразу после
    доставки — бесследный no-op» зеленела тавтологически: сравнивались
    байты, в которые входила одна и та же подпись. В жизни эти PR-ы
    мержат разные люди в разное время, и именно на этом различии дефект
    §I5×§I7 и стоял — стаб обязан его отражать.
    """
    return f"merger-{pr}", f"2026-09-09T05:{pr // 60 % 60:02d}:{pr % 60:02d}Z"


_FULL_FACTS = {
    "state": "MERGED", "baseRefName": "master",
    # Значения — только маркер «поле есть»: конкретную подпись
    # `_ProvOps.pr_facts` подставляет по НОМЕРУ PR (`_pr_signature`).
    "mergedAt": "-", "mergedBy": {"login": "-"},
}


class _ProvOps(_StubOps):
    def __init__(self, commit="c1", prs=None, files=None, facts=None):
        super().__init__()
        self.commit, self.prs = commit, (prs if prs is not None else [])
        # Состав файлов PR (§I7, шаг 4): по умолчанию названный PR ТРОГАЛ
        # анкер — иначе явный `--approval-pr` отказывает (C-5), а на пути
        # возобновления он передаётся из намерения ревизии.
        self.files = [_ANCHOR_REL] if files is None else files
        # Ответ `pr_facts` настраиваемый (F-04 финального ревью): пока он
        # был ОДНИМ значением «вмержен, подпись полна», обе проверки шага 4
        # §I7 снимались мутацией незаметно — ветвление по этим полям
        # физически не могло сработать ни в одном тесте.
        self.facts = _FULL_FACTS if facts is None else facts
        # Пути, по которым спрашивали провенанс: `anchor_rel` выводится из
        # ТЕРМИНАЛЬНОГО узла активного DAG, и без записи аргумента этот
        # вывод ничем не удерживался.
        self.touched: list[str] = []

    def last_commit_touching(self, target_dir, rel_path):
        self.touched.append(rel_path)
        return self.commit

    def prs_containing_commit(self, repo_slug, sha):
        self.calls.append(("prs_containing_commit", sha))
        return self.prs

    def pr_files(self, repo_slug, pr):
        self.calls.append(("pr_files", pr))
        return list(self.files)

    def pr_facts(self, repo_slug, pr):
        # Подпись живёт ЗДЕСЬ, а не в списке PR-ов по коммиту (ревью #164),
        # и ЗАВИСИТ ОТ НОМЕРА PR (`_pr_signature`): одинаковый ответ на
        # любой номер делал бандл-PR и correction-PR
        # неразличимыми. `None` в шаблоне `facts` сохраняется — им тесты
        # проверяют «подпись неполна».
        self.calls.append(("pr_facts", pr))
        facts = dict(self.facts)
        by, at = _pr_signature(pr)
        if facts.get("mergedBy") is not None:
            facts["mergedBy"] = {"login": by}
        if facts.get("mergedAt") is not None:
            facts["mergedAt"] = at
        return facts


_MERGED_PR = {
    "number": 403, "state": "MERGED", "baseRefName": "master",
    "mergedAt": "2026-09-09T05:00:00Z", "mergedBy": "andrei-shtanakov",
    "mergeCommit": "c1",
}


def test_provenance_single_merged_candidate(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    corr = tb._resolve_correction_pr(
        state, _ProvOps(prs=[_MERGED_PR]), _DAG, "master", None,
    )
    assert (corr.pr, corr.approved_by) == (403, _pr_signature(403)[0])
    assert corr.approved_at == _pr_signature(403)[1]


def test_provenance_zero_candidates_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="подписи взять неоткуда"):
        tb._resolve_correction_pr(
            state, _ProvOps(prs=[]), "a/b.md", "master", None
        )


def test_provenance_two_candidates_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    other = {**_MERGED_PR, "number": 999}
    with pytest.raises(RuntimeError, match="подписи взять неоткуда"):
        tb._resolve_correction_pr(
            state, _ProvOps(prs=[_MERGED_PR, other]), "a/b.md", "master", None
        )


def test_provenance_filter_ignores_open_pr_on_same_commit(
    tmp_path, monkeypatch
):
    """Половина фильтра кандидатов «state == MERGED» — своим случаем.

    Тот же коммит анкера обычно попадает и в открытые PR (ветка, куда его
    взяли черри-пиком, или ревью-ветка сверху). Пока все тесты подавали
    ТОЛЬКО вмерженные PR, «отобрали правильных» и «отобрали всех подряд»
    были неразличимы: удаление этой половины фильтра проходило незаметно
    (F-05, мутация M30). На мутанте этот вход даёт двух кандидатов и
    отказ — то есть он и различает."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    open_pr = {**_MERGED_PR, "number": 999, "state": "OPEN"}
    corr = tb._resolve_correction_pr(
        state, _ProvOps(prs=[_MERGED_PR, open_pr]), _DAG, "master",
        None,
    )
    assert (corr.pr, corr.approved_by) == (403, _pr_signature(403)[0])


def test_provenance_filter_ignores_pr_merged_into_other_base(
    tmp_path, monkeypatch
):
    """Вторая половина фильтра — «baseRefName == base_ref».

    Коммит анкера живёт и в PR, вмерженных в релизную ветку; их подпись к
    штампу на `master` отношения не имеет. Без своего входа эту половину
    тоже можно было удалить незаметно (F-05, мутация M31)."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    other_base = {**_MERGED_PR, "number": 999, "baseRefName": "release"}
    corr = tb._resolve_correction_pr(
        state, _ProvOps(prs=[_MERGED_PR, other_base]), _DAG, "master",
        None,
    )
    assert (corr.pr, corr.approved_by) == (403, _pr_signature(403)[0])


def test_provenance_found_pr_not_merged_refuses(tmp_path, monkeypatch):
    """§I7 шаг 4 на пути ПОИСКА: найденный PR обязан быть вмерженным.

    Список по коммиту (`prs_containing_commit`) и факты PR (`pr_facts`) —
    два разных запроса, и второй может застать PR уже не вмерженным
    (список из кэша, PR откатили). Пока `pr_facts` был одноответным
    стабом, проверка снималась мутацией незаметно (F-04, M33)."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps(
        prs=[_MERGED_PR], facts={"state": "OPEN", "baseRefName": "master"},
    )
    with pytest.raises(RuntimeError, match="найден по коммиту"):
        tb._resolve_correction_pr(state, ops, _DAG, "master", None)


@pytest.mark.parametrize(
    "approval_pr", [None, 500], ids=["поиск", "явный-approval-pr"],
)
def test_provenance_incomplete_signature_refuses(
    approval_pr, tmp_path, monkeypatch
):
    """§I7: пустые mergedBy/mergedAt — отказ, а не штамп с `None`.

    Гвард стоит против НАБЛЮДЁННОГО поведения API, а не гипотетического:
    `governance/ops.py:prs_containing_commit` в докстринге фиксирует, что
    эндпоинт `commits/<sha>/pulls` отдаёт `merged_by: null` даже у
    вмерженного PR. Без этого теста подпись `approved_by=None` уходила бы
    в штамп бандла (F-04, мутация M35). Ветка проверки общая для поиска и
    явного `--approval-pr` — обе формы входа."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps(prs=[_MERGED_PR], facts={
        "state": "MERGED", "baseRefName": "master",
        "mergedBy": None, "mergedAt": None,
    })
    with pytest.raises(RuntimeError, match="подпись штампа неполна"):
        tb._resolve_correction_pr(
            state, ops, _DAG, "master", approval_pr
        )


def test_provenance_explicit_flag_refuses_other_base(tmp_path, monkeypatch):
    """§I7 шаг 4 у явного `--approval-pr`: PR обязан целить в `base_ref`.

    Соседний тест проверяет только вмерженность, поэтому PR, вмерженный в
    ЧУЖУЮ ветку, тестом не подавался, и проверку base можно было удалить
    незаметно (F-06, мутация M34). Оператор называет такой PR по ошибке
    легко: тот же анкер, тот же автор, другой релизный поток."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps(facts={**_FULL_FACTS, "baseRefName": "release"})
    with pytest.raises(RuntimeError, match="нацелен в"):
        tb._resolve_correction_pr(state, ops, _DAG, "master", 500)


def test_provenance_no_commit_touching_anchor_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="не менялся"):
        tb._resolve_correction_pr(
            state, _ProvOps(commit=None), "a/b.md", "master", None
        )


def test_provenance_explicit_flag_is_verified_not_trusted(
    tmp_path, monkeypatch
):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)

    class _Explicit(_ProvOps):
        def pr_facts(self, repo_slug, pr):
            return {"state": "OPEN", "baseRefName": "master"}

    with pytest.raises(RuntimeError, match="не вмержен"):
        tb._resolve_correction_pr(
            state, _Explicit(), "a/b.md", "master", 500
        )


def test_provenance_explicit_flag_requires_anchor_change(
    tmp_path, monkeypatch
):
    """§I7 шаг 4, третье условие: явный PR обязан МЕНЯТЬ файл анкера.

    Без него флаг из «заменяет поиск» становился «отключает проверку»:
    оператор называл любой вмерженный в base_ref PR, и его mergedBy/
    mergedAt уходили в штамп бандла — штамп утверждал бы, что байты
    анкера одобрил человек, который их не видел (C-5)."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps(files=["docs/README.md"])
    with pytest.raises(RuntimeError, match="не менял"):
        tb._resolve_correction_pr(state, ops, _DAG, "master", 500)


def test_provenance_explicit_flag_accepts_pr_touching_anchor(
    tmp_path, monkeypatch
):
    """Обратная сторона: PR, реально менявший анкер, принимается.

    Заодно — состав `signed_nodes` (§I7 поузловой): в него входят ТОЛЬКО
    узлы активного DAG, и файл вне бандла (`docs/README.md`) в подписи не
    участвует."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps(files=["docs/README.md", _ANCHOR_REL])
    corr = tb._resolve_correction_pr(state, ops, _DAG, "master", 500)
    assert (corr.pr, corr.approved_by) == (500, _pr_signature(500)[0])
    assert corr.signed_nodes == frozenset({"decomposition"})


# --- _previous_dag (§I8: сверка активного DAG предыдущей доставки) --------


_BASE_SHA = "base-sha-фикстуры"
_SPEC_REL = "spec/WS-alpha-7-tasks.md"


class _ShowFileOps:
    """Минимальный фейк ops для _previous_dag: только show_file.

    Настоящий git здесь не подключаем — Task 5 его тоже не подключала для
    этих тестов, а `show_file` тестируется отдельно (Ops-тесты, реальный
    временный репо).

    Аргументы НЕ игнорируются (F-12 финального ревью): текст отдаётся
    только за спеку В BASE, всё прочее — `None`, как настоящий
    `git show`. Пока стаб отвечал одно и то же на любые ref/path,
    подмена и ref'а, и пути проходила незаметно, а в проде она даёт
    `None` ⇒ `"unavailable"` ⇒ сверка §I8 не производится вовсе —
    fail-OPEN на инварианте, заявленном fail-closed.
    """

    def __init__(self, text: str | None) -> None:
        self._text = text
        self.calls: list[tuple] = []

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        self.calls.append(("show_file", ref, path))
        if (ref, path) != (_BASE_SHA, _SPEC_REL):
            return None
        return self._text


def _spec_text(traces_to: str) -> str:
    return (
        "---\n"
        "spec_stage: tasks\n"
        f"traces_to:\n- {traces_to}\n"
        "---\n\nbody\n"
    )


def test_previous_dag_from_revision_record(tmp_path, monkeypatch) -> None:
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    prev = {"dag": [list(x) for x in tb._BUNDLE_DAG]}
    dag, source = tb._previous_dag(
        state, _ShowFileOps(None), prev, state.target_dir, state.bundle_dir,
        _BASE_SHA,
    )
    assert source == "previous_delivery"
    assert dag == tb._BUNDLE_DAG


def test_previous_dag_derived_from_bundle_composition(
    tmp_path, monkeypatch
) -> None:
    """Легаси-v1 без записи dag: состав каталога совпадает ровно с одним
    вариантом _dag_for, И traces_to доставленной спеки указывает на его
    якорь."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ShowFileOps(_spec_text("decomposition"))
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert source == "derived_from_spec"
    assert dag == tb._BUNDLE_DAG
    # Спрошено ИМЕННО то место (F-12): спека в base, а не в HEAD и не по
    # соседнему пути. Неверный ref/путь в проде даёт None ⇒ "unavailable"
    # ⇒ сверка §I8 не производится вовсе — fail-OPEN на инварианте,
    # который заявлен fail-closed.
    assert ops.calls == [("show_file", _BASE_SHA, _SPEC_REL)]


def test_previous_dag_unavailable_when_composition_matches_nothing(
    tmp_path, monkeypatch
) -> None:
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / state.bundle_dir / "99-alien.md").write_text("x")
    ops = _ShowFileOps(_spec_text("decomposition"))
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert (dag, source) == (None, "unavailable")


def test_previous_dag_refuses_when_bundle_dir_missing(
    tmp_path, monkeypatch
) -> None:
    """Каталога бандла нет — отказ с путём, а не сырой FileNotFoundError.

    Ветка вывода состава исполняется для ЛЮБОЙ сегодняшней v1
    (`deliver_for_run` поле `dag` не пишет), и `_previous_dag` — первый
    код переиздания, который вообще трогает каталог бандла (он раньше и
    `_resolve_correction_pr`, и `_prospective_anchor` с его
    `_check_bundle_composition`). `iterdir()` на несуществующем каталоге
    бросал `FileNotFoundError` мимо `except RuntimeError` в `main`.
    «unavailable» здесь тоже неверен: §I8 зовёт неудачей вывода
    несошедшиеся ИСТОЧНИКИ, а не отсутствие предмета переиздания."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    shutil.rmtree(Path(state.target_dir) / state.bundle_dir)
    ops = _ShowFileOps(_spec_text("decomposition"))
    with pytest.raises(RuntimeError, match="каталога бандла") as exc:
        tb._previous_dag(
            state, ops, {"pr": 5}, state.target_dir, state.bundle_dir,
            _BASE_SHA,
        )
    assert state.bundle_dir in str(exc.value)
    assert "run.json" in str(exc.value)          # процедура, не только факт
    assert ops.calls == []                       # до чтения спеки не дошло


def test_previous_dag_legacy_requires_delivered_spec(
    tmp_path, monkeypatch
) -> None:
    """Спеки в base нет (show_file -> None) → (None, "unavailable"),
    хотя состав каталога совпал ровно с одним вариантом."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ShowFileOps(None)
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert (dag, source) == (None, "unavailable")


def test_previous_dag_legacy_rejects_anchor_mismatch(
    tmp_path, monkeypatch
) -> None:
    """Состав каталога дал 6-узловой вариант (якорь decomposition), а
    traces_to доставленной спеки — behaviour-spec → (None, "unavailable")."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ShowFileOps(_spec_text("behaviour-spec"))
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert (dag, source) == (None, "unavailable")


def test_previous_dag_legacy_unparsable_frontmatter(
    tmp_path, monkeypatch
) -> None:
    """show_file вернул текст без frontmatter → (None, "unavailable")."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ShowFileOps("no frontmatter here\n")
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert (dag, source) == (None, "unavailable")


def test_previous_dag_legacy_broken_yaml_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    """Разделители целы, YAML битый → §6-путь `(None, "unavailable")`.

    Соседний тест подаёт текст БЕЗ frontmatter (ValueError-ветка); сбой
    парсера летел `yaml.YAMLError` мимо `except ValueError` здесь и
    ронял переиздание трейсбеком вместо объявленного fail-closed."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ShowFileOps("---\nspec_stage: tasks\ntraces_to: [a, b\n---\n\nx\n")
    dag, source = tb._previous_dag(
        state, ops, {"pr": 5}, state.target_dir, state.bundle_dir, _BASE_SHA,
    )
    assert (dag, source) == (None, "unavailable")


def test_previous_dag_legacy_rejects_malformed_traces_to(
    tmp_path, monkeypatch
) -> None:
    """traces_to не список (строка вместо списка) и traces_to — пустой
    список: обе формы дают (None, "unavailable"), а не «совпало» — состав
    каталога один совпадает ровно с одним вариантом (_target — полный
    6-узловой бандл), поэтому отказ здесь ТОЛЬКО из-за формы traces_to."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    not_a_list = (
        "---\nspec_stage: tasks\ntraces_to: decomposition\n---\n\nbody\n"
    )
    empty_list = "---\nspec_stage: tasks\ntraces_to: []\n---\n\nbody\n"
    for text in (not_a_list, empty_list):
        ops = _ShowFileOps(text)
        dag, source = tb._previous_dag(
            state, ops, {"pr": 5}, state.target_dir, state.bundle_dir,
            _BASE_SHA,
        )
        assert (dag, source) == (None, "unavailable")


# --- _reconcile_revision / _recover_commit (§I3, §I3.1) -------------------


def test_reconcile_completed_open_pr_returns_it():
    """completed + её PR реально OPEN → вернуть его.

    Факты PR приходят аргументом (C-8): решение по §I3 — чистая функция,
    её ветвление проверяется без стабов ops, а то, что факты реально
    запрашиваются, держит интеграционный
    `test_supersede_returns_existing_pr_when_revision_completed`."""
    from governance import task_bridge as tb

    op = {"status": "completed", "pr": 42, "base_sha": "s",
          "branch": "spec/WS-alpha-7-tasks-v2"}
    facts = {"state": "OPEN", "headRefOid": "h"}
    assert tb._reconcile_revision(2, op, "s", 42, facts) == "return_pr"


def test_reconcile_started_merged_pr_completes():
    """started + её PR смержен, идентичность (headRefOid) сошлась → "complete".

    Строка §I3, до фикс-круга 1 не покрытая ни одним тестом (major #1)."""
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "head_sha": "h",
          "branch": "spec/WS-alpha-7-tasks-v2"}
    facts = {"state": "MERGED", "headRefOid": "h"}
    assert tb._reconcile_revision(2, op, "s", 7, facts) == "complete"


def test_reconcile_completed_closed_unmerged_fails_closed():
    """"любое" состояние ревизии в таблице §I3 включает completed.

    PR закрыт без мержа отказывает и после успешной доставки, не только
    для started (фикс-круг 1, добавление #3)."""
    from governance import task_bridge as tb

    op = {"status": "completed", "pr": 42, "base_sha": "s",
          "branch": "spec/WS-alpha-7-tasks-v2"}
    with pytest.raises(RuntimeError, match="отклонена"):
        tb._reconcile_revision(2, op, "s", 42, {"state": "CLOSED"})


def test_reconcile_completed_identity_mismatch_fails_closed():
    """Идентичность сверяется и для completed, не только для started.

    Тот же общий шаг таблицы §I3 (фикс-круг 1, добавление #3, вторая
    половина)."""
    from governance import task_bridge as tb

    op = {"status": "completed", "pr": 42, "base_sha": "s",
          "head_sha": "mine", "branch": "spec/WS-alpha-7-tasks-v2"}
    facts = {"state": "OPEN", "headRefOid": "someone-else"}
    with pytest.raises(RuntimeError, match="идентичность"):
        tb._reconcile_revision(2, op, "s", 42, facts)


def test_reconcile_started_open_pr_same_base_continues():
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "head_sha": "h",
          "tasks_blob": "b", "branch": "spec/WS-alpha-7-tasks-v2"}
    facts = {"state": "OPEN", "headRefOid": "h"}
    assert tb._reconcile_revision(2, op, "s", 7, facts) == "continue"


def test_reconcile_started_open_pr_shifted_base_fails_closed():
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "old", "head_sha": "h",
          "branch": "b"}
    facts = {"state": "OPEN", "headRefOid": "h"}
    with pytest.raises(RuntimeError, match="--abandon-revision"):
        tb._reconcile_revision(2, op, "new", 7, facts)


def test_reconcile_closed_unmerged_fails_closed():
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "branch": "b"}
    with pytest.raises(RuntimeError, match="отклонена"):
        tb._reconcile_revision(2, op, "s", 7, {"state": "CLOSED"})


def test_reconcile_identity_mismatch_fails_closed():
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "head_sha": "mine",
          "branch": "b"}
    facts = {"state": "OPEN", "headRefOid": "someone-else"}
    with pytest.raises(RuntimeError, match="идентичность"):
        tb._reconcile_revision(2, op, "s", 7, facts)


def test_reconcile_missing_head_ref_oid_fails_closed():
    """Пустой `headRefOid` при записанном `head_sha` — отказ, не «сошлось».

    §I3 объявляет зелёной сошедшуюся сверку, а не «сверить не удалось»:
    иначе ревизия завершается ЧУЖИМ PR, которому приписывается anchor
    нашего намерения, и гейт §I5 «апстрим не менялся» опирается на
    доставку, которой не было. Слой `ops` присутствия поля не гарантирует
    (`pr_facts` отдаёт сырой gh-JSON). Соседний fail-open в
    `_delivered_anchor` сознателен и сюда не переносится: там факт лишь
    записывается неизвестным.
    """
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "head_sha": "mine",
          "branch": "b"}
    with pytest.raises(RuntimeError, match="идентичность не проверить"):
        tb._reconcile_revision(2, op, "s", 7, {"state": "MERGED"})


def test_reconcile_started_no_pr_shifted_base_abandons():
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "old", "branch": "b"}
    assert tb._reconcile_revision(2, op, "new", None, {}) == "abandon_and_next"


def test_reconcile_started_no_pr_same_base_continues():
    """§I3 «started | нет PR | base совпал» → восстановление по §I3.1.

    Обратная сторона предыдущего теста: без неё «сдвинулся» и «не
    сдвинулся» неразличимы."""
    from governance import task_bridge as tb

    op = {"status": "started", "base_sha": "s", "branch": "b"}
    assert tb._reconcile_revision(2, op, "s", None, {}) == "continue"


def test_recover_commit_accepts_matching_local_commit(tmp_path, monkeypatch):
    """head_sha: null, но подходящий коммит есть — принимается, не пересоздаётся."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "commit1" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "base1"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "blob1"

    # Возврата нет (F-03): прод его не читал, а тесты утверждали значение,
    # которого нет в поведении — мутация `return "phantom"` была зелёной.
    assert tb._recover_commit(state, _Ops(), 2, op, None) is None


def test_recover_commit_branch_at_base_means_no_commit_yet(
    tmp_path, monkeypatch
):
    """§I3.1 «null | подходящего коммита нет»: ветка стоит на base.

    `deliver` создаёт ветку (`ensure_branch` от HEAD = base) ЗАДОЛГО до
    коммита, и падение в этом окне оставляет ветку ровно на `base_sha`.
    До фикса (blocker C-2) сверка брала родителя базы, он `base_sha` не
    равен никогда, и собственная ветка ревизии объявлялась чужой —
    ревизия становилась неремонтируемой штатным путём."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": None,
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            # ensure_branch создал ветку от base — она на base и стоит.
            return "base1" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "родитель-базы"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "блоб-v1-спеки"

    assert tb._recover_commit(state, _Ops(), 2, op, None) is None


def test_recover_commit_branch_at_base_after_blob_written(
    tmp_path, monkeypatch
):
    """То же окно, но `tasks_blob` уже записан хуком `before_commit`.

    Различитель «коммита ещё нет» — ветка на `base_sha`, а не пустой
    `tasks_blob`: между `before_commit` и `commit_paths` блоб в намерении
    есть, а коммита ещё нет."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "base1" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "родитель-базы"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "блоб-v1-спеки"

    assert tb._recover_commit(state, _Ops(), 2, op, None) is None


def test_recover_commit_moved_branch_message_claims_only_local_head(
    tmp_path, monkeypatch
):
    """§I3.1 «записан | head отличается»: сверяется ЛОКАЛЬНЫЙ head.

    Remote-голова не запрашивается нигде (`ops.rev_parse` резолвит
    refs/heads/), поэтому обещать её в диагностике нельзя — минор C-3."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": "наш-коммит", "base_sha": "base1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "чужой-коммит" if "tasks-v2" in ref else None

    with pytest.raises(RuntimeError, match="двигали снаружи") as exc:
        tb._recover_commit(state, _Ops(), 2, op, None)
    assert "remote" not in str(exc.value)


def test_recover_commit_refuses_recorded_head_without_local_branch(
    tmp_path, monkeypatch
):
    """§I3.1 «записан | ветки в клоне нет, PR-а нет» — fail-closed.

    Доставка дошла до `push_branch` (ветка на remote, `head_sha` durable)
    и упала на `create_draft_pr`; оператор возобновляет из клона, где
    ЛОКАЛЬНОЙ ветки нет. Молчаливый пропуск вёл к пересозданию ветки от
    base и НОВОМУ коммиту (тот же tree, другой committer date ⇒ другой
    SHA), а `after_commit` затирал бы `head_sha` намерения — §I3 зовёт
    его единственным фактом опознания ревизии в удалённой ветке."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": "запушенный-коммит", "base_sha": "base1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return None          # ветки ревизии в этом клоне нет

    with pytest.raises(RuntimeError, match="в этом клоне нет") as exc:
        tb._recover_commit(state, _Ops(), 2, op, None)
    assert "--abandon-revision 2" in str(exc.value)


def test_recover_commit_accepts_missing_branch_when_pr_identifies_commit(
    tmp_path, monkeypatch
):
    """Та же пара фактов, но PR ревизии есть — отказа НЕТ.

    `_reconcile_revision` уже сверил `headRefOid` живого PR с `head_sha`,
    то есть коммит опознан; вызывающий на этом пути только помечает
    ревизию completed и ветку не трогает. Отказ здесь был бы регрессией:
    полностью состоявшаяся доставка становилась бы неремонтируемой в
    клоне без ветки."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": "запушенный-коммит", "base_sha": "base1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return None

    assert tb._recover_commit(state, _Ops(), 2, op, 7) is None


def test_recover_commit_refuses_foreign_commit(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "commitX" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "OTHER-BASE"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "blob1"

    with pytest.raises(RuntimeError, match="чужой коммит"):
        tb._recover_commit(state, _Ops(), 2, op, None)


def test_recover_commit_refuses_right_parent_wrong_blob(
    tmp_path, monkeypatch
):
    """Вторая половина сверки идентичности §I3.1: родитель ТОТ, блоб чужой.

    Соседний тест ломает только родителя, и пока это был единственный
    случай, условие по `tasks_blob` можно было удалить из реализации
    незаметно (F-02 финального ревью, мутация M09 — зелёная). А ведь
    ровно ради этой половины `tasks_blob` пишется отдельным хуком
    `before_commit`: коммит с правильным родителем, но с ЧУЖИМ
    содержимым `spec/<ws-id>-tasks.md` (кто-то коммитнул поверх base в
    ту же ветку) иначе принимается за собственный коммит ревизии, и
    возобновление доставки достраивает PR поверх чужой работы."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "commitX" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "base1"          # родитель СОВПАЛ с намерением

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "blobX"          # а содержимое спеки — чужое

    with pytest.raises(RuntimeError, match="чужой коммит"):
        tb._recover_commit(state, _Ops(), 2, op, None)


# --- deliver_superseded: сборка переиздания (Task 7) ----------------------


class _SupersedeOps(_ProvOps):
    """Стаб на базе `_ProvOps` для `deliver_superseded`: живого git нет,
    примитивы восстановления коммита/идентичности возвращают None —
    ветвление на "коммита ещё нет" (`_recover_commit`) и "PR не найден"
    (`_reconcile_revision`), не на чужую работу под тем же именем.

    `rev_parse` наследуется от `_StubOps` (Task 7b): "HEAD" → синтетический
    `base-sha-1` (иначе fail-closed гард §I2 останавливает переиздание до
    предмета теста), ветка ревизии → None, то есть коммита ещё нет."""

    def commit_parent(self, target_dir, sha):
        return None

    def blob_in_commit(self, target_dir, sha, rel_path):
        return None

    def find_pr(self, repo_slug, branch, *, any_state=False):
        return None

    def show_file(self, target_dir, ref, path):
        # Спека v1 в base доставлена (фикстура объявляет v1 доставленной),
        # её якорь — decomposition: `_previous_dag` выводит состав по
        # каталогу И этому якорю (dag_source = derived_from_spec).
        #
        # Аргументы сверяются, а не игнорируются (F-12): «в base» —
        # `base-sha-1`, тот же SHA, что `rev_parse("HEAD")` отдаёт ДО
        # коммита. Спросив не то место, прод получил бы None и молча
        # ушёл в "unavailable", отменив сверку §I8.
        self.calls.append(("show_file", ref, path))
        if (ref, path) != ("base-sha-1", _SPEC_REL):
            return None
        return _spec_text("decomposition")


def test_supersede_result_refuses_inconsistent_kind() -> None:
    """Тип исхода не даёт собрать невозможное сочетание.

    `SupersedeResult("delivered", None)` напечатался бы у оператора как
    «PR #None», а `("noop", 5)` объявил бы бесследный no-op с номером
    PR — оба состояния не существуют в §I3/§I5."""
    from governance import task_bridge as tb

    with pytest.raises(ValueError, match="несовместим"):
        tb.SupersedeResult("delivered", None)
    with pytest.raises(ValueError, match="несовместим"):
        tb.SupersedeResult("noop", 5)


def test_supersede_equal_content_anchor_is_traceless_noop(
    tmp_path, monkeypatch
):
    """Равный `content_anchor`: RC-успех, run.json побайтово прежний.

    Сверка §I5 идёт по `content_anchor` — signature-free хэшу DAG, а не по
    пост-штамповому `anchor`: подпись в него не входит, поэтому равенство
    достижимо БЕЗ единой правки апстрима. `anchor` в записи стоит нарочно
    ДРУГОЙ: он отвечает на вопрос §I2 («те ли байты доставлены») и на §I5
    влиять не должен ни в какую сторону."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    content = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "БЛОБ-ДОСТАВКИ-v1",
        "content_anchor": content,
    }
    rs.save(state)
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    ops = _ProvOps(prs=[_MERGED_PR])
    result = tb.deliver_superseded(state, ops)
    after = (rs.run_dir("r-recon") / "run.json").read_bytes()
    assert result == tb.SupersedeResult("noop")
    assert before == after
    # §I5 стоит ВЫШЕ §I7: провенанс не разрешался вовсе. Утверждение
    # именно о вызовах, а не только об исходе: пока сверка шла ПОСЛЕ
    # поиска correction-PR, «последним, кто трогал анкер» оказывался
    # штамп-коммит нашего же tasks-PR — на нём дефект и стоял.
    assert ops.touched == []
    assert not [c for c in ops.calls if c[0] in (
        "prs_containing_commit", "pr_files"
    )]
    # `pr_facts` по PR первой доставки (#5) — это §I3, не §I7; предмет
    # утверждения — что фактов correction-PR (#403) никто не спрашивал.
    assert ("pr_facts", 403) not in ops.calls


def test_supersede_changed_anchor_opens_new_branch_and_pr(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps(prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "completed"
    assert saved["supersedes"] == 1
    assert saved["approval_pr"] == 403
    # Поля записи, а не только PR и ветка (minor ревью Task 7, B10):
    # незаполненные head_sha/tasks_blob раньше не ловились ничем.
    delivered = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert saved["head_sha"] == "commit-sha-1"   # SHA коммита, не базы
    assert saved["tasks_blob"] == blob_sha1(delivered)
    assert saved["tasks_version"] == 2
    assert saved["prospective_anchor"] == saved["anchor"]
    assert saved["dag_source"] == "derived_from_spec"
    assert rs.load("r-recon").ops["tasks-deliver"]["pr"] == 5   # v1 цела


# --- §I7 на РЕАЛЬНОМ состоянии base: бандл уже проштампован ---------------
#
# Все supersede-тесты выше стартуют с бандла в `status: draft` (так его
# кладёт `_target`), и штамп в них срабатывает обычной веткой. Боевого
# входа это не описывает: доставка v1 штампует узлы и коммитит их в свой
# же tasks-PR, человек его мержит — значит на момент переиздания в base
# лежит бандл со `status: approved` и подписью ПРЕДЫДУЩЕЙ доставки.
# Хелперы ниже приводят фикстуру ровно в это состояние.

_PREV_MERGER = "prev-bundle-merger"
_PREV_MERGED_AT = "2026-09-01T00:00:00Z"


def _stamp_base_as_previous_delivery(
    state, legacy_bundle: int | None = None
) -> None:
    """Бандл в base после мержа tasks-PR доставки v1: узлы approved."""
    from governance import task_bridge as tb

    tb.stamp_bundle_approved(
        state.target_dir,
        state.bundle_dir,
        approved_by=_PREV_MERGER,
        approved_at=_PREV_MERGED_AT,
        legacy_bundle=legacy_bundle,
    )


def _apply_correction_to_anchor(
    state, anchor: str = "30-decomposition.md"
) -> None:
    """Correction-PR: правит ТЕЛО анкера, frontmatter не трогает.

    Сброса `status` в draft тут нет намеренно — этот репо его нигде не
    требует и не выполняет, и именно поэтому §I7 упирается в узел,
    который уже approved."""
    path = Path(state.target_dir) / state.bundle_dir / anchor
    text = path.read_text(encoding="utf-8")
    assert "Проза предмета." in text
    path.write_text(
        text.replace("Проза предмета.", "Проза предмета (correction)."),
        encoding="utf-8",
    )


def _anchor_meta(state, anchor: str = "30-decomposition.md") -> dict:
    from governance import task_bridge as tb

    meta, _ = tb.split_frontmatter(
        (Path(state.target_dir) / state.bundle_dir / anchor).read_text(
            encoding="utf-8"
        )
    )
    return meta


def _supersede_over_stamped_base(tmp_path, monkeypatch):
    """state + ops для переиздания поверх уже проштампованного бандла."""
    from governance import run_state as rs

    state = _recon_state(tmp_path, monkeypatch)
    _stamp_base_as_previous_delivery(state)
    _apply_correction_to_anchor(state)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    return state, _SupersedeOps(prs=[_MERGED_PR])


def test_supersede_restamps_signature_on_approved_base(
    tmp_path, monkeypatch
):
    """§I7: подпись переизданного анкера — от correction-PR, не от v1.

    Без перештампа `stamp_bundle_approved` молча пропускает узел
    (`status` уже approved), и в переизданный PR уходит анкер, чей
    `approved_by` называет человека, мержившего ИСХОДНЫЙ бандл-PR, —
    дословно то, что §I7 объявляет недопустимым. Наблюдаемо это и в
    коммите: штамп обязан в него попасть, а не исчезнуть."""
    from governance import task_bridge as tb

    state, ops = _supersede_over_stamped_base(tmp_path, monkeypatch)
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    meta = _anchor_meta(state)
    assert (meta["approved_by"], meta["approved_at"]) == _pr_signature(403)
    committed = next(
        call[1] for call in ops.calls if call[0] == "commit_paths"
    )
    assert _ANCHOR_REL in committed


def test_supersede_restamp_keeps_node_version(tmp_path, monkeypatch):
    """Перештамп подписи не растит `version` узла.

    Инкремент в обычном пути привязан к ПЕРЕХОДУ draft → approved, а не к
    «файл тронули»: перештамп исправляет провенанс тех же байтов, которые
    доставил correction-PR, нового поколения документа не заводит."""
    from governance import task_bridge as tb

    state, ops = _supersede_over_stamped_base(tmp_path, monkeypatch)
    before = _anchor_meta(state)["version"]
    tb.deliver_superseded(state, ops)
    assert _anchor_meta(state)["version"] == before


def test_supersede_restamp_is_idempotent(tmp_path, monkeypatch):
    """Повтор перештампа с той же подписью не меняет байт.

    На этом стоит §I3.1: возобновление ревизии обязано пересчитать тот же
    anchor, иначе гард §I2 объявит расхождение на собственном повторе."""
    from governance import task_bridge as tb

    state, ops = _supersede_over_stamped_base(tmp_path, monkeypatch)
    tb.deliver_superseded(state, ops)
    anchor_path = (
        Path(state.target_dir) / state.bundle_dir / "30-decomposition.md"
    )
    after_first = anchor_path.read_bytes()
    by, at = _pr_signature(403)
    assert tb.stamp_bundle_approved(
        state.target_dir, state.bundle_dir,
        approved_by=by, approved_at=at,
        restamp_nodes=_ANCHOR_NODES,
    ) == []
    assert anchor_path.read_bytes() == after_first


def test_restamp_flag_off_keeps_approved_node_untouched(
    tmp_path: Path,
) -> None:
    """Обычная доставка не перештамповывает: узлов к подписи нет вовсе.

    Дубль `test_stamp_bundle_is_idempotent` по смыслу, но с явным
    `restamp_nodes=None`: он держит границу «новый режим — только у
    переиздания», а не общий инвариант идемпотентности."""
    target = _target(tmp_path)
    task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="x", approved_at="t",
    )
    assert task_bridge.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec",
        approved_by="y", approved_at="t2",
        restamp_nodes=None,
    ) == []
    meta, _ = task_bridge.split_frontmatter(
        (
            target / "workstreams/WS-alpha-7/spec/00-charter.md"
        ).read_text(encoding="utf-8")
    )
    assert meta["approved_by"] == "x"


def test_prospective_anchor_matches_restamp(tmp_path: Path) -> None:
    """§I2 на режиме переиздания: проспективный штамп = фактический.

    Флаг обязан доходить до ОБОИХ вычислений одинаково; забытый в одном
    из них, он разводит anchor'ы, и гард `_commit_facts_cb` рвёт доставку
    между коммитом и push."""
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    bundle_dir = "workstreams/WS-alpha-7/spec"
    task_bridge.stamp_bundle_approved(
        str(target), bundle_dir, approved_by=_PREV_MERGER,
        approved_at=_PREV_MERGED_AT,
    )
    prospective = task_bridge._prospective_anchor(
        str(target), bundle_dir, "andrei-shtanakov",
        "2026-09-09T05:00:00Z", None, restamp_nodes=_ANCHOR_NODES,
    )
    task_bridge.stamp_bundle_approved(
        str(target), bundle_dir, "andrei-shtanakov",
        "2026-09-09T05:00:00Z", restamp_nodes=_ANCHOR_NODES,
    )
    assert prospective == blob_sha1(
        (Path(target) / bundle_dir / "30-decomposition.md").read_text(
            encoding="utf-8"
        )
    )


def test_supersede_pr_body_names_correction_pr(tmp_path, monkeypatch):
    """Тело переизданного PR называет источником подписи correction-PR.

    Формулировка обычной доставки («mergedBy бандл-PR») на переиздании
    ложна ровно так же, как была ложна сама подпись."""
    from governance import task_bridge as tb

    state, ops = _supersede_over_stamped_base(tmp_path, monkeypatch)
    tb.deliver_superseded(state, ops)
    assert "mergedBy correction-PR" in ops.pr_body
    assert "mergedBy бандл-PR" not in ops.pr_body


def test_supersede_resume_restamps_too(tmp_path, monkeypatch):
    """Путь возобновления (§I3 «started | нет PR») тоже перештамповывает.

    Намерение ревизии несёт `prospective_anchor`, посчитанный С
    перештампом; доставка возобновления, забывшая флаг, дала бы другой
    анкер и упёрлась бы в гард §I2 — но проверяем прямо подпись, чтобы
    тест краснел по причине, а не по сообщению гарда."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _stamp_base_as_previous_delivery(state)
    _apply_correction_to_anchor(state)
    by, at = _pr_signature(403)
    prospective = tb._prospective_anchor(
        state.target_dir, state.bundle_dir, by, at, None,
        restamp_nodes=_ANCHOR_NODES,
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    state.ops["tasks-deliver-v2"] = {
        "status": "started",
        "branch": "spec/WS-alpha-7-tasks-v2",
        "base_sha": "base-sha-1",
        "prospective_anchor": prospective,
        "approval_pr": 403,
        "signed_nodes": sorted(_ANCHOR_NODES),
        "tasks_version": 2,
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG],
        "dag_source": "previous_delivery",
        "supersedes": 1,
        "expected_generated_at": "2026-09-09T06:00:00+03:00",
        "tasks_blob": None,
        "head_sha": None,
    }
    rs.save(state)
    ops = _SupersedeOps(prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert _anchor_meta(state)["approved_by"] == _pr_signature(403)[0]


def test_supersede_legacy_bundle_uses_its_own_dag(tmp_path, monkeypatch):
    """Переиздание ЛЕГАСИ-бандла: `--legacy-bundle=5` определяет активный DAG.

    Весь легаси-путь переиздания был непроверенным кодом — ни один тест не
    звал `deliver_superseded` с `legacy_bundle != None` (F-11, мутация
    M60). Вместе с F-10 это значило: и флаг не доказан доходящим, и путь
    за флагом не доказан работающим.

    Бандл здесь — эры до раскатки acceptance (00/10/15/20/30), и §I8
    выводит его состав из каталога и якоря доставленной спеки. На мутанте
    активным становится полный шестиузловой DAG, он с выведенным не
    сходится, и переиздание отказывает «другая доставка»."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    bundle = Path(state.target_dir) / state.bundle_dir
    (bundle / "25-acceptance.md").unlink()
    (bundle / "30-decomposition.md").write_text(
        DECOMPOSITION_MD_LEGACY5, encoding="utf-8"
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    ops = _SupersedeOps(prs=[_MERGED_PR])
    assert tb.deliver_superseded(
        state, ops, legacy_bundle=5
    ) == tb.SupersedeResult("delivered", 77)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["dag"] == [[f, list(u)] for f, u in tb._BUNDLE_DAG_LEGACY5]
    assert saved["dag_source"] == "derived_from_spec"
    # Анкер §I7 берётся у ТЕРМИНАЛЬНОГО узла активного DAG, а не у
    # хардкода: провенанс спрошен по 30-decomposition.md этого бандла.
    assert ops.touched == [f"{state.bundle_dir}/30-decomposition.md"]


def test_supersede_refreshes_base_before_reading_facts(
    tmp_path, monkeypatch
):
    """Первый шаг порядка — синхронизация base, и её видно по фактам.

    `_previous_tasks_version` читает рабочее дерево НАПРЯМУЮ ровно потому,
    что вызывающая сторона уже освежила его до `base_ref` (её докстринг).
    То же и у `_previous_dag`/`_prospective_anchor`. Собственный
    `checkout_and_pull` внутри `deliver()` подстраховкой не является: он
    случается ПОСЛЕ того, как все эти факты посчитаны, — поэтому удаление
    первого вызова тестами не ловилось (F-09, мутация M55).

    Здесь стаб ведёт себя как настоящий `git pull`: приводит спеку в
    дереве к тому, что лежит в base (version 7). Без освежения версия
    читалась бы из устаревшего чекаута (1) и переиздание уехало бы в 2."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _RefreshingOps(_SupersedeOps):
        def checkout_and_pull(self, target_dir, branch):
            super().checkout_and_pull(target_dir, branch)
            (Path(target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
                "---\nspec_stage: tasks\nversion: 7\n---\n\nbody\n",
                encoding="utf-8",
            )

    ops = _RefreshingOps(prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["tasks_version"] == 8
    text = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert "version: 8" in text


def test_supersede_version_is_monotonic(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec").mkdir(exist_ok=True)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nversion: 4\n---\n", encoding="utf-8"
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    ops = _SupersedeOps(prs=[_MERGED_PR])
    tb.deliver_superseded(state, ops)
    text = (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").read_text()
    assert "version: 5" in text


def test_supersede_dag_mismatch_fails_closed(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "СТАРЫЙ",
        "dag": [["00-charter.md", []], ["10-requirements.md", ["charter"]]],
    }
    rs.save(state)
    with pytest.raises(RuntimeError, match="другая доставка"):
        tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))


def test_supersede_legacy_without_content_anchor_records_unavailable(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    # без content_anchor
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5}
    rs.save(state)
    tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["comparison"] == "unavailable"


def _v1_anchor_of_current_tree(state):
    """`anchor` §I2 записи v1, посчитанный по ТЕКУЩЕМУ дереву.

    Нужен ровно одному случаю: запись старого образца, где `anchor` есть
    и он «сошёлся бы», а `content_anchor` нет.
    """
    from governance import task_bridge as tb

    return tb._prospective_anchor(
        state.target_dir, state.bundle_dir, *_pr_signature(403), None,
        restamp_nodes=_ANCHOR_NODES,
    )


@pytest.mark.parametrize(
    "v1_extra",
    [{}, {"content_anchor": None}, "anchor-без-content"],
    ids=["ключа-нет", "content-anchor-null", "старый-anchor-не-в-счёт"],
)
def test_supersede_unavailable_without_content_anchor(
    v1_extra, tmp_path, monkeypatch
):
    """§6: сверять не с чем — «ключа нет», `null` и запись СТАРОГО образца.

    Легаси-леджер ключа не несёт вовсе; новая реконсиляция может честно
    записать `content_anchor: None` (байты PR-а прочитать не удалось).
    Третий случай — ядро правила «поле `anchor` задним числом не
    переосмысливается»: в записи лежит `anchor`, который на этом дереве
    СОШЁЛСЯ БЫ, но он отвечал на вопрос §I2 («те ли байты доставлены»), а
    не на вопрос §I5. Принять его за содержание значило бы объявить
    сверку там, где её не было, — и притом объявить бесследный no-op,
    оставив воркстрим неремонтируемым.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    extra = (
        {"anchor": _v1_anchor_of_current_tree(state)}
        if isinstance(v1_extra, str) else v1_extra
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5, **extra}
    rs.save(state)
    assert tb.deliver_superseded(
        state, _SupersedeOps(prs=[_MERGED_PR])
    ) == tb.SupersedeResult("delivered", 77)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["comparison"] == "unavailable"


def test_supersede_right_after_delivery_is_traceless_noop(
    tmp_path, monkeypatch
):
    """Первый `--supersede` сразу после обычной доставки — бесследный no-op.

    Сквозной случай §I5, ради которого v1 и обязана писать
    `content_anchor`: апстрим не менялся между доставкой и переизданием,
    значит ни ветки, ни PR, ни записи в леджере быть не должно —
    `run.json` побайтово прежний.

    ДВЕ доставки здесь подписаны РАЗНЫМИ PR-ами: v1 штампует подписью
    бандл-PR #5, а переиздание взяло бы подпись correction-PR #403
    (`_pr_signature` их различает). Пока §I5 сравнивал пост-штамповый
    `anchor`, подпись входила в сравниваемые байты, равенство не
    достигалось никогда, и ЭТОТ вызов заводил v2 — сколько бы раз его ни
    повторяли.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    assert tb.deliver_for_run(state, _SupersedeOps(prs=[_MERGED_PR])) == 77
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    ops = _SupersedeOps(prs=[_MERGED_PR])
    result = tb.deliver_superseded(rs.load("r-recon"), ops)
    assert result == tb.SupersedeResult("noop")
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before
    # Провенанс §I7 не разрешался ВООБЩЕ: сверка §I5 стоит выше него.
    # Утверждение о вызовах, а не только об исходе, — «последним, кто
    # трогал анкер» после доставки становится штамп-коммит нашего же
    # tasks-PR, и порядок обязан не давать принять его за correction.
    assert ops.touched == []
    assert not [c for c in ops.calls if c[0] in (
        "prs_containing_commit", "pr_files"
    )]
    assert ("pr_facts", 403) not in ops.calls


# --- content_anchor: канонизация (§I5) и поузловой §I7 -------------------


_ALL_NODES = frozenset(
    task_bridge._node_id(fname) for fname, _ in task_bridge._BUNDLE_DAG
)
_REQUIREMENTS_REL = "workstreams/WS-alpha-7/spec/10-requirements.md"


def _apply_correction_to_node(state, node: str) -> None:
    """Correction-PR правит ТЕЛО произвольного узла, frontmatter не трогает.

    Тот же смысл, что у `_apply_correction_to_anchor`, но не для анкера:
    поузловому §I7 нужен узел ВЫШЕ анкера — им проверяется, что
    механическая перепиновка подпись не двигает."""
    path = Path(state.target_dir) / state.bundle_dir / node
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    path.write_text(text + "\nПравка correction'а.\n", encoding="utf-8")


def _node_bytes(state) -> dict[str, bytes]:
    """Байты всех узлов бандла — для побайтовой сверки «узел не тронут»."""
    base = Path(state.target_dir) / state.bundle_dir
    return {
        fname: (base / fname).read_bytes()
        for fname, _ in task_bridge._BUNDLE_DAG
    }


def test_pr_facts_stub_differs_per_pr(tmp_path, monkeypatch) -> None:
    """Гвард самого стаба: подпись зависит от НОМЕРА PR.

    Пока `_ProvOps.pr_facts` отдавал одно и то же на любой номер,
    бандл-PR и correction-PR были в тестах неразличимы, и регрессия §I5
    зеленела тавтологически. Тест держит стаб честным: сломав его
    обратно, ловим ЗДЕСЬ, а не через полдюжины ложно-зелёных тестов."""
    state = _recon_state(tmp_path, monkeypatch)
    ops = _ProvOps()
    bundle = ops.pr_facts("owner/alpha", 5)
    correction = ops.pr_facts("owner/alpha", 403)
    assert bundle["mergedBy"] != correction["mergedBy"]
    assert bundle["mergedAt"] != correction["mergedAt"]
    assert state.pr == 5      # фикстура: бандл-PR — именно #5


def test_content_anchor_is_signature_free(tmp_path: Path) -> None:
    """Определение канонизации: подпись approve в `content_anchor` не входит.

    Не тавтология: байты анкера при перештампе МЕНЯЮТСЯ (утверждается
    рядом), и пост-штамповый `anchor` §I2 меняется вместе с ними —
    неизменным остаётся ровно канонический хэш."""
    target = str(_target(tmp_path))
    bundle = "workstreams/WS-alpha-7/spec"
    task_bridge.stamp_bundle_approved(target, bundle, "первый", "t1")
    before_hash = task_bridge._content_anchor(target, bundle, None)
    before_bytes = (
        Path(target) / bundle / "30-decomposition.md"
    ).read_bytes()
    task_bridge.stamp_bundle_approved(
        target, bundle, "второй", "t2", restamp_nodes=_ALL_NODES
    )
    assert (
        Path(target) / bundle / "30-decomposition.md"
    ).read_bytes() != before_bytes
    assert task_bridge._content_anchor(target, bundle, None) == before_hash


def test_content_anchor_does_not_leak_signature_through_pins(
    tmp_path: Path,
) -> None:
    """Подпись ВЕРХНЕГО узла не протекает в хэш каскадом через пины.

    Реальный пин считается с байтов upstream-файла ВМЕСТЕ с подписью:
    сменив подпись одному charter'у, мы двигаем пин requirements, оттуда
    — behaviour-spec, и так до анкера. Канонизация обязана пересчитывать
    пины из signature-free представлений; вырезав подпись только у самого
    узла, §I5 всё равно расходился бы — тот же дефект другим путём."""
    target = str(_target(tmp_path))
    bundle = "workstreams/WS-alpha-7/spec"
    task_bridge.stamp_bundle_approved(target, bundle, "первый", "t1")
    before_hash = task_bridge._content_anchor(target, bundle, None)
    design = Path(target) / bundle / "20-design.md"
    before_design = design.read_bytes()
    changed = task_bridge.stamp_bundle_approved(
        target, bundle, "второй", "t2", restamp_nodes=frozenset({"charter"})
    )
    # Перепиновка действительно доехала донизу — иначе сверять нечего.
    assert f"{bundle}/30-decomposition.md" in changed
    assert design.read_bytes() != before_design
    assert task_bridge._content_anchor(target, bundle, None) == before_hash


def test_content_anchor_changes_with_node_body(tmp_path, monkeypatch) -> None:
    """Обратная сторона: правка ТЕЛА любого узла хэш меняет."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _stamp_base_as_previous_delivery(state)
    before = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    _apply_correction_to_node(state, "10-requirements.md")
    assert tb._content_anchor(
        state.target_dir, state.bundle_dir, None
    ) != before


def test_content_anchor_is_taken_from_post_stamp_state(tmp_path: Path) -> None:
    """Представление берётся с ПРОСПЕКТИВНО проштампованного состояния.

    Закрытие развилки спеки (§I2, «открытый пункт»): альтернатива —
    канонизировать сырые байты свежего base. В большинстве кругов две
    величины совпадают: к моменту переиздания base уже проштампован
    предыдущей доставкой, а её штамп канонизация и так вырезает.
    Расходятся они там, где предстоящий штамп НЕ сводится к подписи, —
    correction вернул узел в `draft`. Тогда:

    - «с сырого base» записало бы величину ДОштампового состояния,
      которой в base не будет НИКОГДА: следующий круг увидит уже
      проштампованное дерево и объявит апстрим изменившимся — лишняя
      ревизия после каждого correction'а, трогающего `status`;
    - «с проспективного штампа» записывает ровно то, что доставка и
      положит в base, — и следующий круг сходится.

    Отсюда же ответ про `status`/`version`: специально нормализовать их
    не нужно. Обе стороны сверки пост-штамповые, а перештамп подписи
    `version` не инкрементит (это правило и держит сходимость второго
    круга) — поля совпадают сами.
    """
    target = str(_target(tmp_path))
    bundle = "workstreams/WS-alpha-7/spec"
    dag = task_bridge._BUNDLE_DAG
    # v1 доставлена и вмержена: бандл в base проштампован.
    task_bridge.stamp_bundle_approved(target, bundle, "merger-5", "t5")
    # Correction вернул узел на доавторинг и правит тело.
    node = Path(target) / bundle / "10-requirements.md"
    meta, body = task_bridge.split_frontmatter(
        node.read_text(encoding="utf-8")
    )
    meta["status"] = "draft"
    node.write_text(
        task_bridge.join_frontmatter(meta, body + "\nПравка.\n"),
        encoding="utf-8",
    )
    recorded = task_bridge._content_anchor(target, bundle, None)
    raw = task_bridge._canonical_dag_hash(target, bundle, dag)
    # Развилка настоящая: на этом входе варианты дают РАЗНОЕ.
    assert raw != recorded
    # Доставка v2 делает ровно тот штамп, который был спроектирован.
    task_bridge.stamp_bundle_approved(
        target, bundle, "merger-403", "t403",
        restamp_nodes=frozenset({"requirements", "decomposition"}),
    )
    # Следующий круг сверяется с записанным — и сходится.
    assert task_bridge._content_anchor(target, bundle, None) == recorded
    # А «сырой» вариант записал бы то, чего в base не появится: после
    # доставки сырая величина стала другой, и §I5 объявил бы изменение.
    assert task_bridge._canonical_dag_hash(target, bundle, dag) != raw


def _supersede_with_correction_touching_requirements(tmp_path, monkeypatch):
    """v1 доставлена и вмержена; correction-PR правит requirements + анкер."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _stamp_base_as_previous_delivery(state)
    v1_content = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    _apply_correction_to_node(state, "10-requirements.md")
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "БЛОБ-ДОСТАВКИ-v1",
        "content_anchor": v1_content,
    }
    rs.save(state)
    ops = _SupersedeOps(
        prs=[_MERGED_PR], files=[_REQUIREMENTS_REL, _ANCHOR_REL]
    )
    return state, ops


def test_supersede_signs_only_nodes_touched_by_correction(
    tmp_path, monkeypatch
):
    """§I7 поузловой: подпись correction-PR — только затронутым узлам.

    Correction-PR тронул requirements и анкер; charter, behaviour-spec,
    design и acceptance он не видел. Приписав им подпись #403, штамп
    стёр бы факт, что эти байты одобрил другой человек в другой момент —
    ту же ложь, против которой §I7 и вводился, только с другой стороны.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state, ops = _supersede_with_correction_touching_requirements(
        tmp_path, monkeypatch
    )
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    by403, at403 = _pr_signature(403)
    touched = {"10-requirements.md", "30-decomposition.md"}
    for fname, _ in tb._BUNDLE_DAG:
        meta = _anchor_meta(state, fname)
        want = (
            (by403, at403) if fname in touched
            else (_PREV_MERGER, _PREV_MERGED_AT)
        )
        assert (meta["approved_by"], meta["approved_at"]) == want, fname
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["signed_nodes"] == ["decomposition", "requirements"]


def test_supersede_mechanical_repin_does_not_change_signature(
    tmp_path, monkeypatch
):
    """Перепиновка downstream-узла — не событие approve.

    behaviour-spec correction-PR не менял, но его пин на requirements
    сдвинулся (у requirements сменилась подпись ⇒ сменились байты).
    Байты узла обязаны измениться, подпись — нет: иначе поузловое правило
    вырождается обратно в bundle-wide, только через каскад пинов."""
    from governance import task_bridge as tb

    state, ops = _supersede_with_correction_touching_requirements(
        tmp_path, monkeypatch
    )
    before = _node_bytes(state)
    tb.deliver_superseded(state, ops)
    after = _node_bytes(state)
    beh = _anchor_meta(state, "15-behaviour-spec.md")
    assert after["15-behaviour-spec.md"] != before["15-behaviour-spec.md"]
    assert beh["upstream_hashes"]["requirements"] != task_bridge.blob_sha1(
        before["10-requirements.md"].decode("utf-8")
    )
    assert (beh["approved_by"], beh["approved_at"]) == (
        _PREV_MERGER, _PREV_MERGED_AT
    )
    # Узел, у которого не менялся ни он сам, ни его апстрим, — побайтово
    # прежний: перештамп не расползается по бандлу вовсе.
    assert after["00-charter.md"] == before["00-charter.md"]


def test_supersede_after_merged_revision_is_noop_again(tmp_path, monkeypatch):
    """Повтор сразу после мержа v2 — снова бесследный no-op.

    Последний, кто трогал анкер, теперь tasks-PR самой ревизии v2 (#77):
    ищи переиздание провенанс до сверки, оно приняло бы СВОЙ ЖЕ PR за
    correction, взяло бы его подпись и завело v3 — и так до бесконечности.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state, ops = _supersede_with_correction_touching_requirements(
        tmp_path, monkeypatch
    )
    assert tb.deliver_superseded(state, ops).kind == "delivered"
    # Рабочее дерево = то, что доставила и вмержила v2 (её штамп в base).
    state = rs.load("r-recon")
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    tasks_pr_v2 = {**_MERGED_PR, "number": 77}
    again = _SupersedeOps(commit="c-v2", prs=[tasks_pr_v2])
    assert tb.deliver_superseded(state, again) == tb.SupersedeResult("noop")
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before
    assert again.touched == []
    assert ("pr_facts", 77) not in again.calls


class _RevisionPrOps(_SupersedeOps):
    """У ревизии есть свой PR: `find_pr` отдаёт его, `pr_facts` различает
    correction-PR (провенанс, номер из `_MERGED_PR`) и PR ревизии
    (идентичность — state + headRefOid)."""

    def __init__(self, pr=None, pr_state="OPEN", head="h", local_head=None,
                 **kw):
        super().__init__(**kw)
        self.pr, self.pr_state, self.head = pr, pr_state, head
        # Локальный head ВЕТКИ ревизии (не HEAD базы): им управляет
        # `_recover_commit` (§I3.1) — None означает «коммита ещё нет».
        self.local_head = local_head

    def rev_parse(self, target_dir, ref):
        if ref == "HEAD":
            return super().rev_parse(target_dir, ref)
        return self.local_head

    def find_pr(self, repo_slug, branch, *, any_state=False):
        self.calls.append(("find_pr", branch))
        return self.pr

    def pr_facts(self, repo_slug, pr):
        self.calls.append(("pr_facts", pr))
        if pr == self.pr:
            return {"state": self.pr_state, "headRefOid": self.head}
        return super().pr_facts(repo_slug, pr)


def _revision_intent(state, prospective, **over):
    """Намерение ревизии v2 в форме §I4 (та же, что пишет _start_revision)."""
    from governance import task_bridge as tb

    intent = {
        "status": "started",
        "revision": 2,
        "branch": "spec/WS-alpha-7-tasks-v2",
        "base_sha": "base-sha-1",
        "prospective_anchor": prospective,
        "approval_pr": 403,
        "signed_nodes": sorted(_ANCHOR_NODES),
        "tasks_version": 3,
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG],
        "dag_source": "previous_delivery",
        "supersedes": 1,
        "expected_generated_at": "2026-09-09T10:00:00+03:00",
        "tasks_blob": None,
        "head_sha": None,
    }
    intent.update(over)
    return intent


def _seed_revision(state, monkeypatch, **over):
    """v1 доставлена + незавершённая (по умолчанию) ревизия v2 в леджере."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    by, at = _pr_signature(403)
    prospective = tb._prospective_anchor(
        state.target_dir, state.bundle_dir, by, at, None,
        restamp_nodes=_ANCHOR_NODES,
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    state.ops["tasks-deliver-v2"] = _revision_intent(
        state, prospective, **over
    )
    rs.save(state)
    return prospective


def test_supersede_returns_existing_pr_when_revision_completed(
    tmp_path, monkeypatch
):
    """§I3 «completed | OPEN»: вернуть PR ревизии, v<N+1> НЕ создаётся."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(
        state, monkeypatch, status="completed", pr=9, head_sha="h",
        anchor="ANCHOR-V2",
    )
    before = dict(rs.load("r-recon").ops["tasks-deliver-v2"])
    ops = _RevisionPrOps(pr=9, pr_state="OPEN", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 9
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1          # v3 не заведена
    # §I4: завершённая запись не перезаписывается — ни один байт.
    assert saved.ops["tasks-deliver-v2"] == before
    assert not any(c[0] == "ensure_branch" for c in ops.calls)
    # Факты PR ревизии реально запрашиваются (C-8: их берёт цикл и
    # передаёт в решение аргументом, а не запрашивает трижды).
    assert ("find_pr", "spec/WS-alpha-7-tasks-v2") in ops.calls


def test_supersede_after_merged_revision_starts_next_one(
    tmp_path, monkeypatch
):
    """§I3 «completed | MERGED»: реконсилировать нечего, дальше решает §I5.

    Обратная сторона предыдущего теста: если бы завершённую ревизию с
    ВМЕРЖЕННЫМ PR тоже возвращали, переиздание после неё стало бы
    невозможным навсегда."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(
        state, monkeypatch, status="completed", pr=9, head_sha="h",
        anchor="СТАРЫЙ-V2",
    )
    ops = _RevisionPrOps(pr=9, pr_state="MERGED", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    saved = rs.load("r-recon")
    assert [n for n, _ in tb._revisions(saved)] == [2, 3]
    assert saved.ops["tasks-deliver-v3"]["supersedes"] == 2
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v3") in ops.calls
    assert saved.ops["tasks-deliver-v2"]["pr"] == 9          # v2 цела


def test_supersede_asks_github_once_per_revision(tmp_path, monkeypatch):
    """Реконсиляция ревизии — один `find_pr` и один `pr_facts` (C-8).

    Было четыре запроса на ревизию: цикл спрашивал `find_pr`+`pr_facts`, и
    `_reconcile_revision` повторяла `find_pr` и звала `pr_facts` дважды.
    Функционально безвредно, но на живом `gh` это лишний источник флака."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(
        state, monkeypatch, status="completed", pr=9, head_sha="h",
        anchor="ANCHOR-V2",
    )
    ops = _RevisionPrOps(pr=9, pr_state="OPEN", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 9
    )
    assert ops.calls.count(("find_pr", "spec/WS-alpha-7-tasks-v2")) == 1
    assert ops.calls.count(("pr_facts", 9)) == 1


def test_supersede_returns_open_pr_of_first_delivery(tmp_path, monkeypatch):
    """§I3 строка 1 для исторической v1: её PR OPEN → вернуть его.

    Цикл реконсиляции v1 не видит (`_revisions` собирает только
    `tasks-deliver-v<N>`), и до фикса (major C-1) это состояние уходило в
    `_previous_tasks_version` — RC 1 с сообщением про версию вместо
    контрактных RC 0 + возврат PR, либо (если спека в base есть) второй
    открытый PR на ту же спеку."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    ops = _RevisionPrOps(pr=5, pr_state="OPEN", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 5
    )
    saved = rs.load("r-recon")
    assert tb._revisions(saved) == []              # v2 не заведена
    assert not any(c[0] == "ensure_branch" for c in ops.calls)
    # Как и no-op §I5: решение принято, леджер не тронут.
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before


def test_supersede_refuses_when_first_delivery_pr_closed_unmerged(
    tmp_path, monkeypatch
):
    """§I3 «любое | CLOSED-unmerged» — включая v1: fail-closed."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    ops = _RevisionPrOps(pr=5, pr_state="CLOSED", prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="закрыт без мержа"):
        tb.deliver_superseded(state, ops)
    assert tb._revisions(rs.load("r-recon")) == []


def test_supersede_ignores_first_delivery_pr_when_revision_is_last(
    tmp_path, monkeypatch
):
    """PR v1 разбирается ТОЛЬКО когда v1 и есть последняя доставка.

    После ревизии v2 предыдущая доставка — она; висящий PR v1 (например,
    закрытый вручную после мержа ревизии) переиздание не блокирует."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(
        state, monkeypatch, status="completed", pr=9, head_sha="h",
        anchor="СТАРЫЙ-V2",
    )
    # PR ревизии (9) вмержен, PR v1 (5) — закрыт без мержа.
    class _Ops(_RevisionPrOps):
        def pr_facts(self, repo_slug, pr):
            if pr == 5:
                return {"state": "CLOSED"}
            return super().pr_facts(repo_slug, pr)

    ops = _Ops(pr=9, pr_state="MERGED", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert [n for n, _ in tb._revisions(rs.load("r-recon"))] == [2, 3]


def test_supersede_completes_started_revision_with_merged_pr(
    tmp_path, monkeypatch
):
    """§I3 «started | MERGED»: op завершается этим PR, новой ревизии нет.

    Идентичность здесь сверяется по `headRefOid` PR (это делает
    `_reconcile_revision`), а не по локальной ветке: `local_head` нарочно
    уехал — восстановление коммита (§I3.1) на этой строке не при делах."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    prospective = _seed_revision(state, monkeypatch, head_sha="h")
    ops = _RevisionPrOps(pr=7, pr_state="MERGED", prs=[_MERGED_PR],
                         local_head="ДВИНУЛИ-СНАРУЖИ")
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 7
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1
    rev = saved.ops["tasks-deliver-v2"]
    assert rev["status"] == "completed"
    assert (rev["pr"], rev["anchor"], rev["head_sha"]) == (7, prospective, "h")
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)


def test_supersede_resumes_started_revision_with_open_pr(
    tmp_path, monkeypatch
):
    """§I3 «started | OPEN, base совпал»: ревизия завершается ЭТИМ PR,
    второй PR не создаётся."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, head_sha="h")
    ops = _RevisionPrOps(pr=7, pr_state="OPEN", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 7
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1
    assert saved.ops["tasks-deliver-v2"]["status"] == "completed"
    assert saved.ops["tasks-deliver-v2"]["pr"] == 7
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)


def test_supersede_resumes_started_revision_without_pr(tmp_path, monkeypatch):
    """§I3.1 «started, PR-а нет, base совпал»: доставка доводится в ТОЙ ЖЕ
    ветке, детерминированно из намерения — один PR, новой ревизии нет."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch)
    ops = _RevisionPrOps(pr=None, prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1          # v3 не заведена
    assert saved.ops["tasks-deliver-v2"]["status"] == "completed"
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls
    # Байты — из намерения, не из текущего времени и не из свежего счётчика.
    text = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert 'generated_at: "2026-09-09T10:00:00+03:00"' in text
    assert "version: 3" in text


def test_supersede_resume_takes_signed_nodes_from_intent(
    tmp_path, monkeypatch
):
    """§I7 на возобновлении: состав подписываемых узлов — ИЗ НАМЕРЕНИЯ.

    `prospective_anchor` намерения посчитан с тем составом, что записан
    рядом с ним. Пересчитай возобновление состав заново — и ответ
    `ops.pr_files` (сегодня он шире: correction-PR якобы трогал ещё и
    charter) дал бы ДРУГИЕ байты штампа, а гард §I2 порвал бы доставку
    между коммитом и push. Durable-запись состава и есть то, что этого
    не допускает."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    # База УЖЕ проштампована доставкой v1 — иначе поузловое правило вообще
    # не при делах: на draft-узле штамп идёт переходом, а не перештампом.
    _stamp_base_as_previous_delivery(state)
    _seed_revision(state, monkeypatch)
    ops = _RevisionPrOps(
        pr=None, prs=[_MERGED_PR],
        files=["workstreams/WS-alpha-7/spec/00-charter.md", _ANCHOR_REL],
    )
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    # charter в намерении не значился — подпись correction-PR он не получил.
    assert _anchor_meta(state, "00-charter.md")["approved_by"] != (
        _pr_signature(403)[0]
    )


def test_supersede_refuses_resume_when_branch_missing_locally(
    tmp_path, monkeypatch
):
    """Прод-путь §I3.1 «head_sha записан, ветки в клоне нет, PR-а нет».

    Дырой этого места была не диагностика, а ЗАПИСЬ ЖУРНАЛА: доставка
    переигрывалась от base, `_commit_facts_cb` перезаписывал `head_sha`
    намерения новым SHA и сохранял run.json — ДО `push_branch`, который
    дальше отвергался как non-ff. Состояние самовоспроизводилось: на
    следующем заходе локальный head совпадал с уже ЗАТЁРТЫМ `head_sha`,
    и ревизия чинилась только `--abandon-revision`.

    Проверяется именно сохранность факта: `head_sha` в леджере тот же,
    коммита и push не было."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, head_sha="запушенный-коммит")
    # local_head=None — ветки ревизии в этом клоне нет; PR-а тоже нет.
    ops = _RevisionPrOps(pr=None, local_head=None, prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="--abandon-revision 2"):
        tb.deliver_superseded(state, ops)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["head_sha"] == "запушенный-коммит"   # durable-факт цел
    assert saved["status"] == "started"
    assert not any(
        c[0] in ("ensure_branch", "commit_paths", "push_branch",
                 "create_draft_pr")
        for c in ops.calls
    )


def test_supersede_refuses_merged_pr_without_recorded_head_sha(
    tmp_path, monkeypatch
):
    """`head_sha` пуст, а PR ВМЕРЖЕН — присвоить его нельзя.

    `_reconcile_revision` при пустом `head_sha` сверку `headRefOid`
    пропускает, поэтому чужой PR под именем нашей ветки завершил бы
    ревизию. Наш коммит всегда пишет `head_sha` ДО push, значит такой PR
    завёл не этот прогон — fail-closed до операторского решения."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, head_sha=None)
    ops = _RevisionPrOps(pr=7, pr_state="MERGED", prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="--abandon-revision 2"):
        tb.deliver_superseded(state, ops)
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["status"] == "started"


def test_supersede_refuses_open_pr_without_recorded_head_sha(
    tmp_path, monkeypatch
):
    """То же для возобновления: PR открыт, `head_sha` пуст — не наш PR."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, head_sha=None)
    ops = _RevisionPrOps(pr=7, pr_state="OPEN", prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="--abandon-revision 2"):
        tb.deliver_superseded(state, ops)
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["status"] == "started"
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)


def test_supersede_resume_refuses_foreign_commit(tmp_path, monkeypatch):
    """§I3.1 подключена к проду: в ветке ревизии лежит чужой коммит
    (родитель не `base_sha`) — возобновление fail-closed, не доставка."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch)
    ops = _RevisionPrOps(pr=None, local_head="commitX", prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="чужой коммит"):
        tb.deliver_superseded(state, ops)
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)


def test_supersede_resumes_when_branch_stands_on_base(tmp_path, monkeypatch):
    """Падение между `ensure_branch` и `commit_paths` — ремонтируемо.

    Прод-путь blocker'а C-2: ветка ревизии существует и стоит на её
    `base_sha`, коммита доставки нет. До фикса возобновление объявляло
    собственную ветку чужой («чужой коммит») и ревизия чинилась только
    `--abandon-revision`, хотя контракт обещал доведение доставки."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch)
    # local_head == base_sha намерения: ровно то, что оставляет
    # `ensure_branch`, создающая ветку от текущего HEAD.
    ops = _RevisionPrOps(pr=None, local_head="base-sha-1", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1          # v3 не заведена
    assert saved.ops["tasks-deliver-v2"]["status"] == "completed"
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls


def test_supersede_abandons_shifted_revision_and_starts_next(
    tmp_path, monkeypatch
):
    """§I3 «started | нет PR | base сдвинулся» — СКВОЗЬ `deliver_superseded`.

    Соседний `test_reconcile_started_no_pr_shifted_base_abandons` проверяет
    только ИМЯ перехода, а что с этим именем делает переиздание, не
    проверял никто: ветка `abandon_and_next` не исполнялась ни одним
    тестом — канарейка `raise` в её начале проходила незамеченной (F-08,
    мутации M49/M50). А это ровно тот контур, который сообщения об отказах
    объявляют ЕДИНСТВЕННЫМ выходом оператора.

    Ожидание: v2 становится терминальной `abandoned` с непустой причиной,
    заводится v3 и возвращается её PR."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    # base намерения — позапрошлый: апстрим уехал, пока ревизия лежала.
    _seed_revision(state, monkeypatch, base_sha="БАЗА-ПОЗАПРОШЛАЯ")
    ops = _RevisionPrOps(pr=None, prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    saved = rs.load("r-recon")
    v2 = saved.ops["tasks-deliver-v2"]
    assert v2["status"] == "abandoned"
    assert "base сдвинулся" in v2["reason"]
    assert [n for n, _ in tb._revisions(saved)] == [2, 3]
    # Предыдущая ДОСТАВКА — по-прежнему v1: брошенная v2 ничего не
    # доставила, и `supersedes` обязан указывать мимо неё.
    assert saved.ops["tasks-deliver-v3"]["supersedes"] == 1
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v3") in ops.calls


def test_supersede_skips_abandoned_revision(tmp_path, monkeypatch):
    """Вторая половина того же контура: `abandoned` не реконсилируется.

    Ревизию только что абандонил оператор (`--abandon-revision 2`), и её
    брошенная ветка может нести открытый PR — закрыть его оператор был не
    обязан. Пропуск в цикле реконсиляции тестами не исполнялся вовсе
    (F-08, канарейка M51): без него переиздание уткнулось бы в этот PR и
    отказало «решите судьбу PR явно» — по кругу, в уже решённой ревизии.

    Различитель здесь наблюдаемый: PR #999 подан, и если бы запись
    разбиралась, `find_pr` его нашёл бы и отказ состоялся."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(
        state, monkeypatch, status="abandoned", reason="оператор закрыл PR",
    )
    ops = _RevisionPrOps(pr=999, pr_state="OPEN", prs=[_MERGED_PR])
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    saved = rs.load("r-recon")
    assert [n for n, _ in tb._revisions(saved)] == [2, 3]
    assert saved.ops["tasks-deliver-v2"]["status"] == "abandoned"
    assert saved.ops["tasks-deliver-v2"]["reason"] == "оператор закрыл PR"
    assert saved.ops["tasks-deliver-v3"]["supersedes"] == 1
    # Терминальную запись не спрашивают у GitHub вовсе.
    assert ("find_pr", "spec/WS-alpha-7-tasks-v2") not in ops.calls


def test_supersede_refuses_resume_with_different_dag(tmp_path, monkeypatch):
    """Возобновление с другим --legacy-bundle дало бы другие байты."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, dag=[
        ["00-charter.md", []], ["10-requirements.md", ["charter"]],
    ])
    with pytest.raises(RuntimeError, match="другим составом DAG"):
        tb.deliver_superseded(state, _RevisionPrOps(pr=None, prs=[_MERGED_PR]))


def test_supersede_records_commit_facts_before_push(tmp_path, monkeypatch):
    """§I3: head_sha/tasks_blob durable-записаны МЕЖДУ коммитом и push.

    Стаб `push_branch` читает run.json С ДИСКА в момент вызова: падение
    между коммитом и push обязано оставить ревизию опознаваемой."""
    from governance import run_state as rs
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _WatchPush(_SupersedeOps):
        def __init__(self, **kw):
            super().__init__(**kw)
            # Снимок на КАЖДЫЙ push: утверждается ПЕРВЫЙ — иначе поздний
            # push замаскировал бы запись фактов, сделанную после него.
            self.at_push: list[dict | None] = []

        def push_branch(self, target_dir, branch):
            self.at_push.append(rs.load("r-recon").ops.get("tasks-deliver-v2"))
            super().push_branch(target_dir, branch)

    ops = _WatchPush(prs=[_MERGED_PR])
    tb.deliver_superseded(state, ops)
    delivered = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert ops.at_push and ops.at_push[0] is not None
    assert ops.at_push[0]["head_sha"] == "commit-sha-1"
    assert ops.at_push[0]["tasks_blob"] == blob_sha1(delivered)


def test_supersede_records_tasks_blob_before_commit(tmp_path, monkeypatch):
    """§I3.1: падение сразу ПОСЛЕ коммита оставляет ревизию с `head_sha:
    null`, но с записанным `tasks_blob` — и такой коммит следующий заход
    ПРИНИМАЕТ, а не объявляет чужим.

    Если оба факта писать одним действием, состояние «коммит есть,
    head_sha null» всегда приходит и с `tasks_blob: null`, и строка
    §I3.1 «null + подходящий коммит → принять» недостижима."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _DieAfterCommit(_SupersedeOps):
        """Коммит СОСТОЯЛСЯ, процесс умер до записи head_sha."""

        def commit_paths(self, target_dir, paths, message):
            super().commit_paths(target_dir, paths, message)
            raise RuntimeError("процесс убит сразу после коммита")

    with pytest.raises(RuntimeError, match="убит сразу после коммита"):
        tb.deliver_superseded(state, _DieAfterCommit(prs=[_MERGED_PR]))
    saved = rs.load("r-recon")
    op = saved.ops["tasks-deliver-v2"]
    assert op["status"] == "started"
    assert op["head_sha"] is None
    assert op["tasks_blob"]

    class _WithOrphanCommit(_SupersedeOps):
        """В ветке ревизии лежит НАШ коммит: родитель и блоб — из намерения."""

        def rev_parse(self, target_dir, ref):
            if ref == "HEAD":
                return super().rev_parse(target_dir, ref)
            return "commit-1"

        def commit_parent(self, target_dir, sha):
            return op["base_sha"]

        def blob_in_commit(self, target_dir, sha, rel_path):
            return op["tasks_blob"]

    # Гвард молчит: коммит опознан своим. Возврата у него нет (F-03) —
    # «принять его» исполняет сама доставка, её after_commit пишет
    # head_sha этого же коммита.
    assert tb._recover_commit(saved, _WithOrphanCommit(), 2, op, None) is None


def test_supersede_fails_when_actual_anchor_differs(tmp_path, monkeypatch):
    """§I2: фактический штамп разошёлся с проспективным — фатально.

    Ревизия остаётся `started` (её разберёт реконсиляция), push не идёт."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    monkeypatch.setattr(
        tb, "_prospective_anchor",
        lambda *a, **kw: "0000000ложный-проспективный-штамп",
    )
    ops = _SupersedeOps(prs=[_MERGED_PR])
    with pytest.raises(RuntimeError, match="разошёлся с проспективным"):
        tb.deliver_superseded(state, ops)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert not any(c[0] == "push_branch" for c in ops.calls)


def test_supersede_write_ahead_survives_delivery_failure(tmp_path, monkeypatch):
    """§I4: падение внутри deliver() оставляет ревизию `started` с намерением."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _FailingPr(_SupersedeOps):
        def create_draft_pr(self, *a, **kw):
            raise RuntimeError("gh pr create упал")

    with pytest.raises(RuntimeError, match="gh pr create упал"):
        tb.deliver_superseded(state, _FailingPr(prs=[_MERGED_PR]))
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert saved["branch"] == "spec/WS-alpha-7-tasks-v2"
    assert saved["base_sha"] == "base-sha-1"
    assert saved["approval_pr"] == 403
    assert saved["supersedes"] == 1
    assert saved["expected_generated_at"]
    # Коммит уже был — факты опознания записаны хуками ДО падения на PR.
    assert saved["head_sha"] == "commit-sha-1"
    assert saved["tasks_blob"]


def test_supersede_base_sha_unknown_fails_closed(tmp_path, monkeypatch):
    """rev_parse('HEAD') → None: «база та же» выродилась бы в тождество."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _NoHead(_SupersedeOps):
        def rev_parse(self, target_dir, ref):
            return None

    with pytest.raises(RuntimeError, match="HEAD"):
        tb.deliver_superseded(state, _NoHead(prs=[_MERGED_PR]))


def test_previous_tasks_version_missing_spec_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").unlink()
    with pytest.raises(RuntimeError, match="не найден на базе"):
        tb._previous_tasks_version(state)


def test_previous_tasks_version_unparsable_frontmatter_refuses(
    tmp_path, monkeypatch
):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        "нет frontmatter вовсе\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="frontmatter не разобрать"):
        tb._previous_tasks_version(state)


def test_previous_tasks_version_broken_yaml_refuses(tmp_path, monkeypatch):
    """Разделители целы, а YAML между ними битый — тот же fail-closed.

    Соседний тест подаёт «нет frontmatter вовсе» — это ValueError-ветка
    `split_frontmatter`. Сбой самого парсера шёл `yaml.YAMLError`, мимо
    `except ValueError` здесь и мимо `except RuntimeError` в `main`:
    вместо «версию гадать нельзя» оператор получал трейсбек PyYAML."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nversion: [1, 2\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="frontmatter не разобрать"):
        tb._previous_tasks_version(state)


def test_previous_tasks_version_non_integer_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        '---\nspec_stage: tasks\nversion: "две"\n---\n\nbody\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="не целое число"):
        tb._previous_tasks_version(state)


def test_supersede_not_completed_run_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch, status="waiting_human_merge")
    with pytest.raises(RuntimeError, match="completed"):
        tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))


def test_supersede_dirty_target_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}

    class _DirtyOps(_SupersedeOps):
        def is_dirty(self, target_dir):
            return True

    with pytest.raises(RuntimeError, match="грязный"):
        tb.deliver_superseded(state, _DirtyOps(prs=[_MERGED_PR]))


def test_supersede_no_prior_delivery_refuses(tmp_path, monkeypatch):
    """Свежий прогон без единой доставки — переиздавать нечего."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    # _recon_state кладёт state.pr = 5 по умолчанию, но op tasks-deliver не
    # заводит — прогон формально не доставлял ничего.
    with pytest.raises(RuntimeError, match="переиздавать нечего"):
        tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))


def test_cli_supersede_calls_deliver_superseded(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    called = {}

    def _fake(s, o, legacy_bundle=None, approval_pr=None):
        called["args"] = (s.run_id, legacy_bundle, approval_pr)
        return tb.SupersedeResult("delivered", 77)

    monkeypatch.setattr(tb, "deliver_superseded", _fake)
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0
    assert called["args"] == ("r-recon", None, None)
    assert "переизданная tasks-спека доставлена: PR #77" in (
        capsys.readouterr().out
    )


def test_cli_supersede_passes_flags_through(tmp_path, monkeypatch, capsys):
    """Флаги переиздания доходят до реализации НЕДЕФОЛТНЫМИ значениями.

    Соседний тест запускает голый `--supersede` и ожидает `None`, а `None`
    совпадает и с «флаг проброшен», и с «флаг выброшен» — учебная
    тавтология на стабе: обрыв `--approval-pr` и `--legacy-bundle` в
    диспетчере проходил незаметно (F-10, мутации M63/M64). Цена обрыва
    высока: `--approval-pr` — единственный операторский выход из отказа
    «ноль или несколько кандидатов» (§I7), `--legacy-bundle` —
    единственный способ переиздать легаси-бандл."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    called = {}

    def _fake(s, o, legacy_bundle=None, approval_pr=None):
        called["args"] = (s.run_id, legacy_bundle, approval_pr)
        return tb.SupersedeResult("delivered", 77)

    monkeypatch.setattr(tb, "deliver_superseded", _fake)
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main([
        "--run-id", "r-recon", "--supersede",
        "--approval-pr", "500", "--legacy-bundle", "5",
    ]) == 0
    assert called["args"] == ("r-recon", 5, 500)


def test_cli_supersede_noop_is_success(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    monkeypatch.setattr(
        tb, "deliver_superseded",
        lambda s, o, legacy_bundle=None, approval_pr=None:
            tb.SupersedeResult("noop"),
    )
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0
    # No-op о себе уже сказал сам («апстрим не менялся»); строки о
    # доставке быть не должно — доставки не было.
    assert "доставлена" not in capsys.readouterr().out


def test_cli_supersede_returned_pr_is_not_announced_as_delivered(
    tmp_path, monkeypatch, capsys
):
    """Возврат существующего PR СКВОЗЬ `main`: строки о доставке НЕТ.

    Реконсиляция §I3 в терминальных исходах отдаёт номер уже открытого PR
    (здесь — первой доставки) и сама говорит о нём правду. Пока `main`
    различал только `pr is None`, поверх правдивой строки печаталось
    «переизданная tasks-спека доставлена: PR #5»: две строки подряд,
    противоречащие друг другу, при RC 0 — оператор читает последнюю и
    идёт мержить ЧУЖОЙ PR как переиздание, хотя ни ветки
    `spec/<ws-id>-tasks-v<N>`, ни нового PR не создавалось.

    Интеграционные тесты возврата PR идут мимо `main`, а CLI-тесты
    подменяют `deliver_superseded` заглушкой — противоречие не ловилось
    ничем, поэтому здесь через `main` идёт НАСТОЯЩАЯ реализация."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    ops = _RevisionPrOps(pr=5, pr_state="OPEN", prs=[_MERGED_PR])
    monkeypatch.setattr(tb, "RealOps", lambda: ops)
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0
    out = capsys.readouterr().out
    assert "первая доставка уже открыта PR #5" in out
    assert "переизданная tasks-спека доставлена" not in out
    # И вывод не расходится с фактами: ветки/PR не создавалось.
    assert not any(c[0] == "ensure_branch" for c in ops.calls)
    assert tb._revisions(rs.load("r-recon")) == []


def test_cli_supersede_delivery_is_announced(tmp_path, monkeypatch, capsys):
    """Обратная сторона: состоявшаяся доставка объявляется.

    Без этого теста «не печатать на возврате» удовлетворяется и полным
    удалением строки — оператор терял бы номер PR своего переиздания."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps(prs=[_MERGED_PR])
    monkeypatch.setattr(tb, "RealOps", lambda: ops)
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0
    assert "переизданная tasks-спека доставлена: PR #77" in (
        capsys.readouterr().out
    )
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls


def test_cli_supersede_missing_bundle_dir_is_diagnosed(
    tmp_path, monkeypatch, capsys
):
    """Тот же отказ СКВОЗЬ `main`: RC 1 и диагностика, не трейсбек.

    Проверяется граница, на которой находка и видна оператору: `main`
    ловит только `RuntimeError`, поэтому классификация исключения в
    `_previous_dag` решает, увидит он процедуру или стек PyYAML-подобным
    образом — сырой `FileNotFoundError` пролетел бы мимо `except`."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    shutil.rmtree(Path(state.target_dir) / state.bundle_dir)
    ops = _RevisionPrOps(pr=5, pr_state="MERGED", prs=[_MERGED_PR])
    monkeypatch.setattr(tb, "RealOps", lambda: ops)
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 1
    assert "каталога бандла" in capsys.readouterr().out


def test_cli_abandon_revision_marks_and_returns_zero(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    assert tb.main([
        "--run-id", "r-recon", "--abandon-revision", "2",
        "--reason", "PR закрыт вручную",
    ]) == 0
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "abandoned"
    # Причина оператора доходит ДО леджера (F-10, мутация M65): она
    # хранится навсегда и есть единственное объяснение, почему ревизия
    # брошена; утверждать один `status` значит не отличить её от
    # подставленного литерала.
    assert saved["reason"] == "PR закрыт вручную"


def test_cli_abandon_revision_unknown_number_fails_gracefully(
    tmp_path, monkeypatch, capsys
):
    """Опечатка в номере ревизии — сообщение и RC 1, не трейсбек."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    rc = tb.main([
        "--run-id", "r-recon", "--abandon-revision", "9",
        "--reason", "промах пальцем",
    ])
    assert rc == 1
    assert "нечего абандонить" in capsys.readouterr().out


def test_cli_abandon_revision_completed_fails_gracefully(
    tmp_path, monkeypatch, capsys
):
    """§I4: завершённая запись не мутируется — отказ, а не трейсбек."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._complete_revision(state, 2, pr=10, anchor="a")
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    rc = tb.main([
        "--run-id", "r-recon", "--abandon-revision", "2",
        "--reason", "передумал",
    ])
    assert rc == 1
    assert "завершена" in capsys.readouterr().out
    # Запись цела: отказ не тронул её ни на байт.
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["status"] == "completed"


def test_cli_supersede_with_abandon_revision_refuses(
    tmp_path, monkeypatch, capsys
):
    """Два действия в одном прогоне — fail-closed, а не выбор за оператора.

    `--reason` передан: отказ обязан быть ИМЕННО про сочетание флагов, а
    не про недостающую причину."""
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main([
            "--run-id", "r", "--supersede",
            "--abandon-revision", "2", "--reason", "почему бы и нет",
        ])
    assert exc.value.code == 2
    assert "разные действия" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [["--supersede"], ["--abandon-revision", "2", "--reason", "р"]],
    ids=["supersede", "abandon"],
)
def test_cli_conform_approve_with_other_action_refuses(
    extra, tmp_path, monkeypatch, capsys
):
    """`--conform-approve` — третье действие, и оно тоже не молчит (C-6).

    Диспетчер проверяет флаги по очереди, поэтому без гварда первый
    сработавший молча съедал бы `--conform-approve` — та же «победа
    второго флага», ради которой отбито `--supersede`+`--abandon-revision`.
    """
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main(["--run-id", "r", "--conform-approve", *extra])
    assert exc.value.code == 2
    assert "--conform-approve" in capsys.readouterr().err


def test_cli_approval_pr_without_supersede_refuses(
    tmp_path, monkeypatch, capsys
):
    """`--approval-pr` без `--supersede` не доходит никуда (C-7).

    Без гварда команда молча выполняла бы ОБЫЧНУЮ доставку — не то, о чём
    просил оператор."""
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main(["--run-id", "r", "--approval-pr", "403"])
    assert exc.value.code == 2
    assert "--approval-pr" in capsys.readouterr().err


def test_cli_abandon_revision_requires_reason(tmp_path, monkeypatch, capsys):
    from governance import task_bridge as tb

    with pytest.raises(SystemExit):
        tb.main(["--run-id", "r", "--abandon-revision", "2"])
