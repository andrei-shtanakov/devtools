"""Unit-тесты governance.decomposition_guard: парсер DT и инварианты
графа. Никакого git/ФС — только строки."""

from __future__ import annotations

import pytest

from governance.decomposition_guard import (
    DELIVERABLE_KINDS,
    DtTask,
    dt_contract_findings,
    node_ids,
    parse_dt_tasks,
)

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


def test_broken_waiver_line_next_to_a_valid_one_is_a_finding() -> None:
    """Битая строка рядом с валидной отказывается как дубль (#198).

    Два ключа — уже неоднозначность независимо от того, сколько строк
    разобрано. Поэтому смешанный случай ловит ранняя ветка `keys > 1`, а
    не последующее сравнение keys/matches, где keys уже только 0 или 1.
    """
    line = (
        "tdd_waiver: characterisation\n"
        "tdd_waiver: characterisation · sanction: batch-approve-2026-09-09"
    )
    tasks, findings = parse_dt_tasks(_waived(line))
    assert any(
        "DT-02" in f and "tdd_waiver" in f for f in findings
    ), f"битая строка рядом с валидной проглочена: {findings}"
    assert tasks[1].waiver is None, "объявление с потерей не действует"


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


def test_waiver_conditions_match_the_contract() -> None:
    """Условия класса живут в ОДНОЙ редакции: спека и константа сверены.

    Прецедент — `test_beh_binding_grammar_matches_task_bridge`: дубликат
    запиновывается тестом, читающим обе стороны. Здесь предмет тот же и
    цена выше: из `WAIVER_CONDITIONS` мост печатает пункт чек-листа, а
    из прозы §3a владелец решает, давать ли санкцию, — разойдись они, и
    человек санкционирует одно, а исполнитель подтверждает другое.

    Расхождение уже случилось («обязан иметь» против «имеет») и было
    невидимо: прозу никто не исполняет. Второе место, где условия живут,
    — не дубликат для читателя, а второй вычислитель одного факта.
    """
    import re
    from pathlib import Path

    from governance.decomposition_guard import WAIVER_CONDITIONS

    spec = (
        Path(__file__).resolve().parents[1]
        / "docs/superpowers/specs"
        / "2026-09-05-decomposition-node-conveyor-design.md"
    ).read_text(encoding="utf-8")
    block = spec.split("**Условия класса `characterisation`**", 1)
    assert len(block) == 2, "раздел условий в спеке не найден"
    # Берётся ИМЕННО абзац-перечень, а не всё до следующего заголовка:
    # хвостовая проза за списком иначе прилипает к последнему пункту
    # через `\Z`, и тест краснеет на собственном разборе, а не на
    # расхождении. Абзац опознаётся по началу с «1. ».
    # Граница раздела ищется по НАЧАЛУ заголовка, а не по точному тексту:
    # заголовок переписан («Что делает мост — ТРИ строки, а не две»), и
    # якорь по точной строке перестал находиться — `split` молча вернул
    # весь остаток, граница выродилась, а тест остался зелёным. Пин,
    # который зеленеет от собственной промашки, не пин.
    tail = block[1]
    cut = tail.find("**Что делает мост")
    assert cut != -1, "раздел «Что делает мост» в спеке не найден — якорь устарел"
    body = tail[:cut]
    listing = next(
        para for para in body.split("\n\n") if para.lstrip().startswith("1. ")
    )
    items = [
        " ".join(m.group(1).split())
        for m in re.finditer(r"^\d+\. (.+?)(?=^\d+\. |\Z)", listing,
                             re.M | re.S)
    ]
    assert len(items) == 5, f"перечень условий разобран не целиком: {items}"
    normalized = [i.rstrip(";.").strip() for i in items]
    assert normalized == [
        " ".join(c.split()) for c in WAIVER_CONDITIONS["characterisation"]
    ]


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
    finding = next(f for f in findings if "DT-14" in f and "verifies" in f)
    assert "не разобран" in finding
    assert "отступ необязателен" in finding
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


def test_verifies_block_stops_before_prose_after_blank_line() -> None:
    """Blank lines are allowed, while the following prose ends the list."""
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


