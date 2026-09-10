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


_BUNDLE = "workstreams/WS-alpha-7/spec"
_HUMAN, _HUMAN_AT = "andrei-shtanakov", "2026-09-10T12:00:00+03:00"
_CHARTER = "00-charter.md"
_REQUIREMENTS = "10-requirements.md"
_BEHAVIOUR = "15-behaviour-spec.md"
_DESIGN = "20-design.md"
_ACCEPTANCE = "25-acceptance.md"
_DECOMPOSITION = "30-decomposition.md"


def _set_node(target: str, fname: str, **over) -> None:
    """Точечная правка frontmatter узла бандла (статус, подпись, пины)."""
    path = Path(target) / _BUNDLE / fname
    meta, body = task_bridge.split_frontmatter(path.read_text(encoding="utf-8"))
    for key, value in over.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    path.write_text(
        task_bridge.join_frontmatter(meta, body), encoding="utf-8"
    )


def _meta(target: str, fname: str) -> dict:
    """Frontmatter узла бандла."""
    return task_bridge.split_frontmatter(
        (Path(target) / _BUNDLE / fname).read_text(encoding="utf-8")
    )[0]


def _node_bytes_of(target: str) -> dict[str, bytes]:
    """Байты всех узлов бандла — для побайтовой сверки «узел не тронут»."""
    base = Path(target) / _BUNDLE
    return {
        fname: (base / fname).read_bytes()
        for fname, _ in task_bridge._BUNDLE_DAG
    }


def _repin_all(target: str, bundle_dir: str = _BUNDLE) -> None:
    """Пересчитать пины всего DAG по факту, не трогая подписи и статусы.

    Механическая перепиновка — НЕ событие approve (§I12), и здесь она
    нужна ровно затем, чтобы показать: подпись верхнего узла доезжает до
    пинов всех, кто его пинует, а канонизация обязана этого не видеть."""
    dag = task_bridge._BUNDLE_DAG
    files = {task_bridge._node_id(f): f for f, _ in dag}
    base = Path(target) / bundle_dir
    for fname, upstream_ids in dag:
        if not upstream_ids:
            continue
        path = base / fname
        meta, body = task_bridge.split_frontmatter(
            path.read_text(encoding="utf-8")
        )
        meta["upstream_hashes"] = {
            u: task_bridge.blob_sha1(
                (base / files[u]).read_text(encoding="utf-8")
            )
            for u in upstream_ids
        }
        path.write_text(
            task_bridge.join_frontmatter(meta, body), encoding="utf-8"
        )


def _approve_all(
    target: str,
    by: str = _HUMAN,
    at: str = _HUMAN_AT,
    dag=None,
    bundle_dir: str = _BUNDLE,
) -> None:
    """Честно одобренный бандл: статус, подпись и пины по факту.

    Независимый от `approve_node` оракул: тесты гейта не должны зеленеть
    оттого, что кривая запись согласована с кривым предикатом. Обход
    топологический (порядок DAG), поэтому пин каждого узла считается уже
    по финальным байтам его upstream.

    Это и есть РЕАЛЬНЫЙ base доставки под новым контрактом: бандл, по
    которому человек прошёл `--approve-node` и чей approve-PR вмержен.
    Бандл целиком `draft` — состояние ДО этого шага конвейера, и
    доставочная фикстура им быть больше не вправе (§I12).

    `approved_content_hash` (условие 4) считается ЗДЕСЬ по определению
    контракта, а не заимствуется у `_self_content_hash`: проекция —
    часть предмета проверки, и общая реализация свела бы условие (4) к
    тавтологии «мы посчитали так же, как посчитали»."""
    contour = (
        "status", "version", "approved_by", "approved_at",
        "upstream_hashes", "approved_content_hash",
    )
    dag = dag or task_bridge._BUNDLE_DAG
    files = {task_bridge._node_id(f): f for f, _ in dag}
    base = Path(target) / bundle_dir
    for fname, upstream_ids in dag:
        path = base / fname
        meta, body = task_bridge.split_frontmatter(
            path.read_text(encoding="utf-8")
        )
        meta["status"] = "approved"
        meta["approved_by"] = by
        meta["approved_at"] = at
        if upstream_ids:
            meta["upstream_hashes"] = {
                u: task_bridge.blob_sha1(
                    (base / files[u]).read_text(encoding="utf-8")
                )
                for u in upstream_ids
            }
        meta["approved_content_hash"] = task_bridge.blob_sha1(
            task_bridge.join_frontmatter(
                {k: v for k, v in meta.items() if k not in contour}, body,
            )
        )
        path.write_text(
            task_bridge.join_frontmatter(meta, body), encoding="utf-8"
        )


def _target(tmp_path: Path, approved: bool = True) -> Path:
    """Клон-фикстура с бандлом. `approved=True` (дефолт) — РЕАЛЬНЫЙ base
    доставки: DAG честно одобрен человеком. `approved=False` — бандл, как
    его сгенерировал авторинг: целиком `draft`, доставке не годится."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)
    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    (bundle / "30-decomposition.md").write_text(DECOMPOSITION_MD)
    if approved:
        _approve_all(str(target))
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
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    assert spec.exists()
    assert "### TASK-001:" in spec.read_text()
    names = [c[0] for c in ops.calls]
    # база освежается до ветки
    assert names.index("checkout_and_pull") < names.index("ensure_branch")
    # один коммит, и в нём РОВНО спека: файлов бандла доставка не несёт
    # (§I7 — наблюдаемое следствие того, что она их не трогает).
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == ("spec/WS-alpha-7-tasks.md",)
    assert ("push_branch", "spec/WS-alpha-7-tasks") in ops.calls
    assert "draft" in ops.pr_body.lower()
    assert "честно одобрен" in ops.pr_body
    # Пин tasks-спеки — blob терминального узла активного DAG в base
    # (decomposition, Task 7): доставка бандла не меняет, значит стухнуть
    # пину не от чего.
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
            # Бандл в base уже одобрен человеком — иначе гейт §I12
            # отказал бы, и тест проверял бы не тот порядок.
            _approve_all(str(target))

    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=_LateOps(),
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
            str(target), "workstreams/WS-alpha-7/spec", None,
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
    acceptance-узла): `legacy_bundle=5` принимается по
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

    # Носитель гарда — `_content_anchor` (её зовёт и доставка, и
    # переиздание): состав читается ДО любой работы с узлами.
    assert task_bridge._content_anchor(
        str(target), "workstreams/WS-alpha-7/spec", 5,
    )

    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge._content_anchor(
            str(target), "workstreams/WS-alpha-7/spec", 4,
        )
    with pytest.raises(RuntimeError) as exc_info:
        task_bridge._content_anchor(
            str(target), "workstreams/WS-alpha-7/spec", None,
        )
    message = str(exc_info.value)
    assert "25-acceptance.md" in message
    assert "--legacy-bundle=3|4|5" in message

    (bundle / "25-acceptance.md").write_text(ACCEPTANCE_MD)
    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge._content_anchor(
            str(target), "workstreams/WS-alpha-7/spec", 5,
        )


def test_legacy_bundle_exact_composition(tmp_path: Path) -> None:
    """Каталог с ровно 4 узлами (00/10/15/20, без 30-decomposition.md):
    legacy_bundle=4 принимается; legacy_bundle=3 и
    legacy_bundle=None (полный DAG) отказывают — состав не совпал точно
    (запрет «по самому длинному существующему», спека §4)."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    (bundle / "20-design.md").write_text(DESIGN_MD)

    assert task_bridge._content_anchor(
        str(target), "workstreams/WS-alpha-7/spec", 4,
    )

    with pytest.raises(RuntimeError, match="не совпадает"):
        task_bridge._content_anchor(
            str(target), "workstreams/WS-alpha-7/spec", 3,
        )
    with pytest.raises(RuntimeError, match=r"--legacy-bundle=3\|4"):
        task_bridge._content_anchor(
            str(target), "workstreams/WS-alpha-7/spec", None,
        )


def test_legacy_flag_requires_value() -> None:
    with pytest.raises(SystemExit):
        task_bridge.main(["--run-id", "r-x", "--legacy-bundle"])


def test_legacy_flag_rejects_out_of_range_value() -> None:
    with pytest.raises(SystemExit):
        task_bridge.main(["--run-id", "r-x", "--legacy-bundle", "6"])


# --- Task 7: переходный режим легаси-бандлов (без узла design) -----------


def _target_legacy(tmp_path: Path, approved: bool = True) -> Path:
    """Бандл из трёх узлов (charter/requirements/behaviour-spec) — БЕЗ
    20-design.md, как несли соседние репо до раскатки design-узла."""
    target = tmp_path / "alpha"
    bundle = target / "workstreams/WS-alpha-7/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(CHARTER_MD)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_MD)
    if approved:
        _approve_all(str(target), dag=task_bridge._dag_for(3))
    return target


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
        legacy_bundle=3,
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    meta, _ = task_bridge.split_frontmatter(spec.read_text(encoding="utf-8"))
    assert meta["traces_to"] == ["behaviour-spec"]
    assert "design" not in meta["upstream_hashes"]
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == ("spec/WS-alpha-7-tasks.md",)
    # секция резолюций design не рендерится вовсе — легаси-бандл design
    # текста не несёт
    assert "Решения открытых вопросов" not in spec.read_text()


def _target_legacy_4(tmp_path: Path, approved: bool = True) -> Path:
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
    if approved:
        _approve_all(str(target), dag=task_bridge._dag_for(4))
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
            _approve_all(str(target))

    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=_LateOps(),
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
    _approve_all(str(target))
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
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
    _approve_all(str(target))
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
    _approve_all(str(target))
    ops = _StubOps()
    pr = task_bridge.deliver(
        target_dir=str(target),
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir="workstreams/WS-alpha-7/spec",
        base_ref="master",
        ops=ops,
    )
    assert pr is not None
    spec_text = (target / "spec" / "WS-alpha-7-tasks.md").read_text()
    assert "**Mode:** verify_first" in spec_text
    # BEH-02 (BEHAVIOUR_MD) checked_by target — tests/test_y.py: fallback
    # взял его из сценария, раз структурного verifies на DT-02 нет.
    assert "**Verifies:** tests/test_y.py" in spec_text


def _target_legacy_5(
    tmp_path: Path, behaviour_md: str, decomposition_md: str,
    approved: bool = True,
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
    if approved:
        _approve_all(str(target), dag=task_bridge._dag_for(5))
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
        legacy_bundle=5,
    )
    assert pr == 77
    spec = target / "spec/WS-alpha-7-tasks.md"
    spec_text = spec.read_text()
    assert "**Mode:** verify_first" in spec_text
    assert "(DT-" in spec_text
    meta, _body = task_bridge.split_frontmatter(spec_text)
    assert meta["traces_to"] == ["decomposition"]
    # Коммит доставки несёт ровно спеку (§I7) на любом составе DAG.
    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == ("spec/WS-alpha-7-tasks.md",)


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


def _supersede_state(tmp_path: Path, monkeypatch, **kw):
    """`_recon_state` + бандл в base, ПРОШТАМПОВАННЫЙ доставкой v1.

    Боевое состояние входа переиздания: tasks-PR доставки v1 вмержен,
    значит узлы бандла в base уже `approved` с подписью ЕЁ мержера.
    `_recon_state` кладёт бандл в `draft` — вход, которого у переиздания
    не бывает: поузловой §I7 (devtools#172) на нём fail-closed'ит узел
    вне `signed_nodes`, потому что сохранять там нечего.
    """
    state = _recon_state(tmp_path, monkeypatch, **kw)
    _stamp_base_as_previous_delivery(state)
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
    # Ожидание считается ДО доставки и тем же способом, каким его
    # посчитает переиздание (§I2): доставка бандла не меняет, значит
    # байты анкера до и после неё — одни и те же.
    expected = task_bridge._prospective_anchor(
        state.target_dir, state.bundle_dir, None,
    )
    # `content_anchor` от подписи не зависит вовсе — считается без неё.
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
        tb.deliver_superseded(state, _SupersedeOps())


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


_FULL_FACTS = {
    "state": "MERGED", "baseRefName": "master",
    "mergedAt": "2026-09-09T05:00:00Z", "mergedBy": {"login": "merger"},
}


class _ForgeOps(_StubOps):
    """Стаб форджи для реконсиляции: PR по ветке + факты PR.

    Провенанс correction-PR (`last_commit_touching`/
    `prs_containing_commit`/`pr_files`) из контракта ушёл вместе со
    штампом (§I7): подпись доставка не берёт ни у одного PR, потому что
    не подписывает. Осталось ровно то, что читает реконсиляция §I3, —
    номер PR по имени ветки и его состояние.
    """

    def __init__(self, facts=None, branch_prs=None):
        super().__init__()
        # Ветка → номер PR для `find_pr`: реконсиляция ревизии опознаёт
        # свой PR именно так, а идентичность сверяет по `head_sha`.
        self.branch_prs = dict(branch_prs or {})
        # Ответ `pr_facts` настраиваемый: состояние PR — вход таблицы §I3,
        # и один ответ на любой номер снимал бы её ветвление мутацией
        # незаметно.
        self.facts = _FULL_FACTS if facts is None else facts

    def find_pr(self, repo_slug, branch, *, any_state=False):
        self.calls.append(("find_pr", branch))
        return self.branch_prs.get(branch)

    def pr_facts(self, repo_slug, pr):
        self.calls.append(("pr_facts", pr))
        return dict(self.facts)


_MERGED_PR = {
    "number": 403, "state": "MERGED", "baseRefName": "master",
    "mergedAt": "2026-09-09T05:00:00Z", "mergedBy": "andrei-shtanakov",
    "mergeCommit": "c1",
}
def _ledger_with_delivery(state, **op) -> None:
    """Леджер прогона с завершённой доставкой v1 (номер PR — в записи)."""
    from governance import run_state as rs

    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5, **op}
    rs.save(state)
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
    assert tb._recover_commit(state, _Ops(), 2, op) is None


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

    assert tb._recover_commit(state, _Ops(), 2, op) is None


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

    assert tb._recover_commit(state, _Ops(), 2, op) is None


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
        tb._recover_commit(state, _Ops(), 2, op)
    assert "remote" not in str(exc.value)


