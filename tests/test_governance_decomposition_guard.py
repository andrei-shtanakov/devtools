"""Unit-тесты governance.decomposition_guard: парсер DT и инварианты
графа. Никакого git/ФС — только строки."""

from __future__ import annotations

import pytest

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
    "verifies: [tests/test_a.py]\n"
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
    assert tasks[1].verifies == ("tests/test_a.py",)


# --- TDD-waiver: ТРИ ВЕТКИ ОТКАЗА -----------------------------------------
#
# Ветки отказа написаны ПЕРВЫМИ и стоят выше зелёной дороги намеренно.
# Гвард, у которого проверен только проход, свидетельствует о себе ровно
# столько же, сколько зелёный гвард, не смотревший половину цели: «не
# отказал» неотличимо от «не проверял». Каждый отказ называет DT и
# причину — иначе оператору некуда идти.


def _waived(
    line: str = "tdd_waiver: characterisation · sanction: batch-approve-2026-09-09",
    dt_type: str = "implement",
    depends: str = "[DT-01]",
) -> str:
    """DT-02 с объявлением waiver'а; DT-01 — доставляющая зависимость."""
    return (
        "#### DT-01: Поведение · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\n"
        "depends_on: []\n"
        "parallel_group: core\n"
        "Проза.\n"
        "\n"
        f"#### DT-02: Характеризация · type: {dt_type} · owner: qa\n"
        "scenarios: [BEH-02]\n"
        f"depends_on: {depends}\n"
        + ("delivered_by: [DT-01]\n" if dt_type == "verify" else "")
        + "parallel_group: regression\n"
        f"{line}\n"
        "Проза.\n"
    )


def test_waiver_of_unknown_class_is_refused() -> None:
    """Класс — ЗАКРЫТЫЙ словарь; неизвестный отвергается.

    Это и есть замена эвристике по слову «waiver» в прозе: открытый
    словарь означал бы, что санкцией становится любое слово, которое
    автор счёл подходящим, — то есть догадка вместо санкции.
    """
    _, findings = parse_dt_tasks(
        _waived("tdd_waiver: потому-что-так-быстрее · sanction: я-решил")
    )
    assert any(
        "DT-02" in f and "класс" in f for f in findings
    ), f"класс не назван причиной отказа: {findings}"


def test_waiver_without_dependencies_is_refused() -> None:
    """Условие 1 класса: поведение доставлено ЗАВИСИМОСТЯМИ задачи.

    У DT без единой зависимости доставлять поведение нечем — объявление
    противоречит собственному классу, и принять его значило бы принять
    waiver там, где честный RED как раз возможен.
    """
    _, findings = parse_dt_tasks(_waived(depends="[]"))
    assert any(
        "DT-02" in f and "зависим" in f for f in findings
    ), f"отсутствие зависимостей не названо: {findings}"


def test_waiver_on_verify_task_is_refused() -> None:
    """У verify свой режим (`verify_first`) — снимать RED ему нечем.

    Waiver здесь не «избыточен», а противоречив: он назначил бы задаче
    второй режим исполнения, и какой из двух попадёт в `**Mode:**`,
    решал бы порядок строк в рендере.
    """
    _, findings = parse_dt_tasks(_waived(dt_type="verify"))
    assert any(
        "DT-02" in f and "verify" in f for f in findings
    ), f"waiver у verify-задачи принят: {findings}"


def test_malformed_waiver_key_is_a_finding_not_silence() -> None:
    """Ключ есть, форма не разобрана — находка, а не «поля нет».

    Тот же урок, что у `verifies` (round 13 ревью PR #161): молчаливая
    деградация до «объявления не было» неотличима от легаси-DT, и
    задача уехала бы в проектный `tdd` с непройденным RED — ровно тот
    останов, ради которого проекция заводится.
    """
    _, findings = parse_dt_tasks(_waived("tdd_waiver: characterisation"))
    assert any(
        "DT-02" in f and "tdd_waiver" in f for f in findings
    ), f"битая форма проглочена молча: {findings}"


def test_waiver_declared_twice_is_a_finding() -> None:
    """По одному объявлению на DT — как и один DT-id на документ."""
    line = (
        "tdd_waiver: characterisation · sanction: batch-approve-2026-09-09\n"
        "tdd_waiver: characterisation · sanction: batch-approve-2026-09-09"
    )
    _, findings = parse_dt_tasks(_waived(line))
    assert any(
        "DT-02" in f and "tdd_waiver" in f for f in findings
    ), f"второе объявление принято: {findings}"