@pytest.mark.parametrize("indent", ["", "  "])
def test_verifies_block_does_not_consume_prose_bullet_after_blank(
    indent: str,
) -> None:
    """#162: a Markdown bullet after a blank line is DT prose, not a target."""

    dt = (
        "#### DT-14: Наблюдение · type: verify · owner: qa\n"
        "scenarios: [BEH-01]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
        "verifies:\n"
        f"{indent}- tests/test_a.py\n"
        "\n"
        f"{indent}- Эта маркированная строка объясняет границу задачи\n"
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


def test_manual_only_verify_group_is_underivable() -> None:
    """A document checked by a human is not an executable selector."""

    from governance.decomposition_guard import graph_findings

    beh = (
        "#### BEH-01: Реализация\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Документ\n**checked_by** `kind: manual` "
        "`target: docs/manual.md`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n\n"
        "#### DT-02: V · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: solo\n"
        "verifies: [docs/manual.md]\n"
    )

    findings = graph_findings(beh, dt)
    assert any(
        "DT-02" in finding
        and "группа наблюдения не выводится" in finding
        and "kind: manual" in finding
        for finding in findings
    )


def test_file_target_obeys_resolved_adapter_capability() -> None:
    """Bare files pass for pytest and fail early, by name, for ExUnit."""

    from governance.decomposition_guard import graph_findings
    from governance.spec_runner_contract import SELECTOR_POLICIES

    beh = (
        "#### BEH-01: Реализация\n**checked_by** `kind: integration` "
        "`target: tests/test_a.py::test_one`\n\n"
        "#### BEH-02: Проверка\n**checked_by** `kind: integration` "
        "`target: test/check_test.exs`\n"
    )
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n\n"
        "#### DT-02: V · type: verify · owner: qa\n"
        "scenarios: [BEH-02]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: solo\n"
    )

    assert graph_findings(
        beh, dt, selector_policy=SELECTOR_POLICIES["pytest"]
    ) == []
    findings = graph_findings(
        beh, dt, selector_policy=SELECTOR_POLICIES["exunit"]
    )
    assert any(
        "DT-02" in finding
        and "exunit" in finding
        and "path:line" in finding
        for finding in findings
    )

    selected = beh.replace("test/check_test.exs`", "test/check_test.exs:12`")
    assert graph_findings(
        selected, dt, selector_policy=SELECTOR_POLICIES["exunit"]
    ) == []


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
    fatal = graph_findings(beh, dt)
    assert any(
        "DT-14" in finding
        and "группа наблюдения не выводится" in finding
        for finding in fatal
    )


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


# --- Версия DT-контракта и форма delivers (devtools#282, спека §3b) --------
# Таблица поведения — решение владельца 2026-09-21. Ключевое: ОТСУТСТВИЕ
# ВЕРСИИ САМО ПО СЕБЕ РЕЖИМ НЕ ВКЛЮЧАЕТ. Иначе новый документ с забытым
# полем молча обошёл бы контракт — то есть барьер отключался бы ровно тем,
# от чего защищает.

_V2_FM = "---\nspec_stage: decomposition\ndt_contract_version: 2\n---\n"
#: Индекс узлов бандла для фикстур ниже: единственная ссылка, которую они
#: объявляют, — `acceptance#AC-07`. Тесты, чей предмет — САМО разрешение
#: ссылок, подают свой индекс и эту константу не берут.
_INDEX: dict[str, set[str]] = {"acceptance": {"AC-07"}}
_NO_FM = "---\nspec_stage: decomposition\n---\n"

_DT_V2 = (
    "#### DT-01: Парсер · type: implement · owner: dev\n"
    "scenarios: [BEH-01]\n"
    "depends_on: []\n"
    "parallel_group: core\n"
    "delivers:\n"
    "  - id: DEL-01\n"
    "    kind: capability\n"
    "    statement: \"парсер отвергает дубль ключа\"\n"
    "    sources:\n"
    "      - \"acceptance#AC-07\"\n"
    "Проза предмета.\n"
)


def test_missing_version_without_compat_is_an_error() -> None:
    """Забытое поле — отказ, а не молчаливый легаси-режим."""
    errors, warnings = dt_contract_findings(_NO_FM + DT_OK, allow_legacy_dt=False, node_index=_INDEX)
    assert any("версия" in e.lower() for e in errors), errors
    assert warnings == []


def test_missing_version_with_compat_is_legacy_with_diagnostic() -> None:
    """Режим включает ОПЕРАТОР параметром, а не отсутствие поля."""
    errors, warnings = dt_contract_findings(_NO_FM + DT_OK, allow_legacy_dt=True, node_index=_INDEX)
    assert errors == []
    # Регистр не пинуем: предмет проверки — что диагностика есть и
    # называет отсутствие гарантии, а не её типографика.
    assert any("гарантия переноса" in w.lower() for w in warnings), warnings