def test_recover_commit_refuses_recorded_head_without_local_branch(
    tmp_path, monkeypatch
):
    """§I3.1 «записан | ветки в клоне нет» — fail-closed.

    Гвард зовётся только на пути переиздания (PR ревизии не найден),
    поэтому опознать коммит здесь нечем: живого PR с `headRefOid` нет.
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
        tb._recover_commit(state, _Ops(), 2, op)
    assert "--abandon-revision 2" in str(exc.value)


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
        tb._recover_commit(state, _Ops(), 2, op)


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
        tb._recover_commit(state, _Ops(), 2, op)


# --- deliver_superseded: сборка переиздания (Task 7) ----------------------


class _SupersedeOps(_ForgeOps):
    """Стаб на базе `_ForgeOps` для `deliver_superseded`: живого git нет,
    примитивы восстановления коммита/идентичности возвращают None —
    ветвление на "коммита ещё нет" (`_recover_commit`) и "PR не найден"
    (`_reconcile_revision`), не на чужую работу под тем же именем.
    `find_pr` наследуется от `_ForgeOps`: по умолчанию карта веток пуста,
    то есть PR-а по ветке нет.

    `rev_parse` наследуется от `_StubOps` (Task 7b): "HEAD" → синтетический
    `base-sha-1` (иначе fail-closed гард §I2 останавливает переиздание до
    предмета теста), ветка ревизии → None, то есть коммита ещё нет."""

    def commit_parent(self, target_dir, sha):
        return None

    def blob_in_commit(self, target_dir, sha, rel_path):
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
    ops = _ForgeOps()
    result = tb.deliver_superseded(state, ops)
    after = (rs.run_dir("r-recon") / "run.json").read_bytes()
    assert result == tb.SupersedeResult("noop")
    assert before == after
    # Ни ветки, ни PR, и ни одного сетевого вызова СВЕРХ реконсиляции
    # §I3 (шаг 2 порядка): сверка §I5 считается локально по base, а гейт
    # §I12 до неё не доходит.
    assert [c for c in ops.calls if c[0] == "pr_facts"] == [("pr_facts", 5)]
    assert not [c for c in ops.calls if c[0] in (
        "ensure_branch", "create_draft_pr"
    )]


def test_supersede_changed_anchor_opens_new_branch_and_pr(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps()
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "completed"
    assert saved["supersedes"] == 1
    # Провенанс correction-PR из механизма ушёл (§I7): полей нет вовсе,
    # а не «есть со значением None» — величины больше не существует.
    assert "approval_pr" not in saved and "signed_nodes" not in saved
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


# --- РЕАЛЬНОЕ состояние base переиздания ---------------------------------
#
# Фикстура обязана описывать состояние ПОСЛЕ предыдущего шага конвейера,
# а не начальное. Бандл, целиком лежащий `draft`, — это вход авторинга;
# вход переиздания другой: человек прошёл по DAG `--approve-node`,
# approve-PR вмержен, и в base лежит честно одобренный бандл. Хелперы
# ниже приводят фикстуру ровно в это состояние.

_PREV_MERGER = "prev-bundle-merger"
_PREV_MERGED_AT = "2026-09-01T00:00:00Z"


def _stamp_base_as_previous_delivery(
    state, legacy_bundle: int | None = None
) -> None:
    """Бандл в base переиздания: DAG честно одобрен человеком.

    Раньше это состояние создавал штамп доставки v1 (её tasks-PR нёс
    файлы бандла и был вмержен). Теперь доставка бандла не касается
    (§I7), и в base лежит результат человеческой волны `--approve-node`,
    чей approve-PR вмержен, — та же честно одобренная фикстура, только
    происхождение подписи другое."""
    from governance import task_bridge as tb

    _approve_all(
        state.target_dir, by=_PREV_MERGER, at=_PREV_MERGED_AT,
        dag=tb._dag_for(legacy_bundle), bundle_dir=state.bundle_dir,
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
    return state, _SupersedeOps()
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
    # Штамп v1 — ПОСЛЕ правки состава каталога и по легаси-DAG: базу
    # переиздания оставляет вмерженный tasks-PR доставки v1.
    _stamp_base_as_previous_delivery(state, legacy_bundle=5)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    ops = _SupersedeOps()
    assert tb.deliver_superseded(
        state, ops, legacy_bundle=5
    ) == tb.SupersedeResult("delivered", 77)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["dag"] == [[f, list(u)] for f, u in tb._BUNDLE_DAG_LEGACY5]
    assert saved["dag_source"] == "derived_from_spec"
    # Активный DAG легаси-эры: 25-acceptance.md в него не входит вовсе,
    # и ни гейт §I12, ни anchor его не читают.
    assert not (
        Path(state.target_dir) / state.bundle_dir / _ACCEPTANCE
    ).exists()


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

    state = _supersede_state(tmp_path, monkeypatch)
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

    ops = _RefreshingOps()
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

    state = _supersede_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec").mkdir(exist_ok=True)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nversion: 4\n---\n", encoding="utf-8"
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    ops = _SupersedeOps()
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
        tb.deliver_superseded(state, _SupersedeOps())


def test_supersede_legacy_without_content_anchor_records_unavailable(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    # без content_anchor
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5}
    rs.save(state)
    tb.deliver_superseded(state, _SupersedeOps())
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["comparison"] == "unavailable"


def _v1_anchor_of_current_tree(state):
    """`anchor` §I2 записи v1, посчитанный по ТЕКУЩЕМУ дереву.

    Нужен ровно одному случаю: запись старого образца, где `anchor` есть
    и он «сошёлся бы», а `content_anchor` нет.
    """
    from governance import task_bridge as tb

    return tb._prospective_anchor(
        state.target_dir, state.bundle_dir, None,
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

    state = _supersede_state(tmp_path, monkeypatch)
    extra = (
        {"anchor": _v1_anchor_of_current_tree(state)}
        if isinstance(v1_extra, str) else v1_extra
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5, **extra}
    rs.save(state)
    assert tb.deliver_superseded(
        state, _SupersedeOps()
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
    assert tb.deliver_for_run(state, _SupersedeOps()) == 77
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    ops = _SupersedeOps()
    result = tb.deliver_superseded(rs.load("r-recon"), ops)
    assert result == tb.SupersedeResult("noop")
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before
    # Равенство `content_anchor` достижимо потому, что обе стороны
    # сверки описывают одни и те же байты бандла в base: доставка их не
    # трогала. Сверх реконсиляции §I3 сетевых вызовов нет.
    assert [c for c in ops.calls if c[0] == "pr_facts"] == [("pr_facts", 77)]
    assert not [c for c in ops.calls if c[0] == "create_draft_pr"]


# --- content_anchor: канонизация (§I2/§I5) -------------------------------


_REQUIREMENTS_REL = "workstreams/WS-alpha-7/spec/10-requirements.md"


def _apply_correction_to_node(state, node: str) -> None:
    """Correction правит ТЕЛО произвольного узла, frontmatter не трогает.

    Тот же смысл, что у `_apply_correction_to_anchor`, но не для анкера:
    узел ВЫШЕ анкера показывает, что `content_anchor` — манифест по
    ВСЕМУ DAG, а не блоб терминального узла."""
    path = Path(state.target_dir) / state.bundle_dir / node
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    path.write_text(text + "\nПравка correction'а.\n", encoding="utf-8")


def _node_bytes(state) -> dict[str, bytes]:
    """Байты всех узлов бандла — для побайтовой сверки «узел не тронут»."""
    return _node_bytes_of(state.target_dir)


def test_content_anchor_is_signature_free(tmp_path: Path) -> None:
    """Определение канонизации: подпись approve в `content_anchor` не входит.

    Не тавтология: байты анкера при повторном одобрении МЕНЯЮТСЯ
    (утверждается рядом), и артефактный `anchor` §I2 меняется вместе с
    ними — неизменным остаётся ровно канонический хэш.

    Довод остаётся в силе и после отмены штампа: §I5 спрашивает про
    СОДЕРЖАНИЕ апстрима, а «кто подписал» — другой вопрос. Живой
    контрпример даёт граница миграции: бандл, одобренный прежним
    штампом, несёт синтетическую подпись при неизменном содержании."""
    target = str(_target(tmp_path))
    bundle = _BUNDLE
    before_hash = task_bridge._content_anchor(target, bundle, None)
    before_bytes = (Path(target) / bundle / _DECOMPOSITION).read_bytes()

    _approve_all(target, by="второй", at="t2")

    assert (Path(target) / bundle / _DECOMPOSITION).read_bytes() != (
        before_bytes
    )
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
    bundle = _BUNDLE
    before_hash = task_bridge._content_anchor(target, bundle, None)
    design = Path(target) / bundle / _DESIGN
    before_design = design.read_bytes()

    # Подпись меняется у ОДНОГО charter'а; пины пересчитываются по факту,
    # то есть каскадом доезжают донизу.
    _set_node(target, _CHARTER, approved_by="второй", approved_at="t2")
    _repin_all(target)

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


# --- Гейт §I12: доставка ТРЕБУЕТ одобренности, а не создаёт её ------------
#
# `draft`/`stale` у узла — штатный след correction'а, а не экзотика:
# коррекция бандла приводит узлы ровно в эти статусы, значит доставка
# встречает их каждый раз. Там, где `stamp_bundle_approved` СОЗДАВАЛ
# approved-состояние (и потому не мог отличить «уже одобрено» от
# «одобрено нами только что»), гейт его ТРЕБУЕТ — операция сменила знак,
# а fail-open стал fail-closed.

_PREV_BY, _PREV_AT = "prev-merger", "2026-09-01T00:00:00Z"


def _deliver(target: str, ops, **kw) -> int:
    return task_bridge.deliver(
        target_dir=target,
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        subject="s",
        bundle_dir=_BUNDLE,
        base_ref="master",
        ops=ops,
        **kw,
    )


@pytest.mark.parametrize("status", ["draft", "stale"])
def test_gate_refuses_a_debtor_node_naming_it_and_the_procedure(
    tmp_path: Path, status,
) -> None:
    """Механика долг не гасит. Диагностика называет узел, его статус и
    процедуру — отказ без процедуры бесполезен."""
    target = str(_target(tmp_path))
    _set_node(target, _DESIGN, status=status)
    ops = _StubOps()

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        _deliver(target, ops)

    message = str(exc.value)
    assert f"{_BUNDLE}/{_DESIGN}" in message
    assert status in message
    assert "--approve-node design" in message
    assert "топологическом порядке" in message
    assert ops.calls == [("checkout_and_pull", "master")]


def test_gate_refuses_approved_node_with_drifted_pins_naming_both_values(
    tmp_path: Path,
) -> None:
    """Подпись стоит под байтами, которых в дереве нет (spec-runner#410).

    Молчаливой перепиновки не происходит: она и была дефектом."""
    target = str(_target(tmp_path))
    fake = "ab" + "0" * 38
    _set_node(target, _DESIGN, upstream_hashes={
        "requirements": fake, "behaviour-spec": fake,
    })

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        _deliver(target, _StubOps())

    message = str(exc.value)
    assert fake in message
    actual = task_bridge.blob_sha1(
        (Path(target) / _BUNDLE / _REQUIREMENTS).read_text(encoding="utf-8")
    )
    assert actual in message


@pytest.mark.parametrize(
    "signature",
    [{"approved_by": None, "approved_at": None},
     {"approved_by": "", "approved_at": ""}],
    ids=["keys-absent", "empty-strings"],
)
def test_gate_refuses_approved_without_the_approver(
    tmp_path: Path, signature,
) -> None:
    """«Одобрено» без одобрившего контракт не пропускает: шаблон бандла
    заводит `approved_by: ""`, и пустая строка есть «не подписано»."""
    target = str(_target(tmp_path))
    _set_node(target, _CHARTER, **signature)

    with pytest.raises(RuntimeError, match="approved_by/approved_at пусты"):
        _deliver(target, _StubOps())


def test_gate_refuses_a_bundle_approved_by_the_former_stamp(
    tmp_path: Path,
) -> None:
    """Граница миграции ОТМЕНЕНА находкой ревью §I12.

    Прежнее правило принимало старые `approved` как есть. Но без
    `approved_content_hash` собственные байты одобренного узла
    принципиально непроверяемы — не «плохо проверяемы», а никак:
    одобрение, которое нечем предъявить, гейту предъявить нечего. Такой
    узел несёт РАЗОВЫЙ миграционный долг, и гасится он обычным
    переодобрением, без особого режима и отдельного флага."""
    target = str(_target(tmp_path, approved=False))
    _approve_all(target, by="prev-bundle-merger", at="2026-09-01T00:00:00Z")
    for fname, _ in task_bridge._BUNDLE_DAG:
        _set_node(target, fname, approved_content_hash=None)

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        _deliver(target, _StubOps())

    message = str(exc.value)
    assert "approved_content_hash не записан" in message
    assert "миграционный долг" in message
    assert "--approve-node charter" in message


def test_migration_debt_is_discharged_by_ordinary_reapproval(
    tmp_path: Path,
) -> None:
    """Долг гасится один раз топологическим переодобрением активного DAG —
    теми же вызовами `--approve-node`, что и всякий другой."""
    target = str(_target(tmp_path, approved=False))
    _approve_all(target, by="штамп", at="2026-09-01T00:00:00Z")
    for fname, _ in task_bridge._BUNDLE_DAG:
        _set_node(target, fname, approved_content_hash=None)

    for fname, _ in task_bridge._BUNDLE_DAG:
        _approve(target, task_bridge._node_id(fname))

    assert task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG
    ) == []
    assert _deliver(target, _StubOps()) == 77


def test_delivery_commit_carries_only_the_tasks_spec(tmp_path: Path) -> None:
    """Наблюдаемое следствие §I7, по которому проверку видно снаружи:
    файлов бандла в diff доставки нет вовсе."""
    target = str(_target(tmp_path))
    before = _node_bytes_of(target)
    ops = _StubOps()

    _deliver(target, ops)

    commit = next(c for c in ops.calls if c[0] == "commit_paths")
    assert commit[1] == ("spec/WS-alpha-7-tasks.md",)
    assert _node_bytes_of(target) == before
    assert "штамп" not in ops.pr_body


def test_delivery_leaves_statuses_and_signatures_verbatim(
    tmp_path: Path,
) -> None:
    """Подписи и статусы узлов после доставки дословно прежние: доставка
    не одобряет, не перештамповывает и не перепиновывает."""
    target = str(_target(tmp_path))
    _deliver(target, _StubOps())

    for fname, _ in task_bridge._BUNDLE_DAG:
        meta = _meta(target, fname)
        assert meta["approved_by"] == _HUMAN
        assert meta["approved_at"] == _HUMAN_AT
        assert meta["status"] == "approved"


def test_supersede_fail_closed_leaves_no_branch_commit_or_ledger_entry(
    tmp_path, monkeypatch
) -> None:
    """Гейт §I12 отказывает ДО `_start_revision` — следов не остаётся.

    Порядок §I2 ставит гейт между сверкой §I5 и намерением, поэтому
    долговой узел роняет переиздание раньше ветки, коммита, PR и записи в
    леджере. Съедь отказ внутрь `deliver()` (там гейт тоже стоит, и это
    правильно) — оператор получил бы ту же диагностику, но с
    оставленной started-ревизией, которую пришлось бы разбирать
    реконсиляцией.

    Парный случай — `..._noop_with_debtor_nodes...`: там тот же
    `draft`-узел даёт бесследный no-op, потому что апстрим не менялся.
    Вдвоём они и держат порядок: сначала §I5, только потом §I12."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    # Апстрим изменился (записанный content_anchor заведомо другой), а
    # charter лежит `draft` — доставка обязана отказать.
    _set_node(state.target_dir, _CHARTER, status="draft")
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ",
                                  "content_anchor": "ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps()

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        tb.deliver_superseded(state, ops)

    assert f"{_BUNDLE}/{_CHARTER}" in str(exc.value)
    assert "--approve-node charter" in str(exc.value)
    assert not any(
        call[0] in ("ensure_branch", "commit_paths", "push_branch",
                    "create_draft_pr")
        for call in ops.calls
    )
    assert "tasks-deliver-v2" not in rs.load("r-recon").ops


def test_supersede_noop_with_debtor_nodes_names_them_instead_of_refusing(
    tmp_path, monkeypatch, capsys,
) -> None:
    """§I5 стоит ВЫШЕ гейта §I12 — и это осознанный порядок.

    Воркстрим, в чьём активном DAG лежит `draft`-узел, но апстрим с
    прошлой доставки не менялся, завершается бесследным no-op'ом, а не
    отказом: гейт защищает ДОСТАВКУ, а там, где доставки не будет,
    защищать нечего. Fail-open не возникает по построению — ни ветки, ни
    PR, ни записи. Обязанность у no-op'а всё же есть: назвать долговые
    узлы, чтобы оператор узнал про долг тогда же, когда спрашивает про
    переиздание, а не на следующем запуске.

    Парный случай — `..._fail_closed_leaves_no_branch...`: там содержание
    ИЗМЕНИЛОСЬ, и тот же `draft`-узел даёт fail-closed. Вдвоём эти два
    теста и держат порядок: сначала §I5, только потом §I12."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "БЛОБ-ДОСТАВКИ-v1",
        "content_anchor": tb._content_anchor(
            state.target_dir, state.bundle_dir, None
        ),
    }
    rs.save(state)
    # Долг ставится ПОСЛЕ снятия `content_anchor`: смена статуса — правка
    # файла, и записанный хэш обязан описывать те же байты, что читает
    # сверка, иначе тест проверял бы не порядок, а расхождение.
    _set_node(state.target_dir, _CHARTER, status="draft")
    state.ops["tasks-deliver"]["content_anchor"] = tb._content_anchor(
        state.target_dir, state.bundle_dir, None
    )
    rs.save(state)
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    ops = _SupersedeOps()

    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult("noop")

    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before
    assert not [c for c in ops.calls if c[0] in (
        "ensure_branch", "create_draft_pr"
    )]
    out = capsys.readouterr().out
    assert "апстрим не менялся" in out
    assert f"{_BUNDLE}/{_CHARTER}" in out
    assert "--approve-node charter" in out


def _supersede_with_correction_touching_requirements(
    tmp_path, monkeypatch, **ops_kw
):
    """v1 доставлена и вмержена; correction правит requirements, и волна
    одобрения по DAG уже прошла — то есть РЕАЛЬНЫЙ base переиздания.

    Фикстура описывает состояние ПОСЛЕ предыдущего шага конвейера, а не
    начальное: correction меняет тело узла и оставляет долг, человек
    гасит его `--approve-node` сверху вниз, approve-PR вмержен. Опиши она
    один только correction — гейт §I12 отказал бы, и «переиздание не
    работает» читалось бы как дефект кода вместо дефекта фикстуры.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _stamp_base_as_previous_delivery(state)
    v1_content = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    _apply_correction_to_node(state, "10-requirements.md")
    # Волна одобрения после correction'а: новая подпись, новые пины.
    _approve_all(state.target_dir, by="человек", at="2026-09-10T09:00:00Z")
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "БЛОБ-ДОСТАВКИ-v1",
        "content_anchor": v1_content,
    }
    rs.save(state)
    return state, _SupersedeOps(**ops_kw)


def test_supersede_after_merged_revision_is_noop_again(tmp_path, monkeypatch):
    """Повтор сразу после мержа v2 — снова бесследный no-op.

    `content_anchor`, записанный ревизией v2, посчитан по тем же байтам
    бандла, что лежат в base после мержа её PR: доставка бандла не
    касается, значит и расходиться нечему."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state, ops = _supersede_with_correction_touching_requirements(
        tmp_path, monkeypatch
    )
    assert tb.deliver_superseded(state, ops).kind == "delivered"
    # Рабочее дерево = то, что доставила и вмержила v2 (её штамп в base).
    state = rs.load("r-recon")
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    again = _SupersedeOps()
    assert tb.deliver_superseded(state, again) == tb.SupersedeResult("noop")
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before
    assert [c for c in again.calls if c[0] == "pr_facts"] == []


class _RevisionPrOps(_SupersedeOps):
    """У ревизии есть свой PR: `find_pr` отдаёт его, а `pr_facts` — его
    состояние и `headRefOid` (вход таблицы §I3)."""

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

    # База переиздания — бандл, проштампованный вмерженной доставкой v1
    # (идемпотентно, если вызывающий уже привёл её в это состояние).
    _stamp_base_as_previous_delivery(state)
    prospective = tb._prospective_anchor(
        state.target_dir, state.bundle_dir, None,
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
    ops = _RevisionPrOps(pr=9, pr_state="OPEN")
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
    ops = _RevisionPrOps(pr=9, pr_state="MERGED")
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
    ops = _RevisionPrOps(pr=9, pr_state="OPEN")
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
    ops = _RevisionPrOps(pr=5, pr_state="OPEN")
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
    ops = _RevisionPrOps(pr=5, pr_state="CLOSED")
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

    ops = _Ops(pr=9, pr_state="MERGED")
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
    ops = _RevisionPrOps(pr=7, pr_state="MERGED",
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
    ops = _RevisionPrOps(pr=7, pr_state="OPEN")
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 7
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1
    assert saved.ops["tasks-deliver-v2"]["status"] == "completed"
    assert saved.ops["tasks-deliver-v2"]["pr"] == 7
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)


def test_supersede_resumes_started_revision_with_open_pr_moved_branch(
    tmp_path, monkeypatch
):
    """Та же строка §I3, но ЛОКАЛЬНАЯ ветка уехала — RC 0, не отказ.

    Доставка ревизии дошла до конца (коммит `h`, push, PR #7 OPEN на
    `headRefOid == h`), а процесс умер до `_complete_revision`; оператор
    тем временем подвинул локальную `spec/<ws-id>-tasks-v2` на чужой SHA.
    Гвард §I3.1 сверяет ЛОКАЛЬНЫЙ head, и зовись он на этом исходе —
    возобновление падало бы «ветку двигали снаружи» там, где делать
    нечего: идентичность доказана `headRefOid`, доставка не
    переигрывается, ветка не трогается."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch, head_sha="h")
    ops = _RevisionPrOps(
        pr=7, pr_state="OPEN", head="h", local_head="чужой-коммит",
    )
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 7
    )
    saved = rs.load("r-recon")
    assert len(tb._revisions(saved)) == 1          # v3 не заведена
    assert saved.ops["tasks-deliver-v2"]["status"] == "completed"
    assert saved.ops["tasks-deliver-v2"]["pr"] == 7
    assert not any(
        c[0] in ("ensure_branch", "commit_paths", "push_branch",
                 "create_draft_pr")
        for c in ops.calls
    )