@pytest.mark.parametrize(
    "sanction",
    [
        "потому-что-можно",
        "batch-approve",
        "batch-approve-2026-13-45",
        "batch-approve-2026-9-9",
        "spec-runner#",
        "#425",
        "spec-runner#abc",
    ],
    ids=[
        "свободный-текст", "без-даты", "несуществующая-дата",
        "не-ISO", "без-номера", "без-репо", "номер-не-число",
    ],
)
def test_sanction_outside_the_closed_grammar_is_refused(sanction: str) -> None:
    """`sanction:` — закрытая грамматика, иначе поле есть украшение.

    Свободный текст пропускал бы `sanction: потому что можно` наравне с
    настоящим решением, и машиночитаемость объявления кончалась бы на
    классе. Календарность даты проверяется тоже: `2026-13-45` — строка
    нужного ВИДА, но не дата, и принять её значило бы проверить форму
    формы, а не форму.
    """
    _, findings = parse_dt_tasks(
        _waived(f"tdd_waiver: characterisation · sanction: {sanction}")
    )
    assert any(
        "DT-02" in f and "sanction" in f for f in findings
    ), f"санкция {sanction!r} принята: {findings}"


@pytest.mark.parametrize(
    "sanction",
    ["batch-approve-2026-09-09", "spec-runner#425", "devtools#1"],
    ids=["датированное-решение", "ссылка-на-PR", "однозначный-номер"],
)
def test_sanction_inside_the_closed_grammar_is_accepted(sanction: str) -> None:
    """Обе допустимые формы принимаются — и это вторая половина проверки.

    Без неё грамматика, отвергающая ВСЁ, выглядела бы исправной: «не
    принял мусор» и «не принимает ничего» на одних отказных тестах
    неразличимы.
    """
    tasks, findings = parse_dt_tasks(
        _waived(f"tdd_waiver: characterisation · sanction: {sanction}")
    )
    assert findings == []
    assert tasks[1].waiver is not None
    assert tasks[1].waiver.sanction == sanction


def test_all_waiver_findings_are_reported_at_once() -> None:
    """Причины не прячутся одна за другой: гейт показывает всё сразу.

    Объявление нарушает четыре условия разом — неизвестный класс, кривая
    санкция, `type: verify` и отсутствие зависимостей. Ранний возврат
    после первой находки заставил бы оператора чинить объявление
    кругами, по одной за заход, и каждый круг выглядел бы как новый
    дефект.
    """
    _, findings = parse_dt_tasks(
        _waived(
            "tdd_waiver: выдуманный · sanction: потому-что-можно",
            dt_type="verify",
            depends="[]",
        )
    )
    mine = [f for f in findings if "DT-02" in f]
    assert len(mine) == 4, f"названы не все причины: {findings}"
    joined = "\n".join(mine)
    for expected in ("класс", "sanction", "verify", "зависим"):
        assert expected in joined, f"причина {expected!r} не названа"


def test_declared_waiver_is_parsed_into_the_task() -> None:
    """Зелёная дорога — ПОСЛЕ веток отказа.

    Разобранное объявление доступно структурно (класс и санкция
    отдельными величинами), а не строкой: мост обязан печатать условия
    КЛАССА, и выводить класс из строки повторным разбором значило бы
    завести второго вычислителя одного факта.
    """
    tasks, findings = parse_dt_tasks(_waived())
    assert findings == []
    waiver = tasks[1].waiver
    assert waiver is not None
    assert waiver.node_class == "characterisation"
    assert waiver.sanction == "batch-approve-2026-09-09"
    assert tasks[0].waiver is None, "объявление адресно, а не на документ"


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


def test_letter_suffixed_beh_is_parsed_and_covered() -> None:
    """PR #148: BEH-18a — валидный id (вставки раундов ревью); гард
    видит его во входном наборе, откат [a-z]? в _BEH_HEAD_RE краснит."""
    from governance.decomposition_guard import graph_findings
    beh = (
        "#### BEH-18: Обычный\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_x`\n\n"
        "#### BEH-18a: Вставленный\n**checked_by** `kind: e2e` "
        "`target: tests/test_b.py::test_y`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-18, BEH-18a]\ndepends_on: []\n"
        "parallel_group: solo\n"
    )
    assert graph_findings(beh, dt) == []


# --- FIX 1 (owner ruling, DT-14 multi-file group): структурное поле
# `verifies:` для type: verify — checked_by остаётся владением, verifies —
# группой наблюдения; single-owner эти файлы не касается. ------------------