def test_v2_is_checked_regardless_of_the_compat_switch() -> None:
    """Объявленная версия сильнее переключателя: совместимость её не гасит."""
    for allow in (False, True):
        errors, warnings = dt_contract_findings(
            _V2_FM + _DT_V2, allow_legacy_dt=allow, node_index=_INDEX
    )
        assert errors == [], (allow, errors)
        assert warnings == [], (allow, warnings)


def test_v2_requires_delivers_on_every_dt() -> None:
    errors, _ = dt_contract_findings(_V2_FM + DT_OK, allow_legacy_dt=True, node_index=_INDEX)
    assert any("DT-01" in e and "delivers" in e for e in errors), errors


def test_v2_accepts_an_explicitly_empty_list() -> None:
    """`[]` — утверждение автора «объявленных результатов нет», не пропуск."""
    text = _V2_FM + DT_OK.replace(
        "parallel_group: core\nПроза предмета.",
        "parallel_group: core\ndelivers: []\nПроза предмета.",
        1,
    ).replace(
        "parallel_group: core\nverifies:",
        "parallel_group: core\ndelivers: []\nverifies:",
        1,
    )
    errors, _ = dt_contract_findings(text, allow_legacy_dt=False, node_index=_INDEX)
    assert errors == [], errors


@pytest.mark.parametrize("version", ["1", "3", "two", "2.0", ""])
def test_unknown_or_malformed_version_is_an_error_compat_does_not_mask(
    version: str,
) -> None:
    """Совместимость не маскирует неизвестную версию — иначе её включение
    стало бы способом обойти любой будущий контракт."""
    fm = f"---\nspec_stage: decomposition\ndt_contract_version: {version}\n---\n"
    for allow in (False, True):
        errors, _ = dt_contract_findings(fm + DT_OK, allow_legacy_dt=allow, node_index=_INDEX)
        # Причина названа, а не просто «ошибки есть»: мутант, пропускающий
        # неизвестную версию при включённой совместимости, ВЫЖИЛ на прежней
        # редакции — прогон краснел от постороннего «delivers отсутствует»,
        # и тест подтверждал существование чужой находки.
        assert any("неизвестная dt_contract_version" in e for e in errors), (
            version, allow, errors
        )


@pytest.mark.parametrize(
    ("broken", "expect"),
    [
        # Отступы включены в вырезаемую строку намеренно: без них
        # .replace склеивает остаток с соседней строкой и ломает YAML —
        # тест падал бы на разборе, а не на отсутствии поля.
        ("    statement: \"парсер отвергает дубль ключа\"\n", "statement"),
        ("      - \"acceptance#AC-07\"\n", "sources"),
        ("    kind: capability\n", "kind"),
    ],
)
def test_v2_delivers_form_requires_every_field(broken: str, expect: str) -> None:
    """Вида и идентификатора мало (спека §3b.1): statement обязателен, и
    отсутствие каждого поля называется своим именем."""
    errors, _ = dt_contract_findings(
        _V2_FM + _DT_V2.replace(broken, "", 1), allow_legacy_dt=False, node_index=_INDEX
    )
    assert any(expect in e for e in errors), (expect, errors)


def test_v2_delivers_rejects_duplicate_ids() -> None:
    """`id` обеспечивает связь; дубль сделал бы связь неоднозначной."""
    doubled = _DT_V2 + _DT_V2.replace("DT-01", "DT-02").replace(
        "Парсер", "Второй"
    )
    errors, _ = dt_contract_findings(_V2_FM + doubled, allow_legacy_dt=False, node_index=_INDEX)
    assert any("DEL-01" in e for e in errors), errors


def test_v2_source_must_be_addressed_not_a_heading() -> None:
    """Номера строк и текст заголовка идентификаторами не считаются
    (спека §3b.2): и то и другое меняется при редактуре."""
    bad = _DT_V2.replace('"acceptance#AC-07"', '"25-acceptance.md:41"')
    errors, _ = dt_contract_findings(_V2_FM + bad, allow_legacy_dt=False, node_index=_INDEX)
    assert any("sources" in e for e in errors), errors