def test_supersede_resumes_started_revision_without_pr(tmp_path, monkeypatch):
    """§I3.1 «started, PR-а нет, base совпал»: доставка доводится в ТОЙ ЖЕ
    ветке, детерминированно из намерения — один PR, новой ревизии нет."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch)
    ops = _RevisionPrOps(pr=None)
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
    ops = _RevisionPrOps(pr=None, local_head=None)
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
    ops = _RevisionPrOps(pr=7, pr_state="MERGED")
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
    ops = _RevisionPrOps(pr=7, pr_state="OPEN")
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
    ops = _RevisionPrOps(pr=None, local_head="commitX")
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
    ops = _RevisionPrOps(pr=None, local_head="base-sha-1")
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
    ops = _RevisionPrOps(pr=None)
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
    ops = _RevisionPrOps(pr=999, pr_state="OPEN")
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
        tb.deliver_superseded(state, _RevisionPrOps(pr=None))


def test_supersede_records_commit_facts_before_push(tmp_path, monkeypatch):
    """§I3: head_sha/tasks_blob durable-записаны МЕЖДУ коммитом и push.

    Стаб `push_branch` читает run.json С ДИСКА в момент вызова: падение
    между коммитом и push обязано оставить ревизию опознаваемой."""
    from governance import run_state as rs
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    state = _supersede_state(tmp_path, monkeypatch)
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

    ops = _WatchPush()
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

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _DieAfterCommit(_SupersedeOps):
        """Коммит СОСТОЯЛСЯ, процесс умер до записи head_sha."""

        def commit_paths(self, target_dir, paths, message):
            super().commit_paths(target_dir, paths, message)
            raise RuntimeError("процесс убит сразу после коммита")

    with pytest.raises(RuntimeError, match="убит сразу после коммита"):
        tb.deliver_superseded(state, _DieAfterCommit())
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
    assert tb._recover_commit(saved, _WithOrphanCommit(), 2, op) is None


def test_supersede_fails_when_actual_anchor_differs(tmp_path, monkeypatch):
    """§I2: фактический блоб анкера разошёлся с записанным — фатально.

    Расходиться этим величинам не от чего: переиздание бандла не
    касается. Значит расхождение означает, что вход изменился между
    проверкой и эффектом — base сдвинули, дерево загрязнили, узел
    одобрили заново параллельным прогоном. Ревизия остаётся `started`
    (её разберёт реконсиляция), push не идёт."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)
    monkeypatch.setattr(
        tb, "_prospective_anchor",
        lambda *a, **kw: "0000000ложный-анкер-намерения",
    )
    ops = _SupersedeOps()
    with pytest.raises(RuntimeError, match="разошёлся с записанным"):
        tb.deliver_superseded(state, ops)
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert not any(c[0] == "push_branch" for c in ops.calls)


def test_supersede_write_ahead_survives_delivery_failure(tmp_path, monkeypatch):
    """§I4: падение внутри deliver() оставляет ревизию `started` с намерением."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    rs.save(state)

    class _FailingPr(_SupersedeOps):
        def create_draft_pr(self, *a, **kw):
            raise RuntimeError("gh pr create упал")

    with pytest.raises(RuntimeError, match="gh pr create упал"):
        tb.deliver_superseded(state, _FailingPr())
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert saved["branch"] == "spec/WS-alpha-7-tasks-v2"
    assert saved["base_sha"] == "base-sha-1"
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
        tb.deliver_superseded(state, _NoHead())


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
        tb.deliver_superseded(state, _SupersedeOps())


def test_supersede_dirty_target_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}

    class _DirtyOps(_SupersedeOps):
        def is_dirty(self, target_dir):
            return True

    with pytest.raises(RuntimeError, match="грязный"):
        tb.deliver_superseded(state, _DirtyOps())


def test_supersede_no_prior_delivery_refuses(tmp_path, monkeypatch):
    """Свежий прогон без единой доставки — переиздавать нечего."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    # _recon_state кладёт state.pr = 5 по умолчанию, но op tasks-deliver не
    # заводит — прогон формально не доставлял ничего.
    with pytest.raises(RuntimeError, match="переиздавать нечего"):
        tb.deliver_superseded(state, _SupersedeOps())


def test_cli_supersede_calls_deliver_superseded(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    called = {}

    def _fake(s, o, legacy_bundle=None, replace=None):
        called["args"] = (s.run_id, legacy_bundle, replace)
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
    тавтология на стабе: обрыв `--legacy-bundle` в диспетчере проходил
    незаметно (F-10, мутация M64). Цена обрыва высока: это единственный
    способ переиздать легаси-бандл."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    called = {}

    def _fake(s, o, legacy_bundle=None, replace=None):
        called["args"] = (s.run_id, legacy_bundle, replace)
        return tb.SupersedeResult("delivered", 77)

    monkeypatch.setattr(tb, "deliver_superseded", _fake)
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main([
        "--run-id", "r-recon", "--supersede", "--legacy-bundle", "5",
    ]) == 0
    assert called["args"] == ("r-recon", 5, None)


def test_cli_supersede_noop_is_success(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    monkeypatch.setattr(
        tb, "deliver_superseded",
        lambda s, o, legacy_bundle=None, replace=None:
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
    ops = _RevisionPrOps(pr=5, pr_state="OPEN")
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

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps()
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
    ops = _RevisionPrOps(pr=5, pr_state="MERGED")
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


def test_cli_has_no_approval_pr_flag_anymore(capsys) -> None:
    """Флаг ушёл вместе с провенансом штампа (§I7): подпись доставка не
    берёт ни у одного PR, потому что не подписывает."""
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main(["--run-id", "r", "--supersede", "--approval-pr", "403"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_abandon_revision_requires_reason(tmp_path, monkeypatch, capsys):
    from governance import task_bridge as tb

    with pytest.raises(SystemExit):
        tb.main(["--run-id", "r", "--abandon-revision", "2"])


# --- replace-переход: замена незамерженного предложения (§I10) -----------
#
# Живой случай: PR spec-runner#408 доставлен ревизией v3 с дефектными
# подписями (devtools#172). PR не вмержен и приниматься не должен, а
# выхода из контракта до этого перехода не было ни одного: закрыть — §I3
# fail-closed навсегда, абандонить — §I4 запрещает на терминальном
# статусе, оставить — §I3 возвращает тот же дефектный PR на каждом заходе.

_REPLACED_PR = 408
_REPLACED_BRANCH = "spec/WS-alpha-7-tasks-v3"
_REPLACED_HEAD = "head-of-v3"


class _ReplaceOps(_SupersedeOps):
    """`_SupersedeOps` + поверхность замены: факты, ревью, закрытие, ветка.

    Заменяемый PR отвечает СВОИМИ фактами (`state`/`headRefOid`), а не
    общим шаблоном `_ForgeOps`: correction-PR обязан оставаться вмерженным
    в тех же тестах, где заменяемый открыт, — иначе провенанс §I7 падал
    бы раньше предмета теста и «замена отказала» было бы неотличимо от
    «подписи не нашлось».
    """

    def __init__(
        self,
        *,
        replaced_state="OPEN",
        replaced_head=_REPLACED_HEAD,
        reviews=(),
        threads=False,
        close_ok=True,
        facts_by_pr=None,
        remote_head=_REPLACED_HEAD,
        local_head=_REPLACED_HEAD,
        **kw,
    ):
        # Ветка заменяемой ревизии РЕЗОЛВИТСЯ в её PR — так на живом
        # входе и есть, пока ветка жива. Пустая карта веток описывала бы
        # состояние «ветки v3 нет», на котором цикл §I3 упирается в
        # `pr is None` и проходит мимо заменяемой ревизии сам — предмет
        # проверки исчезал бы вместе с фикстурой.
        kw["branch_prs"] = {
            _REPLACED_BRANCH: _REPLACED_PR, **(kw.get("branch_prs") or {}),
        }
        super().__init__(**kw)
        # Факты ПОНОМЕРНО: заменяемый PR по умолчанию открыт, факты PR
        # новой ревизии задаются тестами окна C. Общий шаблон `_ForgeOps`
        # (вмержен, подпись полна) остаётся дефолтом для остальных —
        # correction-PR обязан быть вмерженным в тех же тестах.
        self.facts_by_pr = {
            _REPLACED_PR: {
                "state": replaced_state, "headRefOid": replaced_head,
            },
            **(facts_by_pr or {}),
        }
        self.replaced_head = replaced_head
        # None остаётся None: им тесты проверяют «список ревью не получен».
        self.reviews = None if reviews is None else list(reviews)
        self.threads = threads
        self.close_ok = close_ok
        self.closed: list[tuple[int, str]] = []
        self.deleted: list[str] = []
        self.deleted_local: list[str] = []
        # Ветка отозванной ревизии ЖИВА и стоит на записанном head — так
        # на живом входе и есть до шага удаления. `None` моделирует
        # «ссылки уже нет», другой SHA — «под тем же именем чужая работа».
        self.remote_head = remote_head
        self.local_head = local_head

    def pr_facts(self, repo_slug, pr):
        if pr not in self.facts_by_pr:
            return super().pr_facts(repo_slug, pr)
        self.calls.append(("pr_facts", pr))
        return dict(self.facts_by_pr[pr])

    def pr_reviews(self, repo_slug, pr):
        self.calls.append(("pr_reviews", pr))
        return None if self.reviews is None else list(self.reviews)

    def unresolved_threads(self, repo_slug, pr):
        self.calls.append(("unresolved_threads", pr))
        return self.threads

    def close_pr(self, repo_slug, pr, comment):
        self.calls.append(("close_pr", pr))
        if self.close_ok:
            self.closed.append((pr, comment))
        return self.close_ok

    def remote_branch_head(self, repo_slug, branch):
        self.calls.append(("remote_branch_head", branch))
        return self.remote_head if branch == _REPLACED_BRANCH else None

    def rev_parse(self, target_dir, ref):
        if ref == _REPLACED_BRANCH:
            self.calls.append(("rev_parse", ref))
            return self.local_head
        return super().rev_parse(target_dir, ref)

    def delete_remote_branch(self, repo_slug, branch):
        self.calls.append(("delete_remote_branch", branch))
        self.deleted.append(branch)
        return True

    def delete_local_branch(self, target_dir, branch):
        self.calls.append(("delete_local_branch", branch))
        self.deleted_local.append(branch)
        return True


def _replace_state(tmp_path, monkeypatch, **rev3):
    """Прогон перед заменой: v1 вмержена, v3 доставила дефектный PR #408.

    `content_anchor` обеих записей РАВЕН текущему — это боевой вход
    живого случая: дефект был в ПОДПИСИ штампа, а `content_anchor`
    подписи не видит (§I2), значит апстрим «не менялся». На таком входе
    §I5 сработал бы первым и сделал замену недостижимой.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    content = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "АНКЕР-v1",
        "content_anchor": content,
    }
    state.ops["tasks-deliver-v3"] = {
        "status": "completed", "revision": 3, "pr": _REPLACED_PR,
        "branch": _REPLACED_BRANCH, "base_sha": "base-sha-1",
        "head_sha": _REPLACED_HEAD, "anchor": "АНКЕР-v3",
        "prospective_anchor": "АНКЕР-v3", "content_anchor": content,
        "tasks_version": 2,
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG],
        "supersedes": 1,
        **rev3,
    }
    rs.save(state)
    return state


def _replace(revision=3, reason="дефектные подписи штампа (devtools#172)"):
    from governance import task_bridge as tb

    return tb.Replacement(revision, reason)


def test_replace_delivers_v4_closes_pr_and_drops_branch(
    tmp_path, monkeypatch
):
    """Успешная замена целиком: три поля, закрытый #408, удалённая ветка.

    `supersedes: 1`, а не 3 — заменяемая ревизия НЕ доставленная
    спецификация: её PR закрывается без мержа. Две связи независимы
    (требование владельца): `supersedes` — что переиздаём, `replaces_*` —
    какое предложение снимаем со стола.
    """
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps()

    result = tb.deliver_superseded(state, ops, replace=_replace())

    assert result == tb.SupersedeResult("delivered", 77)
    saved = rs.load("r-recon").ops["tasks-deliver-v4"]
    assert saved["status"] == "completed"
    assert saved["replaces_revision"] == 3
    assert saved["replaces_pr"] == _REPLACED_PR
    assert saved["replacement_reason"] == (
        "дефектные подписи штампа (devtools#172)"
    )
    assert saved["supersedes"] == 1
    assert [pr for pr, _ in ops.closed] == [_REPLACED_PR]
    # Ссылка снимается в ОБЕИХ половинах — origin и клон.
    assert ops.deleted == [_REPLACED_BRANCH]
    assert ops.deleted_local == [_REPLACED_BRANCH]
    # Ревизия 3 неприкосновенна (§I4): связь направлена вперёд.
    assert rs.load("r-recon").ops["tasks-deliver-v3"] == state.ops[
        "tasks-deliver-v3"
    ]


def test_replace_closes_pr_before_creating_the_new_one(tmp_path, monkeypatch):
    """Порядок владельца: намерение → закрытие #408 → доставка → ветка.

    Обратный порядок оставил бы на одну спеку два открытых PR, а удаление
    ветки раньше создания нового PR — ошибочный артефакт без ветки при
    недоведённой замене."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps()

    tb.deliver_superseded(state, ops, replace=_replace())

    names = [c[0] for c in ops.calls]
    assert names.index("close_pr") < names.index("create_draft_pr")
    assert names.index("create_draft_pr") < names.index(
        "delete_remote_branch"
    )


def test_replace_is_the_only_exit_from_the_defective_pr(
    tmp_path, monkeypatch
):
    """Оба тупика контракта на одном входе — и выход из обоих.

    Тупик §I3 («оставить»): голый `--supersede` возвращает ТОТ ЖЕ #408 на
    каждом заходе. Тупик §I5: не дойди дело до §I3 (ветка v3 удалена,
    PR по ней не резолвится) — бесследный no-op, потому что апстрим не
    менялся: дефект был в ПОДПИСИ, а `content_anchor` подписи не видит
    (§I2). Явная замена проходит оба — на том же самом входе."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    assert tb.deliver_superseded(
        state, _ReplaceOps()
    ) == tb.SupersedeResult("returned", _REPLACED_PR)

    assert tb.deliver_superseded(
        state, _ReplaceOps(branch_prs={
            _REPLACED_BRANCH: None,
        })
    ) == tb.SupersedeResult("noop")

    assert tb.deliver_superseded(
        state, _ReplaceOps(), replace=_replace()
    ).kind == "delivered"


def test_replace_refuses_when_head_diverged(tmp_path, monkeypatch):
    """head #408 разошёлся с намерением — под именем ветки чужая работа."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(replaced_head="ЧУЖОЙ-HEAD")
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()

    with pytest.raises(RuntimeError, match="вмешались снаружи"):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert ops.closed == []
    # Валидация стоит ДО единого эффекта: леджер не тронут.
    assert (rs.run_dir("r-recon") / "run.json").read_bytes() == before


def test_replace_refuses_on_human_review(tmp_path, monkeypatch):
    """Ревью не от ревью-контура — человек высказался, закрывать нельзя."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[{"login": "andrei-shtanakov", "state": "COMMENTED"}],
    )

    with pytest.raises(RuntimeError, match="andrei-shtanakov"):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert ops.closed == []


def test_replace_refuses_on_review_from_the_review_contour_too(
    tmp_path, monkeypatch
):
    """Ревью ai-prosto блокирует замену наравне с человеческим.

    Решение владельца 2026-09-09: review — уже созданный аудитный
    артефакт, и агентская учётка не делает его находки одноразовыми.
    Закрыть PR автоматически вместе с разбором опаснее, чем один раз
    потребовать явного закрытия от оператора. Привилегий по логину нет
    ВООБЩЕ: `REVIEW_LOGIN` отвечает на другой вопрос — чьим именем мы
    действуем (закрытие PR, удаление веток), а не чьё ревью не замечаем."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[{"login": "ai-prosto", "state": "CHANGES_REQUESTED"}],
    )

    with pytest.raises(RuntimeError, match="ai-prosto"):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert ops.closed == []


def test_replace_review_block_ignores_review_login_env(
    tmp_path, monkeypatch
):
    """`REVIEW_LOGIN` на блокировку не влияет — две роли одной учётки.

    Пока привилегия читалась из него, смена учётки ревью-контура молча
    меняла бы, чьё ревью разрешено закрыть механикой. Теперь этот вопрос
    решает только allowlist, а `REVIEW_LOGIN` остаётся про действующее
    лицо."""
    from governance import task_bridge as tb

    monkeypatch.setenv("REVIEW_LOGIN", "other-bot")
    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[{"login": "other-bot", "state": "APPROVED"}],
    )

    with pytest.raises(RuntimeError, match="other-bot"):
        tb.deliver_superseded(state, ops, replace=_replace())


def test_review_allowlist_is_empty_by_default(monkeypatch):
    """Пустой дефолт залочен отдельно: расширить его незаметно нельзя.

    Это единственное место, где привилегия вообще может появиться, и
    появляться она обязана только явной внешней настройкой — не
    константой в коде и не побочным эффектом другой переменной."""
    from governance import task_bridge as tb

    monkeypatch.delenv(tb._REVIEW_ALLOWLIST_ENV, raising=False)
    assert tb._review_allowlist() == frozenset()
    # Соседние переменные слоя allowlist НЕ наполняют.
    monkeypatch.setenv("REVIEW_LOGIN", "ai-prosto")
    assert tb._review_allowlist() == frozenset()
    # Пустые и пробельные элементы отбрасываются: `",, "` — тоже пусто.
    monkeypatch.setenv(tb._REVIEW_ALLOWLIST_ENV, ",,  ,")
    assert tb._review_allowlist() == frozenset()


def test_explicit_allowlist_unblocks_only_the_named_login(
    tmp_path, monkeypatch
):
    """Явная настройка — рабочая точка, а не заглушка на будущее."""
    from governance import task_bridge as tb

    monkeypatch.setenv(tb._REVIEW_ALLOWLIST_ENV, "ai-prosto, other-bot")
    state = _replace_state(tmp_path, monkeypatch)

    assert tb.deliver_superseded(
        state,
        _ReplaceOps(
            reviews=[{"login": "ai-prosto", "state": "APPROVED"},
                     {"login": "other-bot", "state": "COMMENTED"}],
        ),
        replace=_replace(),
    ).kind == "delivered"


def test_pending_review_is_a_draft_not_a_verdict(tmp_path, monkeypatch):
    """`PENDING` — черновик, автором ещё НЕ отправленный.

    Владелец говорит про SUBMITTED review; неотправленный черновик
    вердикта не несёт и удерживать предложение не может."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[{"login": "andrei-shtanakov", "state": "PENDING"}],
    )

    assert tb.deliver_superseded(
        state, ops, replace=_replace()
    ).kind == "delivered"


def test_red_checks_and_plain_comments_do_not_block_replacement(
    tmp_path, monkeypatch
):
    """Красный CI и обычные комментарии замену НЕ удерживают.

    Граница проведена по сущностям форджи, а не по автору: блокирующие
    сигналы спрашиваются РОВНО два — `pr_reviews` (эндпоинт
    `pulls/<n>/reviews`, только review) и `unresolved_threads`
    (`reviewThreads`, inline-треды). `statusCheckRollup` не
    спрашивается вовсе, а примитива чтения issue-комментариев в слое нет —
    структурно попасть в решение им нечем."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[], threads=False,
        facts_by_pr={_REPLACED_PR: {
            "state": "OPEN", "headRefOid": _REPLACED_HEAD,
            "statusCheckRollup": [{"conclusion": "FAILURE"}],
            "mergeStateStatus": "DIRTY",
        }},
    )

    assert tb.deliver_superseded(
        state, ops, replace=_replace()
    ).kind == "delivered"
    # Сигналов вмешательства спрошено РОВНО два вида (каждый дважды:
    # валидация и повторная сверка перед закрытием — окно между ними
    # реально). Ни `statusCheckRollup`, ни комментарии в их числе нет.
    asked = {c[0] for c in ops.calls}
    assert asked & {"pr_reviews", "unresolved_threads"} == {
        "pr_reviews", "unresolved_threads",
    }
    # Позитивный двойник к `("pr_reviews", _REPLACED_PR) not in ops.calls`
    # соседнего теста: он утверждает ОТСУТСТВИЕ кортежа, и без пары
    # «а бывает ли такой вообще» был бы вакуумным (урок ревью PR #174 —
    # сравнение с кортежем, которого не бывает, всегда истинно).
    assert ("pr_reviews", _REPLACED_PR) in ops.calls
    # Красный `statusCheckRollup` лежал в фактах и на исход не повлиял;
    # комментариев PR слой не читает вовсе — единственный примитив с
    # ними (`comment`) пишущий, и он не звался.
    assert not any(c[0] == "comment" for c in ops.calls)


@pytest.mark.parametrize(
    "kw, match",
    [
        ({"threads": True}, "непогашенные review threads"),
        ({"threads": None}, "непогашенные review threads"),
        ({"reviews": None}, "список ревью не получен"),
    ],
    ids=["open-thread", "threads-unknown", "reviews-unknown"],
)
def test_replace_fails_closed_on_threads_and_unknowns(
    kw, match, tmp_path, monkeypatch
):
    """Непогашенный тред и любое «узнать не удалось» — fail-closed.

    `None` от `unresolved_threads`/`pr_reviews` значит «сигнал не
    получен»; прочитать его как «вмешательства нет» значило бы закрывать
    чужое предложение на неизвестном состоянии."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(**kw)

    with pytest.raises(RuntimeError, match=match):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert ops.closed == []


def test_replace_refuses_merged_pr_unconditionally(tmp_path, monkeypatch):
    """MERGED заменять запрещено безусловно — оно уже часть base.

    Ни ревью, ни треды при этом не спрашиваются: отказ не зависит ни от
    чего, что могло бы его снять."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(replaced_state="MERGED")

    with pytest.raises(RuntimeError, match="вмержен"):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert ops.closed == []
    assert ("pr_reviews", _REPLACED_PR) not in ops.calls


def test_replace_accepts_pr_closed_by_operator(tmp_path, monkeypatch):
    """Уже закрытый #408 — ручной выход оператора из отказа, не тупик.

    Владелец назвал его прямо: при внешнем вмешательстве оператор
    закрывает PR сам, с объяснением, и повторяет явный переход. Тогда шаг
    закрытия — no-op, а не отказ."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(replaced_state="CLOSED")

    assert tb.deliver_superseded(
        state, ops, replace=_replace()
    ).kind == "delivered"
    assert ops.closed == []
    assert ops.deleted == [_REPLACED_BRANCH]
    # Раньше тест утверждал только это удаление — и оно было БЕЗУСЛОВНЫМ.
    # На этом пути (`pr_state != "OPEN"`) сверка идентичности не
    # выполнялась ни разу за прогон: `_check_replacement_target` выходит
    # до неё. Значит шаг 5 обязан сверить head сам.
    assert ("remote_branch_head", _REPLACED_BRANCH) in ops.calls
    assert ("rev_parse", _REPLACED_BRANCH) in ops.calls


def test_replace_refuses_v1_and_unfinished_and_missing(
    tmp_path, monkeypatch
):
    """Что заменять НЕЛЬЗЯ: v1, незавершённую ревизию, отсутствующую."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver-v2"] = {"status": "started", "revision": 2}
    rs.save(state)
    ops = _ReplaceOps()

    with pytest.raises(RuntimeError, match="N ≥ 2"):
        tb.deliver_superseded(state, ops, replace=_replace(revision=1))
    with pytest.raises(RuntimeError, match="--abandon-revision 2"):
        tb.deliver_superseded(state, ops, replace=_replace(revision=2))
    with pytest.raises(RuntimeError, match="нет в леджере"):
        tb.deliver_superseded(state, ops, replace=_replace(revision=9))

    state.ops["tasks-deliver-v2"] = {
        "status": "abandoned", "revision": 2, "reason": "base сдвинулся",
    }
    rs.save(state)
    with pytest.raises(RuntimeError, match="брошена"):
        tb.deliver_superseded(state, ops, replace=_replace(revision=2))


def test_replace_refuses_when_close_fails(tmp_path, monkeypatch):
    """Закрытие не удалось — доставки нет: иначе второй открытый PR."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(close_ok=False)

    with pytest.raises(RuntimeError, match="не удалось закрыть"):
        tb.deliver_superseded(state, ops, replace=_replace())

    assert not any(c[0] == "create_draft_pr" for c in ops.calls)
    # Намерение уже durable (write-ahead §I4) — повтор продолжит ЕГО.
    assert rs.load("r-recon").ops["tasks-deliver-v4"]["status"] == "started"


def _abandoned_replacement(replaces_pr=_REPLACED_PR) -> dict:
    """Ревизия-замена, брошенная после закрытия #408.

    Так ловушка §I3 и достижима: цикл реконсиляции проходит МИМО
    ревизии только когда она `abandoned` (или заменена), поэтому до v3
    заход доходит именно через брошенную замену — а завершённая замена
    заслоняет её сама. Сценарий не выдуманный: окно B (PR закрыт,
    доставка не дошла) плюс сдвинувшийся base дают ровно `abandoned`.
    """
    return {
        "status": "abandoned", "revision": 4,
        "reason": "base сдвинулся, открытого PR нет",
        "replaces_revision": 3, "replaces_pr": replaces_pr,
        "replaces_branch": _REPLACED_BRANCH,
        "replaces_head_sha": _REPLACED_HEAD,
        "replacement_reason": "дефектные подписи",
    }


def test_i3_does_not_fail_closed_on_replaced_revision(tmp_path, monkeypatch):
    """§I3 после закрытия #408: forward-ссылка, а не мутация записи v3.

    Без §I10 этот заход ловил бы v3 проверкой «закрыт без мержа → ветка
    отклонена человеком» и отказывал НАВСЕГДА: замена была бы
    одноразовой — закрыв #408 своей же механикой, переиздание запирало бы
    воркстрим."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    # v1 доставила ДРУГОЕ содержание: иначе вызов кончился бы бесследным
    # no-op §I5 и о §I3 не сказал бы ничего.
    state.ops["tasks-deliver"] = {
        **state.ops["tasks-deliver"], "content_anchor": "СОДЕРЖАНИЕ-ДО",
    }
    state.ops["tasks-deliver-v4"] = _abandoned_replacement()
    rs.save(state)
    ops = _ReplaceOps(
        replaced_state="CLOSED",
        branch_prs={_REPLACED_BRANCH: _REPLACED_PR},
    )

    assert tb.deliver_superseded(state, ops).kind == "delivered"
    saved = rs.load("r-recon").ops
    # v3 заменена ⇒ доставкой не считается: переиздаём v1.
    assert saved["tasks-deliver-v5"]["supersedes"] == 1
    # Запись v3 не тронута: связь живёт ВПЕРЁД, в записи замены.
    assert saved["tasks-deliver-v3"] == state.ops["tasks-deliver-v3"]


def test_i3_still_fails_closed_on_closed_pr_without_forward_link(
    tmp_path, monkeypatch
):
    """Тот же закрытый PR БЕЗ forward-ссылки — по-прежнему fail-closed.

    Исключение §I10 узкое: ожидаемым закрытие объявляет только запись,
    назвавшая ЭТУ ревизию и ЭТОТ номер PR. Иначе оно снимало бы правило
    «ветка отклонена человеком» со всего контракта."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        replaced_state="CLOSED",
        branch_prs={_REPLACED_BRANCH: _REPLACED_PR},
    )

    with pytest.raises(RuntimeError, match="закрыт без мержа"):
        tb.deliver_superseded(state, ops)


def test_i3_forward_link_requires_matching_pr_number(tmp_path, monkeypatch):
    """Forward-ссылка сверяет ПАРУ (ревизия, номер PR), не одну ревизию.

    Под именем ветки заменённой ревизии мог позже завестись ДРУГОЙ PR —
    его закрытие ожидаемым не объявлял никто."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver-v4"] = _abandoned_replacement(replaces_pr=999)
    rs.save(state)
    ops = _ReplaceOps(
        replaced_state="CLOSED",
        branch_prs={_REPLACED_BRANCH: _REPLACED_PR},
    )

    with pytest.raises(RuntimeError, match="закрыт без мержа"):
        tb.deliver_superseded(state, ops)


