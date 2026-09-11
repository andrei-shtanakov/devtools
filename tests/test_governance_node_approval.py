"""Предикат честной одобренности узла и его величины (§I12).

Предмет файла — четыре условия предиката и `approved_content_hash`, который
закрывает четвёртое. Условия (3) и (4) отвечают на РАЗНЫЕ половины вопроса
«что покрывает эта подпись»: (3) помнит чужие байты, (4) — свои. Поэтому
каждое проверяется отдельно и на состоянии, где остальные три сходятся, —
иначе тест не отличит «поймало условие (4)» от «поймало что угодно».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import node_approval as na
from governance import task_bridge as tb
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.stale_adapter import blob_sha1

_UPSTREAM = """---
node: requirements
status: approved
version: 2
approved_by: andrei-shtanakov
approved_at: '2026-09-09T10:00:00Z'
---

Требования.
"""


def _node(
    *,
    status: str = "approved",
    approved_by: str = "andrei-shtanakov",
    approved_at: str = "2026-09-10T08:00:00Z",
    version: int = 3,
    pins: dict[str, str] | None = None,
    body: str = "Дизайн.\n",
) -> str:
    """Узел БЕЗ `approved_content_hash` — таким его видит фаза 1.

    Текст собирается литералом, а не тем же рендером, которым его потом
    считает проекция: round-trip разбора и сборки обязан быть частью того,
    что проверяется.
    """
    pin_lines = "".join(
        f"  {up}: {blob}\n" for up, blob in (pins or {}).items()
    )
    upstream = f"upstream_hashes:\n{pin_lines}" if pin_lines else ""
    empty = "''"
    return (
        "---\n"
        "node: design\n"
        f"status: {status}\n"
        f"version: {version}\n"
        f"approved_by: {approved_by or empty}\n"
        f"approved_at: {approved_at or empty}\n"
        f"{upstream}"
        "---\n"
        "\n"
        f"{body}"
    )


def _sign_own_bytes(text: str) -> str:
    """Вписать в узел его собственный self-hash — ровно то, что делает фаза 1."""
    meta, body = split_frontmatter(text)
    meta[na.SELF_HASH_KEY] = na.self_hash(text)
    return join_frontmatter(meta, body)


def _honest_pins() -> dict[str, str]:
    return {"requirements": blob_sha1(_UPSTREAM)}


def _honest_node() -> str:
    return _sign_own_bytes(_node(pins=_honest_pins()))


# --- Что видит и чего не видит self-hash --------------------------------


def test_self_hash_survives_writing_itself_into_the_node() -> None:
    """Поле вырезано из собственной проекции — иначе оно хешировало бы себя.

    Записать значение значит изменить байты, по которым оно посчитано; без
    исключения `approved_content_hash` из проекции ни один узел не сошёлся
    бы с собственной подписью НИКОГДА.
    """
    bare = _node(pins=_honest_pins())
    signed = _sign_own_bytes(bare)
    assert na.self_hash(signed) == na.self_hash(bare)
    assert na.SELF_HASH_KEY in signed


def test_self_hash_ignores_the_whole_approval_envelope() -> None:
    """Конверт целиком снаружи: тот же узел до и после одобрения — тот же хеш.

    На этом стоит и двухфазность (хеш фазы 1 остаётся верным после того,
    как фаза 3 впишет подпись), и каскад (перевод в `stale` меняет только
    `status`, значит запись о покрытых байтах не портится).
    """
    proposed = _node(
        status="approval_pending", approved_by="", approved_at="", version=3
    )
    approved = _node(
        status="approved",
        approved_by="andrei-shtanakov",
        approved_at="2026-09-10T08:00:00Z",
        version=3,
    )
    went_stale = _node(status="stale", version=3)
    assert na.self_hash(proposed) == na.self_hash(approved)
    assert na.self_hash(went_stale) == na.self_hash(approved)


def test_self_hash_sees_the_body_and_the_rest_of_frontmatter() -> None:
    """Пара к предыдущему: хеш вообще что-то различает.

    Без этой половины «конверт не влияет» доказывало бы лишь то, что хеш
    не влияет ни от чего.
    """
    base = _node()
    assert na.self_hash(_node(body="Дизайн, правленый.\n")) != na.self_hash(base)


def test_self_hash_and_i2_canonization_answer_different_questions(
    tmp_path: Path,
) -> None:
    """Две проекции — два разных ответа на одну правку, и слить их нельзя.

    §I2 спрашивает «менялось ли содержание апстрима» и `status` ОСТАВЛЯЕТ
    (иначе слепа к откату узла в `draft` и к волне переодобрения); §I12
    спрашивает «те ли собственные байты» и `status` РЕЖЕТ (иначе одобрение
    байтов не отличить от перезаписи конверта). Здесь меняется РОВНО
    `status`, и величины расходятся по построению.
    """
    dag = (("00-charter.md", ()),)
    bundle = "spec"

    def canon(status: str) -> str:
        root = tmp_path / status
        (root / bundle).mkdir(parents=True)
        text = _node(status=status).replace("node: design", "node: charter")
        (root / bundle / "00-charter.md").write_text(text, encoding="utf-8")
        return tb._canonical_dag_hash(str(root), bundle, dag)

    assert canon("approved") != canon("stale")
    assert na.self_hash(_node(status="approved")) == na.self_hash(
        _node(status="stale")
    )


# --- Каскад: кого он метит, а на ком обрывается -------------------------


def test_approval_pending_is_cascaded_and_never_stops_it() -> None:
    """Долг `approval_pending` объявлен НЕ ПРО ТОТ upstream — каскад идёт.

    Первая редакция §I12 держала его среди останавливающих: предложение,
    посчитанное против прежнего блоба upstream, осталось бы лежать на
    столе mergeable и уже неверным.
    """
    assert na.cascade_marks_stale(na.STATUS_APPROVAL_PENDING)
    assert not na.cascade_stops_at(na.STATUS_APPROVAL_PENDING)


@pytest.mark.parametrize("status", [na.STATUS_DRAFT, na.STATUS_STALE])
def test_debt_statuses_stop_the_cascade(status: str) -> None:
    """Пара к предыдущему: от пометки долгом у них не меняется файл."""
    assert na.cascade_stops_at(status)
    assert not na.cascade_marks_stale(status)


def test_approved_is_cascaded_but_does_not_stop() -> None:
    assert na.cascade_marks_stale(na.STATUS_APPROVED)
    assert not na.cascade_stops_at(na.STATUS_APPROVED)


# --- Предикат: четыре условия -------------------------------------------


def test_honest_node_carries_no_debt() -> None:
    assert na.node_debt("design", _honest_node(), _honest_pins()) is None


def test_root_node_without_upstream_passes_empty_condition() -> None:
    """У корневого узла условие (3) выполнено пусто — предмета нет."""
    root = _sign_own_bytes(_node())
    assert na.node_debt("charter", root, {}) is None


#: Смысл каждого долгового статуса — ЛИТЕРАЛОМ, а не из словаря модуля:
#: сверка величины с её же источником доказывала бы только то, что словарь
#: равен себе. Здесь это второй, независимо записанный ответ на тот же
#: вопрос — из §I12 дословно.
_MEANINGS = {
    "draft": "не одобрялось",
    "stale": "одобрялось, но upstream сдвинулся",
    "approval_pending": "вынесено на одобрение",
}


def test_debt_statuses_are_exactly_the_three_of_the_contract() -> None:
    """Периметр долговых статусов закрыт, и расширить его молча нельзя."""
    assert set(na.DEBT_STATUSES) == set(_MEANINGS)


@pytest.mark.parametrize(("status", "meaning"), sorted(_MEANINGS.items()))
def test_every_debt_status_is_named_with_its_own_meaning(
    status: str, meaning: str
) -> None:
    """Три долговых статуса говорят РАЗНОЕ, и диагностика обязана различать."""
    node = _sign_own_bytes(_node(status=status, pins=_honest_pins()))
    debt = na.node_debt("design", node, _honest_pins())
    assert debt is not None
    assert debt.status == status
    assert debt.reason == meaning
    assert len(set(_MEANINGS.values())) == 3, "смыслы обязаны различаться"


def test_empty_signature_is_not_a_signature() -> None:
    """`approved_by: ""` заводит шаблон бандла — это «не подписано»."""
    node = _sign_own_bytes(
        _node(approved_by="", approved_at="", pins=_honest_pins())
    )
    debt = na.node_debt("design", node, _honest_pins())
    assert debt is not None and "без подписи" in debt.reason


def test_diverged_pin_names_both_values_and_the_order_of_work() -> None:
    """Условие (3): перепиновать молча — и есть дефект spec-runner#410.

    Остальные три условия здесь сходятся: узел `approved`, подписан, его
    self-hash верен. Красит РОВНО разошедшийся пин.
    """
    node = _sign_own_bytes(_node(pins={"requirements": "стар" * 10}))
    debt = na.node_debt("design", node, _honest_pins())
    assert debt is not None
    assert "стар" * 10 in debt.reason
    assert blob_sha1(_UPSTREAM) in debt.reason
    assert "сперва одобрите изменившийся upstream" in debt.procedure


def test_missing_self_hash_is_migration_debt() -> None:
    """Условие (4) ловит узел, у которого первые ТРИ безупречны.

    Ровно в этом состоянии отменённый штамп оставил узлы в base
    (spec-runner#410): статус, подпись и пины согласованы, а собственные
    одобренные байты непроверяемы — поля штамп не писал и писать не мог.
    """
    node = _node(pins=_honest_pins())
    assert na.SELF_HASH_KEY not in node
    debt = na.node_debt("design", node, _honest_pins())
    assert debt is not None
    assert "миграционный долг" in debt.reason
    assert "переодобрение" in debt.procedure


def test_body_edited_after_signing_is_caught_as_reapproval() -> None:
    """Тупик, ради которого условие (4) заведено.

    Correction правит тело `approved`-узла, не трогая frontmatter: пины
    сходятся (upstream не менялся), значит по трёхусловному предикату узел
    «честно одобрен», approve над ним — no-op, каскад никто не запускает, а
    downstream заперты. Четвёртое условие объявляет долг здесь и сейчас.
    """
    signed = _sign_own_bytes(_node(pins=_honest_pins()))
    meta, _ = split_frontmatter(signed)
    edited = join_frontmatter(meta, "Дизайн, правленый correction'ом.\n")
    debt = na.node_debt("design", edited, _honest_pins())
    assert debt is not None
    assert na.SELF_HASH_KEY in debt.reason
    assert "ПЕРЕОДОБРЕНИЕ" in debt.procedure


def test_unknown_status_is_fail_closed() -> None:
    """`invalidated` — статус ЗАЯВКИ; у узла такого статуса не бывает.

    Незнакомое значение не проходит молча: иначе опечатка в frontmatter
    открывала бы доставке дорогу мимо всего предиката.
    """
    node = _sign_own_bytes(_node(status="invalidated", pins=_honest_pins()))
    debt = na.node_debt("design", node, _honest_pins())
    assert debt is not None and "не известен контракту" in debt.reason


# --- Процедура: статус узла на вопрос не отвечает ------------------------


def test_pending_procedure_depends_on_the_ledger_not_on_the_node() -> None:
    """Один и тот же статус узла — две разные процедуры.

    «Ждём мержа вот этого PR» и «заявка терминальна, нужен новый
    candidate» — разные ответы, и различает их только леджер. Какой
    именно PR ждёт — тоже его знание: у заявки их два, и на разных шагах
    ждут разные. Выдумать это по файлу узла нельзя, поэтому ответ
    приходит аргументом.
    """
    node = _sign_own_bytes(
        _node(status=na.STATUS_APPROVAL_PENDING, pins=_honest_pins())
    )
    waiting = na.node_debt(
        "design", node, _honest_pins(), awaiting_merge_pr=407
    )
    orphaned = na.node_debt("design", node, _honest_pins())
    assert waiting is not None and orphaned is not None
    assert "#407" in waiting.procedure
    assert "authorized_approver_accounts" in waiting.procedure
    assert waiting.procedure != orphaned.procedure
    assert orphaned.procedure == na.APPROVE_PROCEDURE


def test_render_names_node_status_reason_and_procedure() -> None:
    debt = na.node_debt("design", _node(pins=_honest_pins()), _honest_pins())
    assert debt is not None
    line = debt.render()
    for part in ("design", "approved", debt.reason, debt.procedure):
        assert part in line