def test_v2_delivers_rejects_duplicate_ids_inside_one_dt() -> None:
    """Блокер ревью #289: `set` схлопывал дубль ДО сверки.

    Проверка уникальности ловила только межзадачные дубли, а внутри-DT
    повтор исчезал ещё в сборе — то есть собственное сообщение проверки
    («уже объявлен в <DT>») для этого случая не могло быть напечатано
    даже структурно. Копипаст записи внутри одного DT — ровно тот вход, на
    котором она нужнее всего.
    """
    doubled = _DT_V2.replace(
        "      - \"acceptance#AC-07\"\n",
        "      - \"acceptance#AC-07\"\n"
        "  - id: DEL-01\n"
        "    kind: capability\n"
        "    statement: \"второе обязательство\"\n"
        "    sources:\n"
        "      - \"acceptance#AC-08\"\n",
        1,
    )
    errors, _ = dt_contract_findings(_V2_FM + doubled, allow_legacy_dt=False, node_index=_INDEX)
    assert any("DEL-01" in e for e in errors), errors


def test_v2_rejects_a_second_delivers_key_in_the_same_dt() -> None:
    """Строка-заглушка, оставшаяся выше настоящего блока, молча съедала его.

    Регион берётся по ПЕРВОМУ совпадению, поэтому `delivers: []` читался
    как законное «результатов нет», а объявленный ниже DEL-01 не видел
    никто. Канон репо для этого класса — считать число ключей отдельно от
    числа разборов (как у `tdd_waiver`).
    """
    text = _DT_V2.replace("delivers:\n", "delivers: []\ndelivers:\n", 1)
    errors, _ = dt_contract_findings(_V2_FM + text, allow_legacy_dt=False, node_index=_INDEX)
    assert any("delivers" in e and "дважды" in e for e in errors), errors


@pytest.mark.parametrize("kind", ["потому-что-надо", "модуль", ""])
def test_v2_kind_is_a_closed_vocabulary(kind: str) -> None:
    """Открытый словарь превратил бы машинную классификацию в свободный
    текст — тот же провал, от которого репо закрылось `WAIVER_CLASSES`."""
    text = _DT_V2.replace("kind: capability", f"kind: {kind}", 1)
    errors, _ = dt_contract_findings(_V2_FM + text, allow_legacy_dt=False, node_index=_INDEX)
    assert any("kind" in e for e in errors), (kind, errors)


def test_v2_accepts_every_declared_kind() -> None:
    """Негативная половина к словарю: объявленные значения принимаются.

    Без неё «закрытый словарь» удовлетворялся бы словарём из одного
    значения или пустым.
    """
    for kind in DELIVERABLE_KINDS:
        text = _DT_V2.replace("kind: capability", f"kind: {kind}", 1)
        errors, _ = dt_contract_findings(_V2_FM + text, allow_legacy_dt=False, node_index=_INDEX)
        assert errors == [], (kind, errors)


# ── срез 2 #282: delivers доезжает структурой, covered_by, sources ──


def test_parse_dt_tasks_exposes_delivers_records() -> None:
    """Мост обязан получать `delivers` из ТОГО ЖЕ парсера, что и гвард.

    Второй разбор того же текста в мосте был бы вторым вычислителем
    предиката: он разошёлся бы с гвардом молча, и разойтись мог бы как раз
    на том, что гвард признал валидным.
    """
    tasks, findings = parse_dt_tasks(_V2_FM + _DT_V2)

    assert findings == [], findings
    (task,) = tasks
    (deliverable,) = task.delivers
    assert deliverable.id == "DEL-01"
    assert deliverable.kind == "capability"
    assert deliverable.statement == "парсер отвергает дубль ключа"
    assert deliverable.sources == ("acceptance#AC-07",)
    assert deliverable.covered_by is None


_DT_V2_COVERED = (
    "#### DT-01: Парсер · type: implement · owner: dev\n"
    "scenarios: [BEH-01]\n"
    "depends_on: []\n"
    "parallel_group: core\n"
    "delivers:\n"
    "  - id: DEL-01\n"
    "    kind: capability\n"
    "    statement: \"парсер отвергает дубль ключа\"\n"
    "    sources:\n"
    "      - \"acceptance#AC-07\"\n"
    "    covered_by: BEH-01\n"
    "Проза предмета.\n"
)