def test_supersedes_skips_replaced_revision_on_later_reissue(
    tmp_path, monkeypatch
):
    """Заменённая ревизия не считается доставкой и в СЛЕДУЮЩИХ заходах.

    Иначе `supersedes`/§I5/§I8 сверялись бы с содержанием, которого в base
    нет: её PR закрыт без мержа."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver-v4"] = {
        "status": "completed", "revision": 4, "pr": 77,
        "replaces_revision": 3, "replaces_pr": _REPLACED_PR,
    }
    rs.save(state)

    assert tb._last_delivery(rs.load("r-recon")) == (
        4, rs.load("r-recon").ops["tasks-deliver-v4"]
    )
    del state.ops["tasks-deliver-v4"]
    state.ops["tasks-deliver-v4"] = {
        "status": "abandoned", "revision": 4, "reason": "р",
        "replaces_revision": 3, "replaces_pr": _REPLACED_PR,
    }
    rs.save(state)
    # v4 брошена, v3 заменена — предыдущая доставка это v1, а не v3.
    assert tb._last_delivery(rs.load("r-recon"))[0] == 1


def test_forward_link_is_status_agnostic(tmp_path, monkeypatch):
    """Для снятия ловушки достаточно САМОЙ ссылки — статус отзыва неважен.

    Записанное намерение объясняет закрытие независимо от того, дошла ли
    отзывающая ревизия до конца. Смотри проверка только на `completed`,
    окно «отзыв ещё `started`» осталось бы дырой — а это ровно то окно,
    где PR уже закрыт, а новая ревизия ещё не доставлена."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    for status in ("started", "completed", "abandoned"):
        state.ops["tasks-deliver-v4"] = {
            **_abandoned_replacement(), "status": status,
        }
        rs.save(state)
        loaded = rs.load("r-recon")
        assert tb._replaced_by(loaded, 3, _REPLACED_PR) == 4
        # Отозванная ревизия не считается доставкой ни при каком статусе
        # отзыва: её байтов в base нет.
        assert tb._last_delivery(loaded)[0] != 3
        assert tb._reconcile_revision(
            3, loaded.ops["tasks-deliver-v3"], "base-sha-1", _REPLACED_PR,
            {"state": "CLOSED"}, replaced=True,
        ) == "replaced"


def test_replacement_may_repeat_version_and_content_of_the_revoked(
    tmp_path, monkeypatch
):
    """Ни версия, ни содержание отличаться от отозванного НЕ обязаны.

    §I6 считает версию от base, а отозванных байтов в base нет: в него
    попадёт ровно одно из двух предложений, и монотонность не нарушится.
    §I5 при замене неприменим, поэтому равный `content_anchor` доставку
    не останавливает — причина замены может лежать вовсе вне байтов
    (дефект инструмента, ложная подпись), и называет её оператор."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    revoked = dict(state.ops["tasks-deliver-v3"])
    ops = _ReplaceOps()

    assert tb.deliver_superseded(
        state, ops, replace=_replace()
    ).kind == "delivered"

    saved = rs.load("r-recon").ops["tasks-deliver-v4"]
    assert saved["tasks_version"] == revoked["tasks_version"] == 2
    assert saved["content_anchor"] == revoked["content_anchor"]


# --- Крэш-окна замены: повтор продолжает ТУ ЖЕ ревизию --------------------


def _v4_intent(state, **kw) -> dict:
    """Намерение ревизии 4 в статусе `started` — общий вход трёх окон.

    `prospective_anchor` считается ПО-НАСТОЯЩЕМУ (подписью correction-PR
    403, теми же узлами): гвард §I2 сверяет фактический штамп повтора
    именно с ним, и синтетическая строка отказывала бы раньше предмета
    теста.
    """
    from governance import task_bridge as tb

    return {
        "status": "started", "revision": 4,
        "branch": "spec/WS-alpha-7-tasks-v4", "base_sha": "base-sha-1",
        "head_sha": None, "tasks_blob": None,
        "prospective_anchor": tb._prospective_anchor(
            state.target_dir, state.bundle_dir, None,
        ),
        "content_anchor": tb._content_anchor(
            state.target_dir, state.bundle_dir, None
        ),
        "tasks_version": 2,
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG],
        "dag_source": "derived_from_spec", "supersedes": 1,
        "expected_generated_at": "2026-09-09T10:00:00+00:00",
        "replaces_revision": 3, "replaces_pr": _REPLACED_PR,
        "replaces_branch": _REPLACED_BRANCH,
        "replaces_head_sha": _REPLACED_HEAD,
        "replacement_reason": "дефектные подписи",
        **kw,
    }


def _window_state(tmp_path, monkeypatch, **intent):
    from governance import run_state as rs

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver-v4"] = _v4_intent(state, **intent)
    rs.save(state)
    return state


def test_window_a_intent_written_pr_still_open(tmp_path, monkeypatch):
    """Окно A: намерение v4 durable, #408 ещё открыт.

    Повтор обязан довести ШАГ ЗАКРЫТИЯ из намерения — он читается из
    леджера, а не из аргументов, поэтому доводит его и голый
    `--supersede`."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch)
    ops = _ReplaceOps()

    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    assert [pr for pr, _ in ops.closed] == [_REPLACED_PR]
    assert ops.deleted == [_REPLACED_BRANCH]
    saved = rs.load("r-recon").ops
    assert saved["tasks-deliver-v4"]["status"] == "completed"
    assert "tasks-deliver-v5" not in saved


def test_window_b_pr_closed_no_pr_anywhere(tmp_path, monkeypatch):
    """Окно B: #408 закрыт, PR v4 ещё нет — открытых PR в воркстриме НОЛЬ.

    Реконсиляция обязана узнать это состояние и продолжить ТУ ЖЕ v4:
    заменённая ревизия из разбора выбывает по forward-ссылке, а не
    отказывает fail-closed'ом."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(replaced_state="CLOSED")

    assert tb.deliver_superseded(state, ops).kind == "delivered"
    assert ops.closed == []          # шаг состоялся раньше — no-op
    assert ops.deleted == [_REPLACED_BRANCH]
    saved = rs.load("r-recon").ops
    assert saved["tasks-deliver-v4"]["pr"] == 77
    assert "tasks-deliver-v5" not in saved


def test_window_c_pr_created_branch_alive(tmp_path, monkeypatch):
    """Окно C: PR v4 создан, ветка v3 жива — повтор доводит только хвост.

    Доставка не переигрывается (PR уже есть), но шаг удаления ветки
    обязан довестись: иначе он не довёлся бы никогда — этот исход
    терминален для вызова."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch, head_sha="commit-sha-1")
    ops = _ReplaceOps(
        replaced_state="CLOSED",
        branch_prs={"spec/WS-alpha-7-tasks-v4": 77},
        facts_by_pr={77: {"state": "OPEN", "headRefOid": "commit-sha-1"}},
    )

    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 77
    )
    assert ops.deleted == [_REPLACED_BRANCH]
    # ГЛАВНОЕ утверждение окна C: доставка не переигрывается. Сравнение с
    # кортежем было вакуумным — `deliver` передаёт label "", четвёртого
    # элемента "spec" не бывает ни при каком поведении, и `not in` был
    # истинным всегда.
    assert not any(c[0] == "create_draft_pr" for c in ops.calls)
    saved = rs.load("r-recon").ops
    assert saved["tasks-deliver-v4"]["status"] == "completed"
    assert "tasks-deliver-v5" not in saved


