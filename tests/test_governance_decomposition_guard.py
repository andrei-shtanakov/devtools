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


BEH = (
    "#### BEH-01: Один\n**checked_by** `kind: integration` "
    "`target: tests/test_a.py::test_one`\n\n"
    "#### BEH-02: Два\n**checked_by** `kind: integration` "
    "`target: tests/test_a.py::test_two`\n\n"
    "#### BEH-03: Три\n**checked_by** `kind: e2e` "
    "`target: tests/test_b.py::test_three`\n"
)


def test_clean_graph_no_findings() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n"
    )
    assert graph_findings(BEH, dt) == []


def test_uncovered_and_double_covered_beh() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-03]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n"
    )
    findings = graph_findings(BEH, dt)
    assert any("BEH-02" in f and "не покрыт" in f for f in findings)
    assert any("BEH-03" in f and "дважды" in f for f in findings)


def test_cycle_is_a_finding() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: [DT-02]\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n"
    )
    assert any("цикл" in f for f in graph_findings(BEH, dt))


def test_forward_reference_in_depends_on_is_a_finding() -> None:
    """Находка 2 финального ревью: depends_on, ссылающийся на DT,
    объявленный НИЖЕ по документу, — форвард-ссылка «уезжает» в чужой
    репо (мост — чистый транслятор, порядок обязан быть топологическим
    уже на входе). DT-02 объявлен нормально (BEH-02, BEH-03 покрыты
    сюръективно), DT-01 ссылается на него раньше своего объявления."""
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-02]\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02, BEH-03]\ndepends_on: []\n"
        "parallel_group: core\n"
    )
    findings = graph_findings(BEH, dt)
    assert any(
        "DT-01" in f and "DT-02" in f and "ниже по документу" in f
        for f in findings
    )


def test_backward_reference_in_depends_on_is_not_a_finding() -> None:
    """Обратная сторона: DT-02 depends_on DT-01, объявленный ВЫШЕ —
    штатный топологический порядок, никакой находки про порядок."""
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\n"
        "parallel_group: core\n"
    )
    assert not any("ниже по документу" in f for f in graph_findings(BEH, dt))


def test_verify_requires_delivered_by_and_closure() -> None:
    from governance.decomposition_guard import graph_findings
    # verify без delivered_by
    dt1 = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: V · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n"
    )
    assert any(
        "DT-02" in f and "delivered_by" in f for f in graph_findings(BEH, dt1)
    )
    # delivered_by вне транзитивного замыкания depends_on
    dt2 = dt1.replace(
        "depends_on: [DT-01]\nparallel_group: core\n",
        "depends_on: []\ndelivered_by: [DT-01]\nparallel_group: core\n",
    )
    assert any(
        "DT-02" in f and "замыкан" in f for f in graph_findings(BEH, dt2)
    )


def test_delivered_by_forbidden_for_implement() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02, BEH-03]\ndepends_on: []\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
    )
    assert any(
        "DT-01" in f and "запрещ" in f for f in graph_findings(BEH, dt)
    )


def test_single_owner_of_test_file() -> None:
    from governance.decomposition_guard import graph_findings
    # BEH-01 и BEH-02 живут в одном tests/test_a.py, но в разных DT
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-03]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: side\n"
    )
    assert any(
        "tests/test_a.py" in f and "single-owner" in f
        for f in graph_findings(BEH, dt)
    )


def test_unknown_references_are_findings() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02, BEH-03, BEH-99]\n"
        "depends_on: [DT-77]\nparallel_group: core\n"
    )
    findings = graph_findings(BEH, dt)
    assert any("BEH-99" in f for f in findings)
    assert any("DT-77" in f for f in findings)


def test_solo_tasks_are_independent_singleton_groups() -> None:
    """Major ревью плана (2 круга): два solo-DT — не одна общая группа;
    ребро в один из них не требует зависимости от другого. Фикстура
    разносит DT по РАЗНЫМ файлам checked_by (BEH-01+BEH-02 живут в одном
    tests/test_a.py и потому обязаны быть в ОДНОЙ DT — иначе тест
    закраснел бы на собственном single-owner-инварианте, круг 2)."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: S1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: solo\n\n"
        "#### DT-02: Core · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n\n"
        "#### DT-03: S2 · type: implement · owner: dev\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-02]\nparallel_group: solo\n"
    )
    assert graph_findings(beh4, dt) == []


def test_delivered_by_edge_exempt_from_sinks_rule() -> None:
    """Minor круга 2: verify c depends_on=[DT-01] и delivered_by=[DT-01]
    в чужую группу с двумя стоками — НЕ находка про стоки."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: A1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: A2 · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: V · type: verify · owner: qa\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: qa\n"
    )
    assert not any("стоков" in f for f in graph_findings(beh4, dt))


def test_point_edge_into_single_foreign_group_is_legitimate() -> None:
    """Major круга 4: ребро в ОДНУ чужую группу с двумя стоками — не
    находка; требовать все стоки значило бы навязать искусственную
    сериализацию (мотивирующий дефект §1)."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: Парсер · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: CLI · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: API · type: implement · owner: dev\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-01]\nparallel_group: api\n"
    )
    assert not any("стоков" in f for f in graph_findings(beh4, dt))


def test_cross_group_dependency_must_cover_all_sinks() -> None:
    """Машинное правило «сводные за хвостами групп» (карта файлов плана):
    ребро в чужую группу обязывает зависеть от ВСЕХ её стоков."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: A1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: A2 · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n\n"
        # сводная: зависит от DT-01 (группа core), но не от DT-02 —
        # второго стока core
        "#### DT-04: Свод · type: implement · owner: dev\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-01, DT-03]\n"
        "parallel_group: solo\n"
    )
    assert any(
        "DT-04" in f and "DT-02" in f for f in graph_findings(beh4, dt)
    )


def test_beh_binding_grammar_matches_task_bridge() -> None:
    """Дубликат checked_by-регекса запинован: обе стороны читают одну
    фикстуру одинаково — включая блок с ДВУМЯ строками checked_by
    (правка поверх старой: обе стороны обязаны взять последнюю)."""
    from governance.decomposition_guard import _parse_beh_bindings
    from governance.task_bridge import parse_behaviour

    double = BEH + (
        "\n#### BEH-05: Пять\n**checked_by** `kind: e2e` "
        "`target: tests/test_old.py::test_five`\n"
        "**checked_by** `kind: integration` "
        "`target: tests/test_new.py::test_five`\n"
    )
    scenarios = parse_behaviour(double)
    bindings = _parse_beh_bindings(double)
    # двусторонняя сверка множеств id (minor круга 4: гард не должен
    # распознавать заголовки, которых не видит мост, и наоборот)
    assert {sc.beh_id for sc in scenarios} == set(bindings)
    for sc in scenarios:
        target, kind = bindings[sc.beh_id]
        expected = (
            sc.checked_target.split("::", 1)[0]
            if sc.checked_target else None
        )
        assert target == expected
        assert kind == (sc.checked_kind if sc.checked_target else None)
