"""Unit-тесты governance.decomposition_guard: парсер DT и инварианты
графа. Никакого git/ФС — только строки."""

from __future__ import annotations

from governance.decomposition_guard import DtTask, parse_dt_tasks

DT_OK = (
    "#### DT-01: Парсер · type: implement · owner: dev\n"
    "scenarios: [BEH-01, BEH-02]\n"
    "depends_on: []\n"
    "parallel_group: core\n"
    "Проза предмета.\n"
    "\n"
    "#### DT-02: Проверка парсера · type: verify · owner: qa\n"
    "scenarios: [BEH-03]\n"
    "depends_on: [DT-01]\n"
    "delivered_by: [DT-01]\n"
    "parallel_group: core\n"
    "Проза.\n"
)


def test_parse_two_tasks() -> None:
    tasks, findings = parse_dt_tasks(DT_OK)
    assert findings == []
    assert [t.dt_id for t in tasks] == ["DT-01", "DT-02"]
    assert tasks[0] == DtTask(
        dt_id="DT-01", title="Парсер", type="implement", owner="dev",
        scenarios=("BEH-01", "BEH-02"), depends_on=(),
        delivered_by=(), parallel_group="core",
    )
    assert tasks[1].delivered_by == ("DT-01",)


def test_near_miss_heading_is_a_finding() -> None:
    """Урок minor'ов PR #145: похожий на DT заголовок мимо строгой
    грамматики — находка, не молчаливое исключение."""
    bad = "#### DT-03 Парсер · type: implement · owner: dev\n"  # нет «:»
    tasks, findings = parse_dt_tasks(bad)
    assert tasks == []
    assert any("DT-03" in f and "грамматик" in f for f in findings)


def test_duplicate_dt_id_is_a_finding() -> None:
    dup = DT_OK + "\n#### DT-01: Дубль · type: implement · owner: dev\n" \
        "scenarios: [BEH-04]\ndepends_on: []\nparallel_group: solo\n"
    _tasks, findings = parse_dt_tasks(dup)
    assert any("DT-01" in f and "раза" in f for f in findings)


def test_missing_scenarios_is_a_finding() -> None:
    bad = (
        "#### DT-05: Пустой · type: implement · owner: dev\n"
        "depends_on: []\nparallel_group: solo\n"
    )
    _tasks, findings = parse_dt_tasks(bad)
    assert any("DT-05" in f and "scenarios" in f for f in findings)


def test_block_ends_at_next_section() -> None:
    """Урок major'а PR #145: блок DT кончается на следующей секции уровня
    1–3 — метаданные чужой секции не читаются как свои."""
    text = (
        "#### DT-01: Одинокий · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n"
        "\n## Инварианты графа\n\nparallel_group: мусор\n"
    )
    tasks, findings = parse_dt_tasks(text)
    assert findings == []
    assert tasks[0].parallel_group == "solo"