def test_window_c_completed_revision_still_drops_branch(
    tmp_path, monkeypatch
):
    """То же окно, но ревизия уже `completed`: хвост доводится и тогда.

    Падение между `_complete_revision` и удалением ветки оставляет
    исход §I3 «completed | OPEN → вернуть существующий PR» — и хвост
    замены обязан доводиться на НЁМ тоже."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _window_state(
        tmp_path, monkeypatch, status="completed", pr=77,
        head_sha="commit-sha-1",
    )
    ops = _ReplaceOps(
        replaced_state="CLOSED",
        branch_prs={"spec/WS-alpha-7-tasks-v4": 77},
        facts_by_pr={77: {"state": "OPEN", "headRefOid": "commit-sha-1"}},
    )

    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "returned", 77
    )
    assert ops.deleted == [_REPLACED_BRANCH]
    assert "tasks-deliver-v5" not in rs.load("r-recon").ops


def test_replace_refuses_when_a_later_revision_is_not_this_replacement(
    tmp_path, monkeypatch
):
    """Просят заменить 3, а поверх неё лежит ревизия, замену не ведущая.

    Замена середины истории породила бы ДВЕ конкурирующие цепочки
    forward-ссылок, и §I3 перестал бы однозначно отвечать, чьё закрытие
    ожидаемо. Повтор той же замены при этом проходит: ревизии, ведущие
    ЭТУ замену, исключением и являются."""
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch, replaces_revision=None)
    ops = _ReplaceOps()

    with pytest.raises(RuntimeError, match="не последняя в леджере"):
        tb.deliver_superseded(state, ops, replace=_replace())


def test_repeat_with_the_flag_continues_the_same_revision(
    tmp_path, monkeypatch
):
    """Повтор с тем же флагом продолжает ревизию замены, а не заводит v5.

    Гвард «замена только последней ревизии» не смеет ловить собственный
    повтор: ревизии, ведущие ЭТУ замену, из него исключены."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch)

    assert tb.deliver_superseded(
        state, _ReplaceOps(), replace=_replace()
    ) == tb.SupersedeResult("delivered", 77)
    saved = rs.load("r-recon").ops
    assert saved["tasks-deliver-v4"]["status"] == "completed"
    assert "tasks-deliver-v5" not in saved


def test_replace_close_revalidates_between_windows(tmp_path, monkeypatch):
    """Между намерением и закрытием человек успевает вмешаться.

    Шаг закрытия сверяет идентичность и ревью САМ, а не полагается на
    валидацию первого захода: между ними стоят сетевые шаги §I7/§I8, и
    окно реально."""
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(
        reviews=[{"login": "andrei-shtanakov", "state": "APPROVED"}],
    )

    with pytest.raises(RuntimeError, match="andrei-shtanakov"):
        tb.deliver_superseded(state, ops)

    assert ops.closed == []


def test_abandoned_replacement_hands_its_obligation_to_the_next_revision(
    tmp_path, monkeypatch
):
    """Брошенная ревизия-замена передаёт обязательство следующей.

    Замена не выполнена: PR отозван (или ещё ждёт отзыва), ветка v3 жива.
    Потеряй следующая ревизия `replaces_*` — forward-ссылка осталась бы
    только в брошенной записи, а хвост замены не довёлся бы никогда:
    ловушка §I3 вернулась бы вместе с живой веткой отозванных байтов."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        **state.ops["tasks-deliver"], "content_anchor": "СОДЕРЖАНИЕ-ДО",
    }
    state.ops["tasks-deliver-v4"] = _abandoned_replacement()
    rs.save(state)
    ops = _ReplaceOps(replaced_state="CLOSED")

    assert tb.deliver_superseded(state, ops).kind == "delivered"

    saved = rs.load("r-recon").ops["tasks-deliver-v5"]
    assert saved["replaces_revision"] == 3
    assert saved["replaces_pr"] == _REPLACED_PR
    assert saved["replacement_reason"] == "дефектные подписи"
    assert ops.deleted == [_REPLACED_BRANCH]


def test_obligation_survives_abandon_inside_the_same_call(
    tmp_path, monkeypatch
):
    """Ревизия-замена брошена ПРЯМО В ЭТОМ вызове — обязательство живо.

    Соседний тест про перенос пред-засевает `abandoned` и потому этот
    путь не исполняет ни разу. А цикл реконсиляции сам переводит
    ревизию-замену в `abandoned` (`abandon_and_next` на сдвинувшемся
    base), и до цикла её статус ещё `started`. Посчитай перенос раньше
    цикла — новая ревизия ушла бы без `replaces_*`, закрывать было бы
    нечего, и отозванный #408 остался бы ОТКРЫТЫМ вторым на ту же спеку.

    Хуже того, на живом входе (дефект в подписи, апстрим не менялся) до
    новой ревизии не дошло бы вовсе: §I5 вернул бы бесследный no-op с
    RC 0, и обязательство замены испарилось бы молча."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    # base ревизии-замены сдвинулся ⇒ цикл её бросит: `started`, PR по её
    # ветке нет, base не тот.
    state = _window_state(tmp_path, monkeypatch, base_sha="base-sha-0")
    ops = _ReplaceOps()

    assert tb.deliver_superseded(state, ops).kind == "delivered"

    saved = rs.load("r-recon").ops
    assert saved["tasks-deliver-v4"]["status"] == "abandoned"
    assert saved["tasks-deliver-v5"]["replaces_revision"] == 3
    assert saved["tasks-deliver-v5"]["replaces_pr"] == _REPLACED_PR
    assert saved["tasks-deliver-v5"]["replacement_reason"] == (
        "дефектные подписи"
    )
    # Обязательство не просто записано — оно ИСПОЛНЕНО.
    assert [pr for pr, _ in ops.closed] == [_REPLACED_PR]
    assert ops.deleted == [_REPLACED_BRANCH]


def test_revoked_revision_with_open_pr_is_never_returned(
    tmp_path, monkeypatch
):
    """Отозванная ревизия при OPEN выбывает из разбора, а не возвращается.

    Иначе механика печатает «ревизия 3 уже доставлена — PR #408» при
    RC 0 и вручает оператору то самое незамерженное дефектное
    предложение, которое леджер объявил снятым: читая последнюю строку,
    он идёт мержить ложные подписи — ровно то, ради запрета чего переход
    и заведён."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        **state.ops["tasks-deliver"], "content_anchor": "СОДЕРЖАНИЕ-ДО",
    }
    state.ops["tasks-deliver-v4"] = _abandoned_replacement()
    rs.save(state)
    # #408 ещё ОТКРЫТ: шаг закрытия не успел выполниться до броска.
    ops = _ReplaceOps(replaced_state="OPEN")

    result = tb.deliver_superseded(state, ops)

    assert result != tb.SupersedeResult("returned", _REPLACED_PR)
    assert result.kind == "delivered"
    # Обязательство доведено: PR закрыт этим же вызовом, ветка снята.
    assert [pr for pr, _ in ops.closed] == [_REPLACED_PR]
    assert ops.deleted == [_REPLACED_BRANCH]


def test_reconcile_replaced_covers_open_but_not_merged(tmp_path, monkeypatch):
    """Таблица §I3 при `replaced`: OPEN и CLOSED выбывают, MERGED — нет.

    MERGED значит, что отозванное предложение всё-таки вмержено, то есть
    отзыв противоречит факту. Пропустить его молча было бы хуже, чем
    разобрать обычной таблицей: дальше по ходу отказ приходит
    безусловным «вмерженное не заменяется»."""
    from governance import task_bridge as tb

    op = {"status": "completed", "branch": _REPLACED_BRANCH,
          "base_sha": "base-sha-1", "head_sha": _REPLACED_HEAD,
          "pr": _REPLACED_PR}

    for state_name in ("OPEN", "CLOSED"):
        assert tb._reconcile_revision(
            3, op, "base-sha-1", _REPLACED_PR,
            {"state": state_name, "headRefOid": _REPLACED_HEAD},
            replaced=True,
        ) == "replaced"
    assert tb._reconcile_revision(
        3, op, "base-sha-1", _REPLACED_PR,
        {"state": "MERGED", "headRefOid": _REPLACED_HEAD}, replaced=True,
    ) == "return_pr"


def test_obligation_is_discharged_when_revoked_pr_gets_merged(
    tmp_path, monkeypatch
):
    """Перенятое обязательство + ВМЕРЖЕННЫЙ отозванный PR = разрядка.

    Сочетание, которого не строил ни один тест: MERGED и перенос были
    покрыты порознь, поэтому тупик зеленел. А тупик полный: человек
    мержит #408, пока замена не доведена; `_replacement_close` упирается
    в безусловный запрет «вмерженное не отзывается» и падает RC 1;
    `--abandon-revision` обязательство не снимает, а переносит на
    следующую ревизию — та падает так же. Каждый запуск тратит ревизию
    леджера и падает; бесследный no-op §I5 выключен непустым
    `replace_fields`. Воркстрим не переиздать больше ничем.

    Разрядка: отзыв адресован ПРЕДЛОЖЕНИЮ, а вмерженных предложений не
    бывает — байты в base. Дальше идёт обычное переиздание, и
    `supersedes` указывает на эту самую ревизию: её доставка
    состоялась."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    # v3 доставила ДРУГОЕ содержание: иначе §I5 закончил бы вызов
    # бесследным no-op и о разрядке тест не сказал бы ничего.
    state.ops["tasks-deliver-v3"] = {
        **state.ops["tasks-deliver-v3"], "content_anchor": "СОДЕРЖАНИЕ-v3",
    }
    state.ops["tasks-deliver-v4"] = _abandoned_replacement()
    rs.save(state)
    ops = _ReplaceOps(replaced_state="MERGED")

    assert tb.deliver_superseded(state, ops).kind == "delivered"

    saved = rs.load("r-recon").ops["tasks-deliver-v5"]
    # Обязательство снято: полей отзыва нет, закрывать никто не пробовал.
    assert "replaces_revision" not in saved
    assert "replaces_pr" not in saved
    assert ops.closed == []
    assert ops.deleted == []
    # Вмерженная ревизия снова считается доставкой — переиздаём ЕЁ.
    assert saved["supersedes"] == 3


def test_merged_revoked_pr_mid_flight_names_the_way_out(
    tmp_path, monkeypatch
):
    """Мерж отозванного PR в полёте — отказ ОДИН раз и с процедурой.

    Намерение ревизии уже durable, её anchor'ы и версия посчитаны на
    base без байтов отозванной доставки — продолжать её нельзя.
    Поэтому здесь fail-closed уместен, но обязан назвать выход: свернуть
    повисшую ревизию и переиздать обычным `--supersede`. Без этого
    оператор упирался бы в запрет, не понимая, чем его разомкнуть."""
    from governance import task_bridge as tb

    state = _window_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(replaced_state="MERGED")

    with pytest.raises(RuntimeError, match="--abandon-revision 4"):
        tb.deliver_superseded(state, ops)

    assert ops.closed == []


def test_discharge_costs_nothing_without_revocations_in_the_ledger(
    tmp_path, monkeypatch
):
    """Нет отзывов в леджере — нет и сетевого запроса о них.

    Разрядка читает состояние отозванного PR, то есть ходит в сеть там,
    где раньше читался только леджер. Платить за это обычное переиздание
    (и бесследный no-op §I5) не должно."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _ReplaceOps()

    assert tb._discharged_replacements(state, ops) == frozenset()
    assert not any(c[0] == "pr_facts" for c in ops.calls)

    # А при отзыве в леджере — ровно один запрос на отозванный PR.
    state.ops["tasks-deliver-v4"] = _abandoned_replacement()
    assert tb._discharged_replacements(state, ops) == frozenset()
    assert [c for c in ops.calls if c[0] == "pr_facts"] == [
        ("pr_facts", _REPLACED_PR)
    ]


@pytest.mark.parametrize(
    "kw, deleted, deleted_local",
    [
        ({"remote_head": "ЧУЖОЙ-SHA"}, [], [_REPLACED_BRANCH]),
        ({"local_head": "ЧУЖОЙ-SHA"}, [_REPLACED_BRANCH], []),
        ({"remote_head": "ЧУЖОЙ", "local_head": "ЧУЖОЙ"}, [], []),
        ({"remote_head": None, "local_head": None}, [], []),
    ],
    ids=["origin-diverged", "clone-diverged", "both-diverged", "absent"],
)
def test_branch_is_dropped_only_where_head_matches_the_revoked_revision(
    kw, deleted, deleted_local, tmp_path, monkeypatch
):
    """Шаг 5 сносит ссылку ТОЛЬКО там, где head совпал с отозванным.

    Удаление необратимо, идёт `git branch -D` (force) и в СОСЕДНЕМ репо:
    под тем же именем могли оказаться дописанные после закрытия PR байты
    либо одноимённая ветка оператора в его клоне — они стали бы
    недостижимы. Половины судятся порознь: расхождение в одной не
    отменяет удаления в другой. Отсутствие ссылки — выполненный шаг, а не
    сбой.

    Расхождение — НЕ отказ: доставка к этому моменту состоялась, и RC 1
    сказал бы о ней неправду."""
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    ops = _ReplaceOps(**kw)

    assert tb.deliver_superseded(
        state, ops, replace=_replace()
    ).kind == "delivered"

    assert ops.deleted == deleted
    assert ops.deleted_local == deleted_local


def test_branch_is_kept_when_intent_carries_no_head_of_the_revoked(
    tmp_path, monkeypatch, capsys
):
    """Нет `replaces_head_sha` — сверять нечем, значит не удаляем.

    Запись старого образца (либо перенятое обязательство без поля) не
    даёт ответить, та ли это ветка. Молчаливое удаление «на всякий
    случай» здесь стоит дороже оставленной ветки.

    Утверждается ИМЕННО диагностика, а не только «не удалили»: сравнение
    `actual != head` при пустом `head` и так не совпало бы, поэтому
    ветка уцелела бы и без гварда — его продукт в том, что оператору
    названа настоящая причина, а не выдуманное расхождение SHA."""
    from governance import task_bridge as tb

    state = _window_state(
        tmp_path, monkeypatch, replaces_head_sha=None,
    )
    ops = _ReplaceOps(replaced_state="CLOSED")

    assert tb.deliver_superseded(state, ops).kind == "delivered"

    assert ops.deleted == []
    assert ops.deleted_local == []
    assert "нет replaces_head_sha" in capsys.readouterr().out


# --- CLI replace-перехода -------------------------------------------------


def test_cli_replace_requires_reason_and_supersede(capsys):
    """Причина обязательна, а сам флаг осмыслен только с --supersede.

    Причина уходит и в леджер (`replacement_reason`), и в комментарий
    закрываемого PR — это единственное место, где потом читается, почему
    предложение отозвали."""
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main(["--run-id", "r", "--supersede", "--replace-revision", "3"])
    assert exc.value.code == 2
    assert "--reason" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        tb.main(["--run-id", "r", "--replace-revision", "3",
                 "--reason", "р"])
    assert "--replace-revision" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, needle",
    [
        (["--replace-revision", "3", "--abandon-revision", "2",
          "--reason", "р"],
         "--replace-revision и --abandon-revision"),
        (["--supersede", "--reason", "р"], "--reason осмыслен только"),
    ],
    ids=["with-abandon", "reason-without-transition"],
)
def test_cli_replace_flag_combinations_refuse(argv, needle, capsys):
    """Взаимоисключения — `parser.error`, а не молчаливая победа флага.

    Общий `--reason` на два перехода однозначен ИМЕННО этим гвардом:
    сосуществовать переходы не могут, толковать нечего. Убери его — и
    одна строка легла бы то в `reason`, то в `replacement_reason` по
    порядку проверок диспетчера."""
    from governance import task_bridge as tb

    with pytest.raises(SystemExit) as exc:
        tb.main(["--run-id", "r", *argv])
    assert exc.value.code == 2
    assert needle in capsys.readouterr().err


def test_cli_shared_reason_lands_in_the_field_of_its_transition(
    tmp_path, monkeypatch, capsys
):
    """Один флаг — два перехода, но поля леджера разные и не путаются."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver-v2"] = {"status": "started", "revision": 2}
    rs.save(state)
    monkeypatch.setattr(tb, "RealOps", lambda: object())

    assert tb.main([
        "--run-id", "r-recon", "--abandon-revision", "2",
        "--reason", "причина абандона",
    ]) == 0
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["reason"] == "причина абандона"
    assert "replacement_reason" not in saved