def test_covered_by_naming_own_scenario_is_accepted() -> None:
    """Базовая половина: объявленная связь с пунктом ЭТОГО DT валидна."""
    errors, warnings = dt_contract_findings(_V2_FM + _DT_V2_COVERED, node_index=_INDEX)

    assert errors == [], errors
    assert warnings == [], warnings
    (task,) = parse_dt_tasks(_V2_FM + _DT_V2_COVERED)[0]
    assert task.delivers[0].covered_by == "BEH-01"


def test_covered_by_naming_a_foreign_scenario_is_an_error() -> None:
    """Связь обязана быть объявлена ПРОВЕРЯЕМО, а не просто записана.

    `covered_by` работает тем, что отменяет создание отдельного пункта.
    Если он называет сценарий, которого у этого DT нет, пункта с такой
    связью в задаче не появится вовсе — результат исчезнет молча, то есть
    ровно тот дефект, против которого заведён #282, только приобретённый
    через сам механизм защиты от него.
    """
    text = _V2_FM + _DT_V2_COVERED.replace("covered_by: BEH-01", "covered_by: BEH-99")

    errors, _ = dt_contract_findings(text, node_index=_INDEX)

    assert any("covered_by" in e and "BEH-99" in e for e in errors), errors


def test_node_ids_extracts_both_heading_forms() -> None:
    """Индекс строится из текста узла — обе живые формы заголовка.

    `#### AC-07: <текст>` (двоеточие) и `#### Q-03 · owner_role: …`
    (интерпункт) — разные DSL разных узлов одного бандла. Экстрактор,
    знающий одну, молча отдал бы пустой индекс для другого узла, и КАЖДАЯ
    ссылка на него стала бы «пункт не найден».
    """
    assert node_ids("#### AC-07: отказ без actor · verification: test\n") == (
        frozenset({"AC-07"})
    )
    assert node_ids(
        "#### Q-03 · owner_role: architects · resolution: resolved\n"
    ) == frozenset({"Q-03"})


def test_sources_pointing_at_a_missing_item_is_an_error() -> None:
    errors, _ = dt_contract_findings(
        _V2_FM + _DT_V2, node_index={"acceptance": {"AC-01"}}
    )

    assert any("AC-07" in e for e in errors), errors


def test_sources_pointing_at_a_missing_node_is_an_error() -> None:
    """Узла нет в бандле — отдельная причина от «пункта нет в узле».

    Одно сообщение на оба случая заставило бы автора искать опечатку в id
    там, где узел не подключён к профилю вовсе.
    """
    errors, _ = dt_contract_findings(_V2_FM + _DT_V2, node_index={})

    assert any("acceptance" in e and "узел" in e.lower() for e in errors), errors


def test_sources_resolved_against_the_index_is_accepted() -> None:
    """Базовая половина: разрешимая ссылка не порождает находок.

    Без неё «ссылка не разрешилась» удовлетворялось бы и проверкой,
    краснеющей на любом входе.
    """
    errors, warnings = dt_contract_findings(
        _V2_FM + _DT_V2, node_index={"acceptance": {"AC-07", "AC-01"}}
    )

    assert errors == [], errors
    assert warnings == [], warnings


def test_deliverable_id_must_follow_the_contract_form() -> None:
    """Находка ревью #290 (major): формы id не проверял никто.

    Гвард объявлен ЕДИНСТВЕННЫМ судьёй формы `delivers`, а мост опознаёт
    результат в чек-листе по контрактной форме `DEL-NN`. Пропусти гвард
    id иной формы — гейт зеленел бы, а доставка падала бы RuntimeError с
    ЛОЖНОЙ причиной «результат не доехал», хотя пункт отрендерен и на
    месте. Судья формы обязан судить форму.
    """
    text = _V2_FM + _DT_V2.replace("id: DEL-01", "id: OUT-01", 1)

    errors, _ = dt_contract_findings(text, node_index=_INDEX)

    assert any("OUT-01" in e and "DEL-" in e for e in errors), errors


def test_contract_form_id_is_accepted() -> None:
    """Базовая половина: контрактная форма проходит.

    Без неё «гвард отвергает чужую форму» удовлетворялось бы и гвардом,
    отвергающим любой id.
    """
    errors, _ = dt_contract_findings(_V2_FM + _DT_V2, node_index=_INDEX)

    assert errors == [], errors


# ── §3b.6: повторное обязательство между задачами ──