def test_verifies_block_form_is_parsed() -> None:
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py\n"
        "  - tests/test_b.py\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py", "tests/test_b.py")


def test_verifies_block_form_survives_blank_lines() -> None:
    """Пустая строка внутри блочного списка его НЕ обрывает (round 14
    ревью PR #161): в YAML список продолжается через пустые строки, а
    обрыв молча терял всё, что за ней."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py\n"
        "\n"
        "  - tests/test_b.py\n"
        "\n"
        "Проза после списка его завершает.\n"
        "  - tests/test_never.py\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py", "tests/test_b.py")


def test_verifies_inline_form_is_also_accepted() -> None:
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies: [tests/test_a.py, tests/test_b.py]\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py", "tests/test_b.py")


def test_verifies_block_form_preserves_selector_entries() -> None:
    """Round 13 ревью PR #161: элементы блочной формы с `::`-селекторами
    сохраняются verbatim (срез `::` — дело только гарда/ownership-
    проверок, не парсера)."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py::test_one\n"
        "  - tests/test_b.py::test_two\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == (
        "tests/test_a.py::test_one", "tests/test_b.py::test_two",
    )


def test_verifies_unparsed_scalar_form_is_a_finding() -> None:
    """Major ревью PR #161, round 13 (контракт владельца): скалярное
    значение (`verifies: <путь>` без `[...]` и без блочного списка) не
    матчит ни инлайн-, ни блочную форму — раньше это молча деградировало
    до «поля нет вовсе» (пустой кортеж, никакой находки, closure-проверка
    и рендер про файл не знают). Теперь ключ verifies, присутствующий в
    блоке, но не разобранный НИ ОДНОЙ формой, — находка формы."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies: tests/test_ops.py\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert any(
        "DT-14" in f and "verifies" in f and "не разобран" in f
        for f in findings
    )
    assert tasks[0].verifies == ()


def test_verifies_empty_block_form_is_a_finding() -> None:
    """Round 13: `verifies:` объявлен блочно, но за ним НЕТ ни одной
    валидной строки `- <путь>` (сразу пустая строка/проза) — тоже находка,
    не тихое «поля нет»."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "\n"
        "Проза без единого элемента списка.\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert any(
        "DT-14" in f and "verifies" in f and "не разобран" in f
        for f in findings
    )
    assert tasks[0].verifies == ()


def test_verifies_block_form_accepts_unindented_entries() -> None:
    """Round 14 ревью PR #161, major (контракт владельца — откат
    чрезмерной строгости round 13): блочная форма verifies БЕЗ отступа
    (`- <путь>` прямо под ключом, столбец 0) — валидный YAML, разрешённый
    промптом авторинга (`_AUTHOR_DSL["decomposition"]` никогда не требовал
    отступа, только «one `- <file>` per line») — обязана парситься, не
    давать fatal-находку формы."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "- tests/test_a.py\n"
        "- tests/test_b.py\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py", "tests/test_b.py")


def test_verifies_block_form_indented_entries_still_accepted() -> None:
    """Round 14: отступленная форма (`  - <путь>`) остаётся валидной —
    отступ теперь ОПЦИОНАЛЕН, а не запрещён."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py\n"
        "  - tests/test_b.py\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py", "tests/test_b.py")