def test_cli_replace_reaches_implementation(tmp_path, monkeypatch, capsys):
    """Флаги замены доходят до реализации НЕДЕФОЛТНЫМИ значениями.

    Обрыв в диспетчере читался бы как обычное переиздание — с бесследным
    no-op §I5 на живом входе, то есть молчаливым «ничего не делаю»."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    called = {}

    def _fake(s, o, legacy_bundle=None, replace=None):
        called["replace"] = replace
        return tb.SupersedeResult("delivered", 77)

    monkeypatch.setattr(tb, "deliver_superseded", _fake)
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main([
        "--run-id", "r-recon", "--supersede",
        "--replace-revision", "3", "--reason", "дефект подписи",
    ]) == 0
    assert called["replace"] == tb.Replacement(3, "дефект подписи")


# --- Перенос состояния исполнения при переиздании (spec-runner#409) --------
#
# Рендер детерминирован по бандлу: без переноса переиздание воркстрима, где
# часть задач уже выполнена, возвращает `✅ DONE` в `TODO`, а `- [x]` — в
# `- [ ]`. Артефакт становится непригоден: state DB помнит успешные задачи,
# файл больше нет, следующий прогон встаёт на `state_spec_mismatch`.
#
# Тексты «доставленных» спек ниже строятся НЕ руками, а тем же
# `render_tasks_dt`, что и настоящая доставка, плюс те же мутации, что
# делает spec-runner (`update_task_status` вставляет эмодзи, `- [ ]` → `- [x]`).


def _sc(beh_id: str, title: str, target: str = "tests/test_x.py"):
    from governance.task_bridge import Scenario

    return Scenario(
        beh_id=beh_id, title=title, traces=("FR-01",),
        checked_kind="integration", checked_target=target,
    )


def _dt(dt_id: str, title: str, scenarios: tuple[str, ...], depends=()):
    from governance.decomposition_guard import DtTask

    return DtTask(
        dt_id=dt_id, title=title, type="implement", owner="dev",
        scenarios=scenarios, depends_on=tuple(depends), delivered_by=(),
        parallel_group="solo",
    )


def _render(dt_tasks, scenarios, version: int = 1) -> str:
    from governance import task_bridge as tb

    return tb.render_tasks_dt(
        ws_id="WS-alpha-7", subject="s",
        bundle_path="workstreams/WS-alpha-7/spec/30-decomposition.md",
        scenarios=scenarios, dt_tasks=dt_tasks,
        generated_at="2026-09-02T06:07:39+03:00",
        anchor_blob="a" * 40, version=version,
    )


def _executed(text: str, task_id: str) -> str:
    """Спека после успешного прогона `task_id` — мутации spec-runner.

    Ровно то, что пишет `update_task_status` (`| ✅ DONE` вместо `| TODO`,
    эмодзи ВСТАВЛЯЕТСЯ) и `mark_all_checklist_done` (`- [ ]` → `- [x]`) в
    границах одной задачи."""
    out, inside = [], False
    for line in text.split("\n"):
        if line.startswith("### TASK-"):
            inside = line.startswith(f"### {task_id}:")
        elif inside:
            line = line.replace("| TODO", "| ✅ DONE")
            line = line.replace("- [ ] ", "- [x] ")
        out.append(line)
    return "\n".join(out)


_CARRY_SCENARIOS = [
    _sc("BEH-01", "Просмотр списка"),
    _sc("BEH-02", "Пустое состояние"),
    _sc("BEH-03", "Ошибка сети", target="tests/test_y.py"),
]
_CARRY_DT = [
    _dt("DT-01", "Реализация", ("BEH-01", "BEH-02")),
    _dt("DT-02", "Обработка ошибок", ("BEH-03",), depends=("DT-01",)),
]


def _task_body(text: str, task_id: str) -> str:
    start = text.index(f"### {task_id}:")
    tail = text.find("### TASK-", start + 1)
    return text[start:] if tail < 0 else text[start:tail]


def test_carry_preserves_status_and_marks_of_unchanged_task() -> None:
    """Неизменённая задача переиздания несёт `✅ DONE` и свои `- [x]`."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001")
    fresh = _render(_CARRY_DT, _CARRY_SCENARIOS, version=2)
    assert "| ✅ DONE" not in fresh          # рендер состояния не знает
    carried = tb._carry_execution_state(fresh, delivered)

    done = _task_body(carried, "TASK-001")
    assert "P2 | ✅ DONE   Est: 0.5d" in done
    assert "- [x] реализовать BEH-01: Просмотр списка" in done
    assert "- [x] реализовать BEH-02: Пустое состояние" in done
    assert "- [ ]" not in done
    # Незапущенная задача остаётся чистой — перенос не красит всё подряд
    todo = _task_body(carried, "TASK-002")
    assert "P2 | TODO   Est: 0.5d" in todo
    assert "- [x]" not in todo
    # Кроме маркеров состояния байты рендера не тронуты
    assert carried.replace("✅ DONE", "TODO").replace(
        "- [x]", "- [ ]"
    ) == fresh


def test_carry_skips_task_whose_body_changed() -> None:
    """Изменённая задача приходит чистой: `DONE` утверждал ТО тело.

    Меняется заголовок DT (титул задачи) — тело расходится, статус не
    переносится. Неизменённые пункты чеклиста галочки при этом сохраняют:
    правила статуса и пунктов независимы."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001")
    changed = [
        _dt("DT-01", "Реализация (переписана)", ("BEH-01", "BEH-02")),
        _CARRY_DT[1],
    ]
    fresh = _render(changed, _CARRY_SCENARIOS, version=2)
    body = _task_body(tb._carry_execution_state(fresh, delivered), "TASK-001")
    assert "P2 | TODO   Est: 0.5d" in body
    assert "| ✅ DONE" not in body
    assert "- [x] реализовать BEH-01: Просмотр списка" in body


def test_carry_skips_checklist_item_whose_text_changed() -> None:
    """Переформулированный пункт начинается заново.

    Титул BEH-02 переписан ⇒ текст его пункта другой ⇒ галочки нет;
    соседний пункт той же задачи её сохраняет."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001")
    scenarios = [
        _CARRY_SCENARIOS[0],
        _sc("BEH-02", "Пустое состояние (уточнено)"),
        _CARRY_SCENARIOS[2],
    ]
    fresh = _render(_CARRY_DT, scenarios, version=2)
    body = _task_body(tb._carry_execution_state(fresh, delivered), "TASK-001")
    assert "- [ ] реализовать BEH-02: Пустое состояние (уточнено)" in body
    assert "- [x] реализовать BEH-01: Просмотр списка" in body


def test_carry_leaves_new_task_clean() -> None:
    """Задача, появившаяся впервые, состояния не наследует.

    Новая задача поставлена ПЕРВОЙ — она занимает номер `TASK-001`,
    который в доставленной спеке принадлежал выполненной DT-01.
    Сопоставление по номеру объявило бы её выполненной, не начав."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001")
    scenarios = [_sc("BEH-00", "Новый сценарий"), *_CARRY_SCENARIOS]
    grown = [_dt("DT-00", "Новая задача", ("BEH-00",)), *_CARRY_DT]
    body = _task_body(
        tb._carry_execution_state(_render(grown, scenarios, version=2),
                                  delivered),
        "TASK-001",
    )
    assert "#DT-00" in body
    assert "P2 | TODO   Est: 0.5d" in body
    assert "| ✅ DONE" not in body
    assert "- [x]" not in body


def test_carry_renumbering_does_not_move_status_to_a_foreign_task() -> None:
    """Сдвиг нумерации не переносит состояние на ЧУЖУЮ задачу.

    Задача вставлена в середину: бывшая TASK-002 (DT-02) стала TASK-003.
    Сопоставление по номеру отдало бы состояние выполненной DT-02 задаче
    DT-99, которая не исполнялась ни секунды. Сопоставление §I11 идёт по
    БАЙТАМ, и номер в них входит, поэтому вставленная задача чистая, а
    сдвинутая теряет статус — «принятый остаток» §I11."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-002")
    scenarios = [
        _CARRY_SCENARIOS[0], _CARRY_SCENARIOS[1],
        _sc("BEH-99", "Вставленная в середину"), _CARRY_SCENARIOS[2],
    ]
    shifted = [
        _CARRY_DT[0],
        _dt("DT-99", "Вставленная", ("BEH-99",), depends=("DT-01",)),
        _dt("DT-02", "Обработка ошибок", ("BEH-03",), depends=("DT-99",)),
    ]
    fresh = _render(shifted, scenarios, version=2)
    carried = tb._carry_execution_state(fresh, delivered)

    inserted = _task_body(carried, "TASK-002")
    assert "#DT-99" in inserted            # именно вставленная задача
    assert "| ✅ DONE" not in inserted
    assert "- [x]" not in inserted
    # У DT-02 сдвинулись номер в заголовке и в `Depends on` — блок не
    # совпал, статус не переносится. Пункт с ПРЕЖНИМ текстом галочку всё
    # же сохраняет: §I11 сверяет пункты отдельно от объемлющей задачи и
    # по всей спеке, так что переезд пункта в другой блок ему не помеха.
    moved = _task_body(carried, "TASK-003")
    assert "#DT-02" in moved
    assert "| ✅ DONE" not in moved
    assert "- [x] реализовать BEH-03: Ошибка сети" in moved


def test_carry_from_spec_without_tasks_changes_nothing() -> None:
    """Доставленной спеки нет / она без задач — переносить нечего."""
    from governance import task_bridge as tb

    fresh = _render(_CARRY_DT, _CARRY_SCENARIOS, version=2)
    assert tb._carry_execution_state(fresh, "---\nx: 1\n---\n\nbody\n") == fresh


def test_carry_ignores_duplicate_block_in_delivered_spec() -> None:
    """Повторившийся блок — неоднозначность ⇒ задача приходит чистой.

    §I11 объявляет единственность кандидата УСЛОВИЕМ переноса, а не
    свойством сегодняшних данных: сегодня номера задач различны, значит
    двух одинаковых блоков не бывает, — но опираться на это контракт не
    вправе, и код обязан отвечать чистым результатом, а не догадкой."""
    from governance import task_bridge as tb

    delivered = _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001")
    # Копия ВСТАВЛЯЕТСЯ на место оригинала, а не дописывается в хвост:
    # у хвостового блока другие завершающие строки, и байты разошлись бы
    block = _task_body(delivered, "TASK-001")
    doubled = delivered.replace(block, block + block, 1)
    fresh = _render(_CARRY_DT, _CARRY_SCENARIOS, version=2)
    body = _task_body(
        tb._carry_execution_state(fresh, doubled), "TASK-001"
    )
    assert "| ✅ DONE" not in body
    assert "- [x]" not in body


def test_carry_normalizes_meta_line_instead_of_dropping_it() -> None:
    """Сверка блока видит приоритет и оценку, а не только статус (§I11).

    Мета-строка НОРМАЛИЗУЕТСЯ, а не вырезается: выбросить её целиком
    значило бы ослепить сравнение к смене `P2`/`Est`, живущих в той же
    строке, — задача с переставленным приоритетом сохраняла бы `DONE`."""
    from governance import task_bridge as tb

    delivered = _executed(
        _render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001"
    ).replace("P2 | ✅ DONE   Est: 0.5d", "P0 | ✅ DONE   Est: 3d")
    fresh = _render(_CARRY_DT, _CARRY_SCENARIOS, version=2)
    body = _task_body(tb._carry_execution_state(fresh, delivered), "TASK-001")
    assert "| ✅ DONE" not in body
    # Пункты чеклиста живут по своему правилу и приоритетом не задеты
    assert "- [x] реализовать BEH-01: Просмотр списка" in body


def _twin_item(text: str) -> str:
    """Пункт BEH-03 переименован в пункт BEH-01 — дубль текста в спеке."""
    return text.replace(
        "реализовать BEH-03: Ошибка сети",
        "реализовать BEH-01: Просмотр списка",
    )


_DUP_ITEM = "- [x] реализовать BEH-01: Просмотр списка"


def test_carry_skips_checklist_text_repeated_in_delivered_spec() -> None:
    """Текст пункта, встречающийся в базовой спеке дважды, не переносит.

    Единственность текста — УСЛОВИЕ переноса на уровне пункта: гарантии
    различия, какую номера задач дают блокам, у чеклистов нет. Дубль
    заведён ОДИНАКОВО в обеих спеках, поэтому блоки совпадают и статусы
    переносятся своим правилом — предмет теста ровно в пунктах."""
    from governance import task_bridge as tb

    delivered = _twin_item(
        _executed(_executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001"),
                  "TASK-002")
    )
    fresh = _twin_item(_render(_CARRY_DT, _CARRY_SCENARIOS, version=2))
    carried = tb._carry_execution_state(fresh, delivered)
    assert _DUP_ITEM not in carried
    assert carried.count("| ✅ DONE") == 2
    # Однозначные пункты тех же задач галочки сохраняют
    assert "- [x] реализовать BEH-02: Пустое состояние" in carried
    assert carried.count("- [x] проверка группы") == 2


def test_carry_skips_checklist_text_repeated_in_fresh_render() -> None:
    """Единственность требуется И В НОВОМ рендере, не только в базовой спеке.

    Дубль заведён только в новом рендере: в базовой спеке текст один и
    отмечен, но перенести его некуда однозначно — обе копии претендуют."""
    from governance import task_bridge as tb

    delivered = _executed(
        _executed(_render(_CARRY_DT, _CARRY_SCENARIOS), "TASK-001"), "TASK-002"
    )
    fresh = _twin_item(_render(_CARRY_DT, _CARRY_SCENARIOS, version=2))
    carried = tb._carry_execution_state(fresh, delivered)
    assert _DUP_ITEM not in carried
    # TASK-001 переименование не задело — её блок совпал, статус перенесён;
    # TASK-002 изменена, поэтому чистая
    assert _task_body(carried, "TASK-001").count("| ✅ DONE") == 1
    assert "| ✅ DONE" not in _task_body(carried, "TASK-002")


# --- Перенос состояния: сквозные пути доставки -----------------------------


def _delivered_v1(state, executed: str = "TASK-001") -> str:
    """Спека v1 фикстуры, отрендеренная тем же кодом и «исполненная».

    Именно она лежит в base на момент переиздания — тела задач те же, что
    даст свежий рендер (`generated_at`/`version` живут во frontmatter)."""
    from governance import decomposition_guard as dg
    from governance import task_bridge as tb

    base = Path(state.target_dir) / state.bundle_dir
    dt_tasks, _ = dg.parse_dt_tasks(
        (base / "30-decomposition.md").read_text(encoding="utf-8")
    )
    return _executed(
        tb.render_tasks_dt(
            ws_id=state.ws_id, subject=state.subject,
            bundle_path=f"{state.bundle_dir}/30-decomposition.md",
            scenarios=tb.parse_behaviour(
                (base / "15-behaviour-spec.md").read_text(encoding="utf-8")
            ),
            dt_tasks=dt_tasks,
            generated_at="2026-09-01T00:00:00+03:00",
            anchor_blob="b" * 40, version=1,
        ),
        executed,
    )


class _CarryOps(_SupersedeOps):
    """`_SupersedeOps`, но спека В BASE — реально доставленная и исполненная.

    Аргументы `show_file` сверяются, как у родителя: перенос обязан
    читать base по `base_sha`, а не HEAD и не рабочее дерево."""

    def __init__(self, *args, delivered: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delivered = delivered

    def show_file(self, target_dir, ref, path):
        self.calls.append(("show_file", ref, path))
        if (ref, path) != ("base-sha-1", _SPEC_REL):
            return None
        return self.delivered


def test_supersede_preserves_execution_state_of_unchanged_tasks(
    tmp_path, monkeypatch
):
    """Живой дефект spec-runner#409: переиздание сохраняет `DONE` и `[x]`.

    До фикса `deliver()` рендерил спеку детерминированно из бандла и о
    предыдущей доставке не знал ничего — переиздание воркстрима, где
    TASK-001 уже выполнена, возвращало её в `TODO` с пустыми чекбоксами,
    и следующий прогон вставал на `state_spec_mismatch`."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _CarryOps(delivered=_delivered_v1(state))
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult(
        "delivered", 77
    )
    text = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert "P2 | ✅ DONE   Est: 0.5d" in text
    assert "- [x] реализовать BEH-01: Просмотр списка" in text
    assert "- [x] реализовать BEH-02: Пустое состояние" in text
    assert "- [ ]" not in text
    # Переиздание всё же состоялось: version вырос, а не «файл прежний»
    assert "version: 2" in text
    # `tasks_blob` §I3.1 посчитан по ФАКТИЧЕСКИМ байтам файла — иначе
    # возобновление не опознало бы собственный коммит
    from governance.stale_adapter import blob_sha1
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["tasks_blob"] == (
        blob_sha1(text)
    )


def test_supersede_carry_source_is_base_not_worktree(tmp_path, monkeypatch):
    """Перенос читает base по `base_sha`, а не рабочее дерево.

    В рабочем дереве лежит ИСПОЛНЕННАЯ спека (после `checkout_and_pull`
    стаба файл остаётся на месте), а base отвечает `None` — переносить
    нечего, и ни один маркер состояния в переиздание не попадает. Иначе
    байты спеки зависели бы от случайного состояния чекаута, а §I3.1
    требует их воспроизводимости."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        _delivered_v1(state), encoding="utf-8"
    )
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "СТАРЫЙ-ДРУГОЙ",
        # dag из записи: `_previous_dag` не пойдёт в show_file, и
        # единственный его вызов останется тот, что делает перенос
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG],
    }
    rs.save(state)
    ops = _CarryOps(delivered="")
    assert tb.deliver_superseded(state, ops).kind == "delivered"
    text = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert "✅" not in text and "- [x]" not in text
    assert ("show_file", "base-sha-1", _SPEC_REL) in ops.calls


class _LoudReconOps(_ReconOps):
    """`_ReconOps`, у которого `show_file` ГРОМКИЙ: он и пишется в calls, и
    отдаёт исполненную спеку.

    Молчаливый `None` родителя делал бы утверждение «обычная доставка в
    base не ходит» невыполнимым в обе стороны: вызов не виден, а его
    результат пуст, — и лишний поход остался бы незамеченным."""

    def __init__(self, delivered: str) -> None:
        super().__init__()
        self.delivered = delivered

    def show_file(self, target_dir, ref, path):
        self.calls.append(("show_file", ref, path))
        return self.delivered


def test_deliver_for_run_carries_nothing(tmp_path, monkeypatch):
    """Обычная доставка состояния не переносит и в base за ним не ходит.

    Спеки в base ещё нет — переносить нечего; новых путей отказа на
    штатном пути появиться не должно."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    ops = _LoudReconOps(_delivered_v1(state))
    # Исполненная спека лежит и в рабочем дереве, и «в base» (show_file):
    # ни один из двух источников не имеет права дотянуться до доставки v1
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        ops.delivered, encoding="utf-8"
    )
    assert tb.deliver_for_run(state, ops) == 77
    text = (
        Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    ).read_text(encoding="utf-8")
    assert "✅" not in text and "- [x]" not in text
    assert not [c for c in ops.calls if c[0] == "show_file"]


def test_supersede_resume_reproduces_the_same_bytes(tmp_path, monkeypatch):
    """Повторный заход по тому же намерению даёт те же байты спеки.

    Возобновление (§I3 «started | PR-а нет») берёт состояние из base
    ЭТОЙ ревизии (`op["base_sha"]`), а не из того, куда уехал апстрим, —
    иначе `tasks_blob` намерения разошёлся бы с фактическим блобом."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    delivered = _delivered_v1(state)
    first = _CarryOps(delivered=delivered)
    assert tb.deliver_superseded(state, first).kind == "delivered"
    spec = Path(state.target_dir) / "spec/WS-alpha-7-tasks.md"
    bytes_first = spec.read_bytes()
    saved = rs.load("r-recon")
    blob_first = saved.ops["tasks-deliver-v2"]["tasks_blob"]

    # Возврат в состояние «намерение durable, PR ещё не создан»
    saved.ops["tasks-deliver-v2"].update(
        status="started", pr=None, head_sha=None, tasks_blob=None,
    )
    rs.save(saved)
    spec.write_text("затёрто\n", encoding="utf-8")
    again = _CarryOps(delivered=delivered)
    assert tb.deliver_superseded(saved, again).kind == "delivered"
    assert spec.read_bytes() == bytes_first
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["tasks_blob"] == (
        blob_first
    )
    assert ("show_file", "base-sha-1", _SPEC_REL) in again.calls