_DT_RESTATES = (
    "#### DT-01: Ядро · type: implement · owner: dev\n"
    "scenarios: [BEH-01]\n"
    "depends_on: []\n"
    "parallel_group: core\n"
    "delivers:\n"
    "  - id: DEL-01\n"
    "    kind: capability\n"
    "    statement: \"retention_days ограничен 7-365\"\n"
    "    sources:\n"
    "      - \"acceptance#AC-07\"\n"
    "Проза.\n"
    "\n"
    "#### DT-12: Отчёты · type: implement · owner: dev\n"
    "scenarios: [BEH-02]\n"
    "depends_on: [DT-01]\n"
    "parallel_group: core\n"
    "delivers:\n"
    "  - id: DEL-12\n"
    "    kind: capability\n"
    "    statement: \"retention_days ограничен 7-365\"\n"
    "    sources:\n"
    "      - \"acceptance#AC-07\"\n"
    "    restates: DEL-01\n"
    "Проза.\n"
)


def test_restates_pointing_at_a_dependency_is_accepted() -> None:
    """Базовая половина: объявленный повтор через ребро зависимости валиден.

    Без неё «гвард отвергает битый restates» удовлетворялось бы и гвардом,
    отвергающим любой.
    """
    errors, warnings = dt_contract_findings(
        _V2_FM + _DT_RESTATES, node_index=_INDEX
    )

    assert errors == [], errors
    assert warnings == [], warnings
    tasks, _ = parse_dt_tasks(_V2_FM + _DT_RESTATES)
    assert tasks[1].delivers[0].restates == "DEL-01"


def test_restates_of_an_unknown_deliverable_is_an_error() -> None:
    text = _DT_RESTATES.replace("restates: DEL-01", "restates: DEL-99")

    errors, _ = dt_contract_findings(_V2_FM + text, node_index=_INDEX)

    assert any("DEL-99" in e for e in errors), errors


def test_restates_of_own_deliverable_is_an_error() -> None:
    """Повтор ССЫЛАЕТСЯ на предшествующую задачу, а не на себя.

    Самоссылка объявляла бы задачу повторяющей собственное обязательство —
    пункт превратился бы в «проверить, что сделано в этой же задаче», то
    есть в проверку без исполнителя.

    Утверждение привязано к ПРИЧИНЕ, а не к факту красноты: мутант,
    снимавший эту проверку, ВЫЖИВАЛ на прежней редакции теста. Самоссылка
    попутно нарушает ещё два инварианта — замыкание `depends_on` и запрет
    цепочек, — и тест подтверждался существованием ЧУЖОЙ находки, в
    которой случайно нашлось то же слово.
    """
    text = _DT_RESTATES.replace("restates: DEL-01", "restates: DEL-12")

    errors, _ = dt_contract_findings(_V2_FM + text, node_index=_INDEX)

    assert any("ссылка на себя" in e for e in errors), errors


def test_restates_without_a_dependency_edge_is_an_error() -> None:
    """Без ребра «уже сделано» НЕ гарантировано — пометка была бы ложью.

    Тот же инвариант, что у `delivered_by` и `verifies`: владелец обязан
    лежать в транзитивном замыкании `depends_on`. Иначе задачи могут идти
    параллельно, и исполнитель получит указание проверить результат,
    которого ещё нет.
    """
    text = _DT_RESTATES.replace("depends_on: [DT-01]", "depends_on: []")

    errors, _ = dt_contract_findings(_V2_FM + text, node_index=_INDEX)

    assert any("depends_on" in e for e in errors), errors


def test_chain_of_restates_is_an_error() -> None:
    """Цепочки запрещены: ссылка ведёт на ИСХОДНОЕ обязательство.

    Цепочка рассеивает источник: пункт назвал бы задачу-посредника, а не
    ту, где обязательство действительно исполнено, — и исполнитель пошёл
    бы проверять не туда.
    """
    text = _DT_RESTATES + (
        "\n#### DT-20: Экспорт · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\n"
        "depends_on: [DT-12]\n"
        "parallel_group: core\n"
        "delivers:\n"
        "  - id: DEL-20\n"
        "    kind: capability\n"
        "    statement: \"retention_days ограничен 7-365\"\n"
        "    sources:\n"
        "      - \"acceptance#AC-07\"\n"
        "    restates: DEL-12\n"
        "Проза.\n"
    )

    errors, _ = dt_contract_findings(_V2_FM + text, node_index=_INDEX)

    assert any("цепоч" in e.lower() for e in errors), errors