def test_verifies_block_form_stops_at_first_non_dash_non_blank_line() -> None:
    """Round 14 (контракт владельца): список останавливается на первой
    строке, которая НЕ является ни `- ` элементом (в любой форме — с
    отступом или без), ни пустой строкой — проза БЕЗ ведущего дефиса
    корректно завершает список."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py\n"
        "Проза предмета без ведущего дефиса.\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py",)


def test_verifies_block_form_stops_at_blank_line() -> None:
    """Пустая строка тоже заканчивает блочный список (round 13)."""
    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        "  - tests/test_a.py\n"
        "\n"
        "Проза предмета после пустой строки.\n"
    )
    tasks, findings = parse_dt_tasks(dt)
    assert findings == []
    assert tasks[0].verifies == ("tests/test_a.py",)


def test_verify_with_checked_by_target_but_without_verifies_is_legacy_ok() -> None:
    """Round 7 ревью PR #161, минор (контракт владельца, замена round-5
    безусловного «verify без verifies»): verify-DT с checked_by-целью в
    scenarios (легаси-форма single-file verify) — НЕ находка, даже без
    verifies, ни на уровне graph_findings, ни на уровне non_fatal_findings
    — промпт авторинга рекомендует verifies только когда группа наблюдения
    выходит за собственные checked_by-цели, и гард обязан это отражать
    (иначе конформный по промпту бандл шумит на зелёном гейте)."""
    from governance.decomposition_guard import graph_findings, non_fatal_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_b.py::test_two`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n\n"
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: solo\n"
    )
    assert graph_findings(beh, dt) == []
    assert non_fatal_findings(beh, dt) == []


def test_verify_group_underivable_is_a_fatal_finding() -> None:
    """Round 13 ревью PR #161, минор (контракт владельца — промотировано
    из non-fatal, round 7): условие «группа наблюдения не выводится
    ВООБЩЕ» (ни из checked_by, ни из verifies) ТОЖДЕСТВЕННО тому, при
    котором render_tasks_dt детерминированно поднимает RuntimeError —
    «не блокирует доставку» было ложью для этого входа. Теперь это FATAL
    находка `graph_findings` (S4-гейт и deliver() её видят и отказывают),
    а не non-fatal warning."""
    from governance.decomposition_guard import graph_findings, non_fatal_findings

    beh = (
        "#### BEH-01: Один\n- **checked_by**: без бэктиков, не биндится\n\n"
        "#### BEH-02: Два\n- **checked_by**: тоже без бэктиков\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n\n"
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: solo\n"
    )
    findings = graph_findings(beh, dt)
    assert any(
        "DT-14" in f and "группа наблюдения не выводится" in f
        for f in findings
    )
    # Больше НЕ дублируется в non-fatal канале — единственный вывод, fatal.
    assert not any(
        "группа наблюдения не выводится" in f
        for f in non_fatal_findings(beh, dt)
    )


def test_orphan_verifies_target_is_non_fatal_finding() -> None:
    """Round 7 ревью PR #161, минор (контракт владельца): элемент
    verifies, не совпавший ни с одной checked_by-целью бандла, — опечатка
    либо осиротевший путь, находка формы (не fatal), но обязана быть
    видна оператору через non_fatal_findings — иначе уезжает в
    tasks-спеку как селектор прогона, которого ни одна задача не
    создаёт."""
    from governance.decomposition_guard import graph_findings, non_fatal_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Два\n- **checked_by**: без бэктиков, не биндится\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n\n"
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: solo\n"
        "verifies:\n  - tests/test_typo.py\n"
    )
    warnings = non_fatal_findings(beh, dt)
    assert any(
        "DT-14" in f and "tests/test_typo.py" in f
        and "опечатка либо осиротевший путь" in f
        for f in warnings
    )
    assert graph_findings(beh, dt) == []


def test_verifies_target_owner_outside_depends_on_closure_is_a_finding() -> None:
    """Round 8 ревью PR #161, major (контракт владельца): verifies обязан
    ссылаться на файлы, чей владелец (checked_by) — в ТРАНЗИТИВНОМ
    ЗАМЫКАНИИ depends_on наблюдающей задачи, тот же инвариант, что уже
    есть у delivered_by. DT-03 наблюдает tests/test_a.py (владелец —
    DT-01), но depends_on=[DT-02] — DT-01 вне замыкания, verify_first-
    прогон законно стартовал бы раньше, чем DT-01 вообще создаст файл."""
    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::t1`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_b.py::t2`\n\n"
        "#### BEH-03: Три\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::t3`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: V · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-02]\n"
        "delivered_by: [DT-02]\nparallel_group: core\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    findings = graph_findings(beh, dt)
    assert any(
        "DT-03" in f and "tests/test_a.py" in f and "DT-01" in f
        and "замыкания" in f
        for f in findings
    )


def test_verifies_selector_form_does_not_bypass_closure_invariant() -> None:
    """Round 10 ревью PR #161, минор (контракт владельца): verifies в
    форме `file.py::test` (тот же полный pytest-селектор, в котором
    checked_by-цели реально приходят) обязана нормализоваться срезом
    `::` ПЕРЕД сверкой с владельцем — иначе `file_owner.get(f)` не
    находит запись (bindings несут ГОЛЫЕ пути), closure-инвариант молча
    пропускается, а orphan-проверка ложно маркирует РЕАЛЬНЫЙ файл как
    «опечатка». Зеркало
    test_verifies_target_owner_outside_depends_on_closure_is_a_finding,
    но verifies — с `::test1` вместо голого пути."""
    from governance.decomposition_guard import graph_findings, non_fatal_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::t1`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_b.py::t2`\n\n"
        "#### BEH-03: Три\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::t3`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: V · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-02]\n"
        "delivered_by: [DT-02]\nparallel_group: core\n"
        "verifies:\n  - tests/test_a.py::test_one\n"
    )
    findings = graph_findings(beh, dt)
    # closure-инвариант ловит владельца ВНЕ замыкания — как для голого пути.
    assert any(
        "DT-03" in f and "tests/test_a.py::test_one" in f and "DT-01" in f
        and "замыкания" in f
        for f in findings
    )
    # НЕ ложная находка «опечатка» — путь реально принадлежит DT-01.
    assert not any(
        "опечатка либо осиротевший путь" in f
        for f in non_fatal_findings(beh, dt)
    )


def test_verifies_target_owner_directly_in_depends_on_is_clean() -> None:
    """Owner DT-01 напрямую в depends_on наблюдающей задачи — чисто."""
    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::t1`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_b.py::t2`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: V · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    assert graph_findings(beh, dt) == []


def test_verifies_target_owner_transitively_in_depends_on_is_clean() -> None:
    """Owner DT-01 — не прямая, а ТРАНЗИТИВНАЯ зависимость (через DT-02) —
    тоже чисто: замыкание depends_on, а не только прямые рёбра."""
    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::t1`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_b.py::t2`\n\n"
        "#### BEH-03: Три\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::t3`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\nparallel_group: core\n\n"
        "#### DT-03: V · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-02]\n"
        "delivered_by: [DT-02]\nparallel_group: core\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    assert graph_findings(beh, dt) == []


def test_verifies_on_implement_is_a_finding() -> None:
    dt = (
        "#### DT-01: Реализация · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    _tasks, findings = parse_dt_tasks(dt)
    assert any(
        "DT-01" in f and "verifies" in f and "запрещ" in f for f in findings
    )


def test_single_owner_fires_even_when_own_checked_by_target_is_in_own_verifies() -> None:
    """Major ревью PR #161, round 4 finding 3 (корректирует round-2/3
    решение — прежний тест ошибочно ожидал здесь отсутствие находки):
    DT-01 (implement) владеет tests/test_a.py через BEH-01; DT-14 (verify)
    несёт СВОЙ сценарий BEH-02, чей checked_by-таргет — ТОТ ЖЕ файл, и ТАКЖЕ
    объявляет его в СВОЁМ verifies. Это НЕ observation — DT-14 сам
    редактирует файл через собственный checked_by (BEH-02), значит
    реально владеет им наравне с DT-01: single-owner обязан сработать.
    Владение (scenarios/checked_by) всегда старше наблюдения (verifies)."""
    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_two`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    findings = graph_findings(beh, dt)
    assert any(
        "tests/test_a.py" in f and "single-owner" in f for f in findings
    )


def test_single_owner_still_conflicts_between_implement_dts_despite_unrelated_verifies() -> None:
    """Major ревью PR #161, finding 4 (регресс на баг из ORIGINAL FIX 1):
    verifies какой-то ДРУГОЙ (verify) задачи, перечисляющий файл, НЕ
    отменяет single-owner между ДВУМЯ implement-задачами, реально
    претендующими на владение тем же файлом — байт-лок тест-файла остаётся
    в силе; наблюдение стороннего verify-DT не даёт implement-DT-ам молча
    делить файл."""
    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Один\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Два\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_two`\n\n"
        "#### BEH-03: Три\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_three`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: side\n\n"
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01, DT-02]\n"
        "delivered_by: [DT-01, DT-02]\nparallel_group: side\n"
        "verifies:\n  - tests/test_a.py\n"
    )
    findings = graph_findings(beh, dt)
    assert any(
        "tests/test_a.py" in f and "single-owner" in f for f in findings
    )


def test_unknown_beh_suffix_form_is_a_form_finding() -> None:
    """Major ревью PR #148: BEH-18A/BEH-18ab (форма вне [a-z]?) обязаны
    давать находку формы, а не молча склеиваться с предыдущим блоком."""
    from governance.decomposition_guard import graph_findings
    beh = (
        "#### BEH-18: Обычный\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_x`\n\n"
        "#### BEH-18A: Заглавный суффикс\n**checked_by** `kind: e2e` "
        "`target: tests/test_b.py::test_y`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-18]\ndepends_on: []\nparallel_group: solo\n"
    )
    findings = graph_findings(beh, dt)
    assert any("BEH-18A" in f and "грамматик" in f for f in findings)