# --- §I12: `--approve-node` — одобрение узла есть человеческий акт ---------
#
# Единственный переход `draft|stale → approved` во всём мосту. Доставка
# права одобрять не имеет ни на одном пути (§I7), поэтому предикат
# «честно одобрен» и команда, которая его выполняет, — предмет отдельного
# блока.

def _approve(target: str, node_id: str, **kw) -> list[str]:
    return task_bridge.approve_node(
        target, _BUNDLE, node_id, kw.pop("by", _HUMAN),
        kw.pop("at", _HUMAN_AT), **kw,
    )


def test_approve_node_transitions_draft_and_records_the_human_act(
    tmp_path: Path,
) -> None:
    """Пункт 3 контракта команды: переход, `version + 1`, пины, подпись."""
    target = str(_target(tmp_path, approved=False))
    before = _meta(target, _CHARTER)["version"]

    changed = _approve(target, "charter")

    assert changed == [f"{_BUNDLE}/{_CHARTER}"]
    meta = _meta(target, _CHARTER)
    assert meta["status"] == "approved"
    assert meta["version"] == before + 1
    assert meta["approved_by"] == _HUMAN
    assert meta["approved_at"] == _HUMAN_AT


def test_approve_node_pins_actual_upstream_blobs(tmp_path: Path) -> None:
    """Пины пересчитываются с ФАКТИЧЕСКИХ байтов upstream, не с записанных."""
    target = str(_target(tmp_path, approved=False))
    _approve(target, "charter")

    _approve(target, "requirements")

    charter_blob = task_bridge.blob_sha1(
        (Path(target) / _BUNDLE / _CHARTER).read_text(encoding="utf-8")
    )
    assert _meta(target, _REQUIREMENTS)["upstream_hashes"] == {
        "charter": charter_blob
    }


def test_approve_node_refuses_when_direct_upstream_is_not_approved(
    tmp_path: Path,
) -> None:
    """Пункт 2: топологическая готовность прямых upstream — условие входа."""
    target = str(_target(tmp_path, approved=False))
    untouched = (Path(target) / _BUNDLE / _REQUIREMENTS).read_bytes()

    with pytest.raises(RuntimeError, match="топологическая готовность"):
        _approve(target, "requirements")

    assert (Path(target) / _BUNDLE / _REQUIREMENTS).read_bytes() == untouched
    assert _meta(target, _CHARTER)["status"] == "draft"


def test_approve_node_upstream_check_is_the_same_predicate(
    tmp_path: Path,
) -> None:
    """Upstream `approved`, но с разошедшимися пинами — тоже не готовность.

    Предикат ОДИН на все проверки: сведи готовность к `status == approved`,
    и узел, чей upstream был одобрен помимо этой команды и уехал вперёд,
    прошёл бы молча. Индукция «прямые, а не транзитивные» держится ровно
    на этом условии."""
    target = str(_target(tmp_path))
    _set_node(target, _BEHAVIOUR, status="stale")
    # requirements формально approved, но charter с тех пор уехал: его пин
    # больше не сходится, значит для behaviour-spec upstream не готов.
    _set_node(target, _CHARTER, version=99)

    with pytest.raises(RuntimeError, match="топологическая готовность") as exc:
        _approve(target, "behaviour-spec")
    assert "10-requirements.md" in str(exc.value)


def test_approve_node_repeat_over_honest_node_is_a_noop(
    tmp_path: Path,
) -> None:
    """Пункт 4: ни записи, ни новой подписи, ни инкремента `version`.

    Требование, а не вежливость: подпишись повтор заново, сменились бы
    байты узла, downstream уехал бы в `stale`, и каждый лишний вызов
    плодил бы долг на ровном месте."""
    target = str(_target(tmp_path))
    before = _node_bytes_of(target)

    assert _approve(target, "requirements", by="другой", at="2030-01-01") == []

    assert _node_bytes_of(target) == before


def test_approve_node_refuses_approved_node_with_drifted_pins(
    tmp_path: Path,
) -> None:
    """Пункт 6: fail-closed, а не молчаливая перепиновка (spec-runner#410).

    Отказ называет узел, обе величины разошедшегося пина и процедуру —
    снизу вверх ПО ПРИЧИНЕ: сперва одобрить изменившийся upstream, чей
    каскад объявит долг этому узлу."""
    target = str(_target(tmp_path))
    _set_node(
        target, _REQUIREMENTS,
        upstream_hashes={"charter": "de" + "0" * 38},
    )
    before = _node_bytes_of(target)

    with pytest.raises(RuntimeError, match="перепиновать") as exc:
        _approve(target, "requirements")

    message = str(exc.value)
    assert "de" + "0" * 38 in message
    actual = task_bridge.blob_sha1(
        (Path(target) / _BUNDLE / _CHARTER).read_text(encoding="utf-8")
    )
    assert actual in message
    assert "сперва одобрите изменившийся upstream" in message
    assert "charter" in message
    assert _node_bytes_of(target) == before


def test_approve_node_reapproves_node_whose_signature_is_empty(
    tmp_path: Path,
) -> None:
    """«Одобрено» без одобрившего — незаконное состояние, и единственный
    выход из него тот же, что у разошедшегося self-hash: переодобрение.

    Пины верны, ждать нечего; fail-closed здесь запер бы воркстрим
    навсегда — гейт такой узел не пропускает, а починить его больше
    нечем. Развилка спекой прямо не закрыта (п.5 назван через self-hash),
    решена по правилу «пробел, из которого нет штатного выхода, — не
    принятый остаток, а тупик» (§7)."""
    target = str(_target(tmp_path))
    _set_node(target, _CHARTER, approved_by="")

    assert _approve(target, "charter", by="человек", at="t") != []

    meta = _meta(target, _CHARTER)
    assert meta["approved_by"] == "человек"
    assert task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG, nodes=("charter",)
    ) == []


def test_approve_node_refuses_id_outside_the_active_dag(
    tmp_path: Path,
) -> None:
    """Approve есть акт о ПОЗИЦИИ В ГРАФЕ, а не о файле на диске."""
    target = str(_target(tmp_path, approved=False))

    with pytest.raises(RuntimeError, match="не входит в активный DAG") as exc:
        _approve(target, "workstreams/WS-alpha-7/spec/00-charter.md")
    assert "charter" in str(exc.value)


# --- каскад `stale`: рекурсивный, меняет ровно статус ----------------------


def test_cascade_is_recursive_not_single_level(tmp_path: Path) -> None:
    """Approve requirements → behaviour-spec/design/acceptance в `stale`, и
    decomposition, который requirements НЕ пинует, — тоже.

    Смена статуса есть правка файла, а файл узла входит в пин его
    собственных downstream: одноуровневый каскад оставил бы decomposition
    `approved` с пином на байты design/acceptance, которых уже нет, — то
    самое незаконное состояние, ради запрета которого он вводился."""
    target = str(_target(tmp_path))
    _set_node(target, _REQUIREMENTS, status="stale")

    changed = _approve(target, "requirements")

    assert set(changed) == {
        f"{_BUNDLE}/{name}" for name in (
            _REQUIREMENTS, _BEHAVIOUR, _DESIGN, _ACCEPTANCE, _DECOMPOSITION,
        )
    }
    for name in (_BEHAVIOUR, _DESIGN, _ACCEPTANCE, _DECOMPOSITION):
        assert _meta(target, name)["status"] == "stale", name


def test_cascade_stops_on_a_node_that_already_carries_the_debt(
    tmp_path: Path,
) -> None:
    """Долг у него объявлен, помечать нечем — рекурсия конечна без счётчика."""
    target = str(_target(tmp_path))
    _set_node(target, _REQUIREMENTS, status="stale")
    _set_node(target, _BEHAVIOUR, status="draft")
    _set_node(target, _DESIGN, status="stale")
    frozen = {
        name: (Path(target) / _BUNDLE / name).read_bytes()
        for name in (_BEHAVIOUR, _DESIGN)
    }

    changed = _approve(target, "requirements")

    assert f"{_BUNDLE}/{_BEHAVIOUR}" not in changed
    assert f"{_BUNDLE}/{_DESIGN}" not in changed
    for name, before in frozen.items():
        assert (Path(target) / _BUNDLE / name).read_bytes() == before
    # acceptance пинует requirements напрямую — его каскад достаёт.
    assert _meta(target, _ACCEPTANCE)["status"] == "stale"


def test_cascade_changes_status_and_nothing_else(tmp_path: Path) -> None:
    """Подпись, `version` и прежние пины `stale`-узла сохраняются.

    Пины обязаны продолжать указывать на одобренные байты — это запись
    «что именно покрывала подпись», а не устаревший мусор; операция
    СОЗДАЁТ долг, а не гасит его."""
    target = str(_target(tmp_path))
    _set_node(target, _REQUIREMENTS, status="stale")
    _set_node(target, _BEHAVIOUR, version=7)
    before = _meta(target, _BEHAVIOUR)
    assert before["version"] == 7 and before["approved_by"] == _HUMAN

    _approve(target, "requirements")

    # Весь frontmatter дословно прежний, кроме одного ключа: `version`,
    # подпись и пины `stale`-узла — записи о ПРОШЛОМ одобрении.
    assert _meta(target, _BEHAVIOUR) == {**before, "status": "stale"}


def test_after_any_approve_no_approved_node_carries_a_false_pin(
    tmp_path: Path,
) -> None:
    """Инвариант каскада, сформулированный через результат, а не обход:
    это ровно предикат, который потом проверяет гейт §I12 у доставки."""
    target = str(_target(tmp_path))
    _set_node(target, _DESIGN, status="stale")

    _approve(target, "design")

    stale_free = [
        finding for finding in task_bridge._approval_findings(
            target, _BUNDLE, task_bridge._BUNDLE_DAG
        )
        if "пин" in finding
    ]
    assert stale_free == []


# --- волна одобрения: накапливающая ветка, её head, draft-PR ---------------
#
# Ветка — предмет проверки, а не декорация вокруг неё, поэтому здесь
# ЖИВОЙ git: bare origin + клон. Стаб подтвердил бы только то, что мы его
# так написали; «второй вызов видит результат первого» — свойство ветки,
# и проверять его надо на ветке.


def _git(*args: str, cwd) -> str:
    import subprocess

    done = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=True,
    )
    return done.stdout


class _WaveOps(task_bridge.RealOps):
    """RealOps с живым git и заглушённой форджей (gh наружу не ходит)."""

    def __init__(self, login: str = _HUMAN) -> None:
        self.login = login
        self.created: list[tuple[str, str]] = []
        self.pr_by_branch: dict[str, int] = {}

    def gh_login(self) -> str | None:
        return self.login

    def find_pr(self, repo_slug, branch, *, any_state=False):
        return self.pr_by_branch.get(branch)

    def create_draft_pr(
        self, target_dir, repo_slug, branch, title, body, label
    ) -> int:
        self.created.append((branch, title))
        self.pr_by_branch[branch] = 900 + len(self.created)
        return self.pr_by_branch[branch]


_WAVE_BRANCH = "spec/WS-alpha-7-bundle-approve"


def _wave_state(tmp_path: Path, monkeypatch, approved: bool = False):
    """Клон с бандлом на master + прогон над ним; → (state, origin)."""
    import subprocess

    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "master", str(origin)],
        check=True,
    )
    target = _target(tmp_path, approved=approved)
    _git("init", "-q", "-b", "master", cwd=target)
    _git("config", "user.email", "t@example.com", cwd=target)
    _git("config", "user.name", "t", cwd=target)
    _git("config", "commit.gpgsign", "false", cwd=target)
    _git("add", "-A", cwd=target)
    _git("commit", "-qm", "бандл", cwd=target)
    _git("remote", "add", "origin", str(origin), cwd=target)
    _git("push", "-q", "-u", "origin", "master", cwd=target)
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        target_dir=str(target),
        bundle_dir=_BUNDLE,
        profile=None,
        run_id="r-wave",
    )
    state.status = "completed"
    state.base_ref = "master"
    rs.save(state)
    return state, origin


def _origin_has(origin: Path, branch: str) -> bool:
    return bool(_git("branch", "--list", branch, cwd=origin).strip())


def test_wave_second_call_reads_the_head_of_the_accumulating_branch(
    tmp_path: Path, monkeypatch,
) -> None:
    """Условие сходимости волны, а не удобство.

    Волна идёт по DAG сверху вниз несколькими вызовами, и всё, что сделал
    предыдущий, лежит в ВЕТКЕ: мержа ещё не было. Читай второй вызов один
    base — топологическая готовность не была бы предъявлена ни для одного
    узла ниже первого, и он одобрял бы поверх устаревшего дерева."""
    state, origin = _wave_state(tmp_path, monkeypatch)
    ops = _WaveOps()

    first = task_bridge.approve_node_for_run(state, ops, "charter")
    second = task_bridge.approve_node_for_run(state, ops, "requirements")

    assert first == second, "второй PR не создаётся никогда"
    assert len(ops.created) == 1
    assert ops.created[0][0] == _WAVE_BRANCH
    # Пин requirements указывает на charter ИЗ ВЕТКИ (уже одобренный), а
    # не на его версию в base.
    head_charter = _git(
        "show", f"origin/{_WAVE_BRANCH}:{_BUNDLE}/{_CHARTER}",
        cwd=state.target_dir,
    )
    assert task_bridge.split_frontmatter(head_charter)[0]["status"] == (
        "approved"
    )
    meta = _meta(state.target_dir, _REQUIREMENTS)
    assert meta["status"] == "approved"
    assert meta["upstream_hashes"]["charter"] == task_bridge.blob_sha1(
        head_charter
    )
    # base не тронут: devtools пишет в соседние репо только PR-ом.
    base_charter = _git(
        "show", f"origin/master:{_BUNDLE}/{_CHARTER}", cwd=state.target_dir
    )
    assert task_bridge.split_frontmatter(base_charter)[0]["status"] == "draft"


def test_wave_commit_carries_the_node_and_the_whole_cascade(
    tmp_path: Path, monkeypatch,
) -> None:
    """Частичный коммит оставил бы в ветке `approved`-узел с ложным пином —
    состояние, которое каскад и обязан не допускать."""
    state, origin = _wave_state(tmp_path, monkeypatch, approved=True)
    _set_node(state.target_dir, _REQUIREMENTS, status="stale")
    _git("commit", "-qam", "correction: requirements в долг",
         cwd=state.target_dir)
    _git("push", "-q", "origin", "master", cwd=state.target_dir)

    task_bridge.approve_node_for_run(state, _WaveOps(), "requirements")

    files = _git(
        "show", "--name-only", "--format=", f"origin/{_WAVE_BRANCH}",
        cwd=state.target_dir,
    ).split()
    assert set(files) == {
        f"{_BUNDLE}/{name}" for name in (
            _REQUIREMENTS, _BEHAVIOUR, _DESIGN, _ACCEPTANCE, _DECOMPOSITION,
        )
    }


def test_wave_noop_over_honest_node_starts_no_branch(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Повтор — no-op: ни ветки, ни PR, ни коммита."""
    state, origin = _wave_state(tmp_path, monkeypatch, approved=True)
    ops = _WaveOps()

    assert task_bridge.approve_node_for_run(state, ops, "charter") is None

    assert ops.created == []
    assert not _origin_has(origin, _WAVE_BRANCH)
    assert "no-op" in capsys.readouterr().out


def test_wave_announces_readiness_only_when_the_dag_converges(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Пока DAG одобрен не целиком, PR остаётся draft и готовность не
    объявляется; последним вызовом механика СООБЩАЕТ готовность — перевод
    в ready и мерж человеческие (§I9)."""
    state, origin = _wave_state(tmp_path, monkeypatch)
    ops = _WaveOps()

    for node in ("charter", "requirements", "behaviour-spec", "design"):
        task_bridge.approve_node_for_run(state, ops, node)
        assert "волна не завершена" in capsys.readouterr().out
    task_bridge.approve_node_for_run(state, ops, "acceptance")
    assert "волна не завершена" in capsys.readouterr().out

    task_bridge.approve_node_for_run(state, ops, "decomposition")

    out = capsys.readouterr().out
    assert "одобрен целиком" in out
    assert "ready" in out and "человеческ" in out
    assert task_bridge._approval_findings(
        state.target_dir, _BUNDLE, task_bridge._BUNDLE_DAG
    ) == []


@pytest.mark.parametrize("login", ["ai-prosto", None])
def test_wave_refuses_agent_and_unresolved_login_without_a_write(
    tmp_path: Path, monkeypatch, login,
) -> None:
    """Профильный guard: подпись узла от агентской учётки неотличима в
    артефакте от человеческой; пустой логин — подписывать нечем."""
    state, origin = _wave_state(tmp_path, monkeypatch)
    ops = _WaveOps(login=login)

    with pytest.raises(RuntimeError, match="approve|подписывать нечем"):
        task_bridge.approve_node_for_run(state, ops, "charter")

    assert ops.created == []
    assert not _origin_has(origin, _WAVE_BRANCH)
    assert _meta(state.target_dir, _CHARTER)["status"] == "draft"


def test_wave_refuses_under_the_review_gh_config_dir(
    tmp_path: Path, monkeypatch,
) -> None:
    """Вторая половина того же факта: профиль опознаётся и по каталогу.

    Переопределённый `REVIEW_LOGIN` рассогласовал бы проверку, стой она
    на одном лишь имени учётки."""
    state, origin = _wave_state(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "GH_CONFIG_DIR", str(task_bridge.REVIEW_GH_CONFIG_DIR)
    )
    ops = _WaveOps(login="andrei-shtanakov")

    with pytest.raises(RuntimeError, match="ревью-контурный"):
        task_bridge.approve_node_for_run(state, ops, "charter")

    assert ops.created == []
    assert not _origin_has(origin, _WAVE_BRANCH)


def test_wave_refusal_leaves_no_branch(tmp_path: Path, monkeypatch) -> None:
    """§I12 п.6: отказ не пишет ни файла узла, ни статусов downstream, ни
    ветки — валидация стоит ДО создания накапливающей."""
    state, origin = _wave_state(tmp_path, monkeypatch)
    ops = _WaveOps()

    with pytest.raises(RuntimeError, match="топологическая готовность"):
        task_bridge.approve_node_for_run(state, ops, "requirements")

    assert not _origin_has(origin, _WAVE_BRANCH)
    assert ops.created == []
    assert _meta(state.target_dir, _REQUIREMENTS)["status"] == "draft"
    # «Ни ветки» — и локальной тоже: отказ обязан оставить клон там же,
    # где взял. Ветка создаётся ПОСЛЕ валидации, а не до неё.
    assert _git("branch", "--list", _WAVE_BRANCH,
                cwd=state.target_dir).strip() == ""
    assert _git("branch", "--show-current",
                cwd=state.target_dir).strip() == "master"


def test_wave_dirty_target_refuses(tmp_path: Path, monkeypatch) -> None:
    state, origin = _wave_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "чужое.txt").write_text("х", encoding="utf-8")

    with pytest.raises(RuntimeError, match="грязный"):
        task_bridge.approve_node_for_run(state, _WaveOps(), "charter")


# --- CLI `--approve-node` -------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        ["--supersede"],
        ["--conform-approve"],
        ["--abandon-revision", "2", "--reason", "x"],
        ["--replace-revision", "2", "--reason", "x", "--supersede"],
    ],
)
def test_cli_approve_node_is_the_only_action_of_the_run(extra, capsys) -> None:
    with pytest.raises(SystemExit):
        task_bridge.main(
            ["--run-id", "r", "--approve-node", "charter", *extra]
        )
    assert "отдельное действие" in capsys.readouterr().err


def test_cli_approve_node_refuses_a_path_instead_of_a_node_id(capsys) -> None:
    """Промах ловится на разборе аргументов — не пишется вовсе ничего."""
    with pytest.raises(SystemExit):
        task_bridge.main(
            ["--run-id", "r", "--approve-node",
             "workstreams/WS-alpha-7/spec/00-charter.md"]
        )
    err = capsys.readouterr().err
    assert "не node-id активного DAG" in err
    assert "decomposition" in err


def test_cli_approve_node_allowed_ids_follow_the_legacy_flag(capsys) -> None:
    """Состав допустимых id — активный DAG, а не полный список всегда."""
    with pytest.raises(SystemExit):
        task_bridge.main(
            ["--run-id", "r", "--approve-node", "acceptance",
             "--legacy-bundle", "5"]
        )
    err = capsys.readouterr().err
    assert "acceptance" in err and "decomposition" in err


def test_cli_approve_node_reaches_implementation(
    tmp_path, monkeypatch, capsys,
) -> None:
    state, _origin = _wave_state(tmp_path, monkeypatch)
    seen: dict = {}

    def _fake(s, o, node_id, legacy_bundle=None):
        seen.update(run=s.run_id, node=node_id, legacy=legacy_bundle)
        return 901

    monkeypatch.setattr(task_bridge, "approve_node_for_run", _fake)
    monkeypatch.setattr(task_bridge, "RealOps", lambda: object())

    assert task_bridge.main(["--run-id", "r-wave", "--approve-node",
                             "charter"]) == 0
    assert seen == {"run": "r-wave", "node": "charter", "legacy": None}


def test_cli_approve_node_failure_is_a_message_not_a_traceback(
    tmp_path, monkeypatch, capsys,
) -> None:
    state, _origin = _wave_state(tmp_path, monkeypatch)

    def _boom(s, o, node_id, legacy_bundle=None):
        raise RuntimeError("топологическая готовность не предъявлена")

    monkeypatch.setattr(task_bridge, "approve_node_for_run", _boom)
    monkeypatch.setattr(task_bridge, "RealOps", lambda: object())

    assert task_bridge.main(["--run-id", "r-wave", "--approve-node",
                             "requirements"]) == 1
    assert "топологическая готовность" in capsys.readouterr().out


# --- Граница эпохи штампа и гейт на остальных путях доставки --------------


def test_stamp_era_intent_is_not_resumed_and_names_the_way_out(
    tmp_path, monkeypatch,
) -> None:
    """§I4: ревизия эпохи штампа не возобновляется — fail-closed с процедурой.

    Её `prospective_anchor` посчитан по ПРОШТАМПОВАННОМУ дереву:
    продолжить значит доставить штамп, которого контракт больше не
    разрешает, а не продолжить — разойтись с записанным anchor'ом. Ни
    один из двух исходов не годится, поэтому выход — свернуть ревизию и
    переиздать обычным `--supersede`."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    _seed_revision(state, monkeypatch)
    saved = rs.load("r-recon")
    saved.ops["tasks-deliver-v2"].update(
        approval_pr=403, signed_nodes=["decomposition"],
    )
    rs.save(saved)
    ops = _RevisionPrOps(pr=None, local_head=None)

    with pytest.raises(RuntimeError, match="эпохи штампа") as exc:
        tb.deliver_superseded(saved, ops)

    message = str(exc.value)
    assert "--abandon-revision 2" in message
    assert "--supersede" in message
    assert not [c for c in ops.calls if c[0] in (
        "commit_paths", "push_branch", "create_draft_pr"
    )]


def test_completed_stamp_era_records_are_read_as_before(
    tmp_path, monkeypatch,
) -> None:
    """Удаление адресовано МЕХАНИЗМУ, а не журналу: завершённая ревизия,
    несущая `approval_pr`/`signed_nodes`, — законное историческое
    состояние, и код обязан её пережить, не спотыкаясь. Вычищать поля
    задним числом было бы мутацией журнала, которую §I4 запрещает."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _supersede_state(tmp_path, monkeypatch)
    content = tb._content_anchor(state.target_dir, state.bundle_dir, None)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "АНКЕР-v1"}
    state.ops["tasks-deliver-v2"] = {
        "status": "completed", "revision": 2, "pr": 700,
        "branch": "spec/WS-alpha-7-tasks-v2", "base_sha": "base-sha-1",
        "head_sha": "h2", "anchor": "АНКЕР-v2", "prospective_anchor": "АНКЕР-v2",
        "content_anchor": content, "tasks_version": 2,
        # Запись эпохи штампа — дословно как её сделали тогда.
        "approval_pr": 407, "signed_nodes": ["design", "acceptance"],
        "dag": [[f, list(u)] for f, u in tb._BUNDLE_DAG], "supersedes": 1,
    }
    rs.save(state)
    ops = _RevisionPrOps(pr=700, pr_state="MERGED", head="h2")

    # Апстрим с тех пор не менялся → бесследный no-op по её же записи.
    assert tb.deliver_superseded(state, ops) == tb.SupersedeResult("noop")
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["approval_pr"] == 407


def test_replacement_gate_refuses_before_the_intent_and_the_close(
    tmp_path, monkeypatch,
) -> None:
    """§I10 шаг 1: §I5 при замене пропущен, поэтому гейт §I12 — первая
    проверка, способная отказать. PR не закрывается, намерение не
    пишется, ветка и коммит не создаются."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _replace_state(tmp_path, monkeypatch)
    _set_node(state.target_dir, _DESIGN, status="stale")

    ops = _ReplaceOps()
    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        tb.deliver_superseded(state, ops, replace=_replace())

    assert f"{_BUNDLE}/{_DESIGN}" in str(exc.value)
    assert ops.closed == []
    assert "tasks-deliver-v4" not in rs.load("r-recon").ops
    assert not [c for c in ops.calls if c[0] in (
        "ensure_branch", "create_draft_pr"
    )]


def test_first_delivery_refuses_a_freshly_generated_bundle(
    tmp_path, monkeypatch, capsys,
) -> None:
    """Отказ первой доставки на свежем бандле ШТАТЕН, а не регресс.

    Бандл лежит в base целиком `draft`, потому что штамповать его больше
    некому: это и есть новый порядок работы («сгенерировать DAG →
    одобрить узлы топологически → deliver_for_run»)."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    for fname, _ in tb._BUNDLE_DAG:
        _set_node(state.target_dir, fname, status="draft")
    ops = _ReconOps()

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        tb.deliver_for_run(state, ops)

    message = str(exc.value)
    for fname, _ in tb._BUNDLE_DAG:
        assert f"{_BUNDLE}/{fname}" in message
    assert "--approve-node charter" in message
    assert "топологическом порядке" in message
    assert not [c for c in ops.calls if c[0] in (
        "ensure_branch", "commit_paths", "create_draft_pr"
    )]
    # Реконсиляция op'а уже записала `started` — это write-ahead, а не
    # эффект доставки; PR и ветки нет.
    assert rs.load("r-recon").ops["tasks-deliver"]["status"] == "started"


def test_first_delivery_never_asks_the_bundle_pr_for_a_signature(
    tmp_path, monkeypatch,
) -> None:
    """`mergedBy`/`mergedAt` бандл-PR больше не вход доставки (§I7).

    Решение devtools#110 приравнивало инициированный мерж к approve, и на
    этом стоял штамп первой доставки; в этой части оно пересмотрено. Мерж
    бандл-PR вносит байты бандла в base — и только."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)

    class _NoMergeFacts(_ReconOps):
        def pr_facts(self, repo_slug, pr):
            self.calls.append(("pr_facts", pr))
            return {"state": "MERGED", "headRefOid": "h"}

    ops = _NoMergeFacts()
    assert tb.deliver_for_run(state, ops) == 77
    # Факты бандл-PR (#5) не спрашивались вовсе: спрашивать нечего.
    assert ("pr_facts", 5) not in ops.calls


# --- Условие (4): узел помнит СВОИ одобренные байты ------------------------
#
# Пины помнят чужие байты, на которые узел опирался; `approved_content_hash`
# — свои, которые человек читал, когда одобрял. Без второй половины подпись
# непроверяема в принципе, а воркстрим запирается: см. тупик ниже.


def _self_hash_of(target: str, fname: str) -> str:
    """Оракул условия (4), написанный по определению контракта."""
    contour = (
        "status", "version", "approved_by", "approved_at",
        "upstream_hashes", "approved_content_hash",
    )
    meta, body = task_bridge.split_frontmatter(
        (Path(target) / _BUNDLE / fname).read_text(encoding="utf-8")
    )
    return task_bridge.blob_sha1(
        task_bridge.join_frontmatter(
            {k: v for k, v in meta.items() if k not in contour}, body,
        )
    )


def _correct_body(target: str, fname: str) -> None:
    """Correction правит ТЕЛО узла, frontmatter не трогая."""
    path = Path(target) / _BUNDLE / fname
    path.write_text(
        path.read_text(encoding="utf-8") + "\nПравка correction'а.\n",
        encoding="utf-8",
    )


def test_approve_node_records_the_hash_of_its_own_approved_bytes(
    tmp_path: Path,
) -> None:
    """Переход пишет self-hash по КАНОНИЧЕСКОЙ собственной части узла."""
    target = str(_target(tmp_path, approved=False))

    _approve(target, "charter")

    meta = _meta(target, _CHARTER)
    assert meta["approved_content_hash"] == _self_hash_of(target, _CHARTER)


def test_self_hash_does_not_hash_itself_nor_the_approval_contour(
    tmp_path: Path,
) -> None:
    """Проекция вырезает весь approval-контур И само поле.

    Само поле — по механической причине: оно лежит в том же frontmatter и
    иначе хешировало бы себя. Контур — по существу: вопрос (4) про ТО,
    ЧТО ОДОБРЯЛИ, а не про то, как одобрение записано."""
    target = str(_target(tmp_path))
    before = _meta(target, _CHARTER)["approved_content_hash"]

    _set_node(
        target, _CHARTER, version=99, status="stale",
        approved_by="кто-то", approved_at="когда-то",
    )

    assert _self_hash_of(target, _CHARTER) == before


def test_reapproval_converges_because_the_field_does_not_hash_itself(
    tmp_path: Path,
) -> None:
    """Поле обязано вырезать САМО СЕБЯ, и это проверяется сходимостью.

    Не вырезай оно себя — каждый approve считал бы хеш по байтам с
    ПРЕДЫДУЩИМ значением поля, записывал бы новое и тем делал записанное
    неверным: узел не стал бы честно одобренным никогда, а повторный
    вызов не был бы no-op'ом. Первый approve этого не показывает (поля
    ещё нет вовсе) — показывает второй."""
    target = str(_target(tmp_path))
    honest = ("charter",)

    _correct_body(target, _CHARTER)
    assert _approve(target, "charter") != []
    assert task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG, nodes=honest
    ) == []

    _correct_body(target, _CHARTER)
    assert _approve(target, "charter") != []
    assert task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG, nodes=honest
    ) == []
    # И повтор поверх сошедшегося — no-op.
    assert _approve(target, "charter") == []


def test_stale_keeps_its_self_hash_valid(tmp_path: Path) -> None:
    """Пометка долга не вправе делать неверной запись о том, что покрывала
    подпись: каскад меняет ровно `status`, а `status` проекция режет."""
    target = str(_target(tmp_path))
    _set_node(target, _REQUIREMENTS, status="stale")

    _approve(target, "requirements")

    meta = _meta(target, _BEHAVIOUR)
    assert meta["status"] == "stale"
    assert meta["approved_content_hash"] == _self_hash_of(target, _BEHAVIOUR)


def test_body_edit_of_an_approved_node_is_caught_by_the_fourth_condition(
    tmp_path: Path,
) -> None:
    """Подпись покрывает байты, которых человек не видел, — и предикат это
    ловит. Трёхусловный не ловил по построению: он помнил чужие байты и
    не помнил своих."""
    target = str(_target(tmp_path))
    _correct_body(target, _REQUIREMENTS)

    findings = task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG, nodes=("requirements",)
    )

    assert len(findings) == 1
    assert "approved_content_hash" in findings[0]
    assert "не видел" in findings[0]


def test_body_edit_of_the_terminal_node_is_caught_too(
    tmp_path: Path,
) -> None:
    """Терминальный узел покрыт наравне со всеми: своя запись есть у
    каждого. Через downstream его не поймать — downstream у него нет."""
    target = str(_target(tmp_path))
    _correct_body(target, _DECOMPOSITION)

    with pytest.raises(RuntimeError, match="одобрен не целиком") as exc:
        _deliver(target, _StubOps())
    assert f"{_BUNDLE}/{_DECOMPOSITION}" in str(exc.value)


def test_fourth_condition_unlocks_the_dead_end(tmp_path: Path) -> None:
    """Тупик, ради которого условие (4) и заведено.

    Correction правит тело `approved`-узла, не трогая frontmatter. По
    трёхусловному предикату он честно одобрен ⇒ `--approve-node` даёт
    no-op ⇒ каскад не запускается ⇒ downstream остаются `approved` с
    пинами на старый блоб ⇒ fail-closed, и перевести их в `stale`
    некому. Воркстрим заперт: единственный узел, чей approve сдвинул бы
    дело с места, считается уже одобренным.

    С условием (4) тот же вызов — ПЕРЕОДОБРЕНИЕ: новая подпись, новый
    self-hash, `version + 1` и рекурсивный каскад, который объявляет долг
    downstream. Дальше волна идёт обычным порядком."""
    target = str(_target(tmp_path))
    _correct_body(target, _REQUIREMENTS)
    before_version = _meta(target, _REQUIREMENTS)["version"]

    changed = _approve(target, "requirements", by="человек", at="t-новое")

    assert changed != [], "no-op здесь и есть тупик"
    meta = _meta(target, _REQUIREMENTS)
    assert meta["version"] == before_version + 1
    assert meta["approved_by"] == "человек"
    assert meta["approved_content_hash"] == _self_hash_of(
        target, _REQUIREMENTS
    )
    # Каскад объявил долг downstream — тем, кто в тупике был fail-closed.
    for name in (_BEHAVIOUR, _DESIGN, _ACCEPTANCE, _DECOMPOSITION):
        assert _meta(target, name)["status"] == "stale", name
    # И после волны DAG проходит гейт целиком.
    for fname, _ in task_bridge._BUNDLE_DAG:
        _approve(target, task_bridge._node_id(fname), by="человек", at="t")
    assert task_bridge._approval_findings(
        target, _BUNDLE, task_bridge._BUNDLE_DAG
    ) == []


def test_reapproval_still_requires_topological_readiness(
    tmp_path: Path,
) -> None:
    """Пункт 2 адресован команде целиком, и сходящиеся пины его не
    заменяют: совпавший пин доказывает лишь, что байты upstream не
    менялись с момента подписи, — а миграционный долг байтов не меняет
    вовсе, и такой upstream проходил бы молча."""
    target = str(_target(tmp_path))
    # Легаси-дерево: у charter'а нет self-hash, но пины downstream указывают
    # на его ФАКТИЧЕСКИЕ байты — миграционный долг байтов не меняет, и
    # сходящийся пин про него ничего не говорит.
    _set_node(target, _CHARTER, approved_content_hash=None)
    _repin_all(target)
    _correct_body(target, _REQUIREMENTS)
    assert task_bridge._pin_drift(
        Path(target) / _BUNDLE,
        {task_bridge._node_id(f): f for f, _ in task_bridge._BUNDLE_DAG},
        ("charter",),
        _meta(target, _REQUIREMENTS),
    ) == [], "пины requirements обязаны сходиться — иначе тест о другом"
    before = _node_bytes_of(target)

    with pytest.raises(RuntimeError, match="топологическая готовность") as exc:
        _approve(target, "requirements")

    assert f"{_BUNDLE}/{_CHARTER}" in str(exc.value)
    assert "миграционный долг" in str(exc.value)
    assert _node_bytes_of(target) == before


def test_reapproval_is_not_triggered_when_the_self_hash_still_matches(
    tmp_path: Path,
) -> None:
    """Обратная сторона: совпал self-hash — байты те же, что человек
    читал, и повтор обязан остаться no-op'ом (п.4)."""
    target = str(_target(tmp_path))
    before = _node_bytes_of(target)

    assert _approve(target, "decomposition", by="другой", at="t2") == []

    assert _node_bytes_of(target) == before
