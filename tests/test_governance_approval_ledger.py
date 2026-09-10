"""Леджер заявок на одобрение узла (§I12): живость, номера, терминальность.

Записи здесь НЕ собираются руками: каждая проходит через те же мутаторы,
которыми её ведёт механика, — иначе тест проверял бы состояние, которого
конвейер никогда не производит. Ровно на этом уже ловились предыдущие круги.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import approval_branches, approval_ledger as al
from governance import bundle_dag as bd
from governance import run_state as rs
from governance.approval_facts import Authorization, MergeEvent

WS_ID = "WS-T1"


@pytest.fixture()
def state(tmp_path: Path, monkeypatch) -> rs.RunState:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path)
    s = rs.new_run(
        subject="одобрение узлов",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id=WS_ID,
        target_dir="/tmp/alpha",
        bundle_dir=f"workstreams/{WS_ID}/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-approve",
    )
    rs.save(s)
    return s


def _start(state: rs.RunState, wave: int, step: int, attempt: int) -> str:
    return al.start_request(
        state,
        WS_ID,
        wave,
        step,
        attempt,
        nodes=["design"],
        content_hashes={"design": "self-hash-1"},
        upstream_pins={"design": {"requirements": "blob-1"}},
    )


def _merge(state: rs.RunState, key: str, login: str = "andrei-shtanakov") -> None:
    al.record_merge(
        state,
        key,
        MergeEvent(login, "2026-09-10T08:00:00Z", "commit-1"),
        Authorization(login, "v1:deadbeef", "AUTHORIZED_APPROVER_ACCOUNTS"),
    )


# --- Write-ahead --------------------------------------------------------


def test_intent_is_on_disk_before_any_effect(state: rs.RunState) -> None:
    """Запись раньше эффекта: иначе работу, оставшуюся после падения,
    некому опознать."""
    key = _start(state, 1, 1, 1)
    on_disk = rs.load("r-approve").ops[key]
    assert on_disk["status"] == al.STATUS_STARTED
    assert on_disk["candidate_pr"] is None and on_disk["head_sha"] is None
    assert on_disk["nodes"] == ["design"]
    assert on_disk["content_hashes"] == {"design": "self-hash-1"}
    assert on_disk["upstream_pins"] == {"design": {"requirements": "blob-1"}}


def test_head_sha_is_durable_before_push(state: rs.RunState) -> None:
    """Единственный факт, по которому работу заявки опознают в ветке."""
    key = _start(state, 1, 1, 1)
    al.record_head_sha(state, key, "cafe" * 10)
    assert rs.load("r-approve").ops[key]["head_sha"] == "cafe" * 10


def test_branch_names_follow_the_ssot_template(
    state: rs.RunState, tmp_path: Path, monkeypatch
) -> None:
    """Имена выведены из шаблона, а не написаны литералом.

    Проверка идёт подменой САМОГО SSOT: другой шаблон — другие имена. Тест,
    сверяющий имя с вызовом того же построителя, доказывал бы лишь, что
    построитель равен себе.
    """
    default_key = _start(state, 2, 3, 4)
    default_branch = state.ops[default_key]["branch"]
    assert default_branch == f"spec/{WS_ID}-approve-2-3-4"

    patterns = tmp_path / "patterns.env"
    patterns.write_text(
        "APPROVAL_CANDIDATE_TEMPLATE=node/{ws_id}/w{wave}/k{step}/a{attempt}\n"
        "APPROVAL_FINALIZE_SUFFIX=.final\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(approval_branches, "PATTERNS_PATH", patterns)
    other_key = _start(state, 2, 3, 5)
    assert state.ops[other_key]["branch"] == f"node/{WS_ID}/w2/k3/a5"
    assert state.ops[other_key]["finalize_branch"] == f"node/{WS_ID}/w2/k3/a5.final"


def test_finalize_branch_is_the_candidate_plus_suffix(state: rs.RunState) -> None:
    key = _start(state, 1, 1, 1)
    op = state.ops[key]
    assert op["finalize_branch"] == f"{op['branch']}-final"


# --- Живость: следующий шаг, а не статус файла узла ----------------------


def test_next_step_walks_the_resume_table(state: rs.RunState) -> None:
    """Каждая строка таблицы возобновления §I12 — свой шаг, и он выводится
    из записи, а не из того, что видно снаружи."""
    key = _start(state, 1, 1, 1)
    assert al.next_step(state.ops[key]) is al.Step.CREATE_CANDIDATE

    al.record_head_sha(state, key, "cafe" * 10)
    assert al.next_step(state.ops[key]) is al.Step.CREATE_CANDIDATE, (
        "коммит без PR оставляет шаг создания — это и есть крэш-окно"
    )

    al.record_candidate_pr(state, key, 407)
    assert al.next_step(state.ops[key]) is al.Step.AWAIT_CANDIDATE_MERGE

    _merge(state, key)
    assert al.next_step(state.ops[key]) is al.Step.FINALIZE
    assert al.is_live(state.ops[key]), (
        "вмерженный candidate заявку НЕ закрывает: следующий шаг есть, "
        "и это финализация"
    )

    al.record_finalize_pr(state, key, 408)
    assert al.next_step(state.ops[key]) is al.Step.AWAIT_FINALIZE_MERGE
    assert al.is_live(state.ops[key]), "финализирующий PR открыт — жива всегда"

    al.complete_request(state, key)
    assert al.next_step(state.ops[key]) is None
    assert not al.is_live(state.ops[key])


def test_merge_facts_come_from_the_forge_event(state: rs.RunState) -> None:
    key = _start(state, 1, 1, 1)
    al.record_candidate_pr(state, key, 407)
    _merge(state, key)
    on_disk = rs.load("r-approve").ops[key]
    assert on_disk["merged_by"] == "andrei-shtanakov"
    assert on_disk["merged_at"] == "2026-09-10T08:00:00Z"
    assert on_disk["merge_commit"] == "commit-1"
    assert on_disk["authorization"] == {
        "login": "andrei-shtanakov",
        "policy": "v1:deadbeef",
        "source": "AUTHORIZED_APPROVER_ACCOUNTS",
    }, "решение об авторизации записано ТЕМ ЖЕ write'ом, что факты мержа"


# --- Терминальность -----------------------------------------------------


def test_abandoned_and_invalidated_say_different_things(
    state: rs.RunState,
) -> None:
    """Оба терминальны, но различие журнальное и обязано сохраниться.

    `abandoned` — человек закрыл candidate, то есть РЕШИЛ не одобрять;
    `invalidated` — заявку сделали неисполнимой факты, решения человека в
    ней нет.
    """
    closed = _start(state, 1, 1, 1)
    al.record_candidate_pr(state, closed, 407)
    al.abandon_request(state, closed, "candidate закрыт без мержа")

    killed = _start(state, 1, 1, 2)
    al.record_candidate_pr(state, killed, 409)
    al.invalidate_request(
        state, killed, "мерж от ai-prosto — вне allowlist", by="approve-1-1-3"
    )

    on_disk = rs.load("r-approve").ops
    assert on_disk[closed]["status"] == al.STATUS_ABANDONED
    assert on_disk[killed]["status"] == al.STATUS_INVALIDATED
    assert on_disk[killed]["invalidated_by"] == "approve-1-1-3"
    assert "ai-prosto" in on_disk[killed]["reason"]
    assert not al.is_live(on_disk[closed]) and not al.is_live(on_disk[killed])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s, k: al.record_candidate_pr(s, k, 999),
        lambda s, k: al.record_head_sha(s, k, "dead" * 10),
        lambda s, k: al.complete_request(s, k),
        lambda s, k: al.invalidate_request(s, k, "передумали"),
    ],
)
def test_terminal_record_is_not_mutated(state: rs.RunState, mutate) -> None:
    """§I4: причина, по которой заявку похоронили, — единственный след решения.

    Отказ идёт на ЛЮБОМ терминальном статусе, а не только на `completed`:
    иначе повторный вызов перезаписал бы `reason`, а `save` пишет `run.json`
    целиком.
    """
    key = _start(state, 1, 1, 1)
    al.abandon_request(state, key, "человек закрыл candidate")
    with pytest.raises(RuntimeError, match="терминальна"):
        mutate(state, key)
    assert rs.load("r-approve").ops[key]["reason"] == "человек закрыл candidate"


def test_missing_request_is_named(state: rs.RunState) -> None:
    with pytest.raises(RuntimeError, match="нет в леджере"):
        al.record_candidate_pr(state, "approve-9-9-9", 1)


# --- Номера: волна, уровень, попытка ------------------------------------


def test_attempt_numbers_are_never_reused(state: rs.RunState) -> None:
    """Петля, которую закрывает `A`: без него ветка новой заявки называлась
    бы как ветка похороненной, и усыновление читало бы факты чужого мержа.
    """
    first = _start(state, 1, 2, al.next_attempt(state, 1, 2))
    al.record_candidate_pr(state, first, 407)
    al.invalidate_request(state, first, "merger вне allowlist")

    second_attempt = al.next_attempt(state, 1, 2)
    assert second_attempt == 2
    second = _start(state, 1, 2, second_attempt)
    assert state.ops[second]["branch"] != state.ops[first]["branch"]

    with pytest.raises(RuntimeError, match="номера не переиспользуются"):
        _start(state, 1, 2, 1)


def test_attempts_are_counted_per_step(state: rs.RunState) -> None:
    """Пара к предыдущему: соседний уровень начинает счёт заново."""
    _start(state, 1, 2, 1)
    assert al.next_attempt(state, 1, 2) == 2
    assert al.next_attempt(state, 1, 3) == 1


def _open_wave(state: rs.RunState, wave: int, *nodes: str) -> None:
    al.open_wave_record(
        state, wave, nodes, bd.composition_fingerprint(nodes)
    )


def test_wave_outlives_the_death_of_all_its_requests(
    state: rs.RunState,
) -> None:
    """Открытость волны — её ЗАПИСЬ, а не состояние заявок (§I12).

    Между вмерженным уровнем и заведённым следующим живой заявки нет ни
    одной, и волна обязана это пережить: закройся она там, `K` не рос бы
    никогда, а `A` не рос бы вовсе. Здесь тот же промежуток доведён до
    предела — заявок нет ни одной живой, — и волна всё равно открыта.
    """
    _open_wave(state, 1, "charter", "requirements")
    key = _start(state, 1, 1, 1)
    al.record_candidate_pr(state, key, 407)
    _merge(state, key)
    al.record_finalize_pr(state, key, 408)
    al.complete_request(state, key)

    assert al.live_requests(state) == [], "живых заявок не осталось"
    assert al.open_wave(state) == 1, "волна жива: проход не завершён"
    assert al.next_attempt(state, 1, 1) == 2, "восстановление внутри той же W"


def test_wave_numbers_come_from_wave_records(state: rs.RunState) -> None:
    """«Максимальный записанный плюс один» читается по записям волн.

    Без durable-записи читать нечего, а вывод из заявок закрывал бы волну
    ровно в промежутке между уровнями.
    """
    assert al.next_wave(state) == 1 and al.open_wave(state) is None
    _open_wave(state, 1, "charter")
    assert al.next_wave(state) == 2

    al.complete_wave(state, 1)
    assert al.open_wave(state) is None
    assert al.next_wave(state) == 2, "номер закрытой волны не переиспользуется"
    with pytest.raises(RuntimeError, match="не переиспользуются"):
        _open_wave(state, 1, "charter")


def test_two_open_waves_are_a_refusal_not_a_choice(state: rs.RunState) -> None:
    """Молчаливый выбор старшей оставил бы вторую висеть незамеченной."""
    _open_wave(state, 1, "charter")
    _open_wave(state, 2, "charter")
    with pytest.raises(RuntimeError, match="открытых волн больше одной"):
        al.open_wave(state)


@pytest.mark.parametrize(
    "close",
    [
        lambda st: al.complete_wave(st, 1),
        lambda st: al.obsolete_wave(st, 1, ("x",), "v1:x", "состав другой"),
    ],
)
def test_closed_wave_is_not_mutated(state: rs.RunState, close) -> None:
    """§I4: запись волны после записи неприкосновенна — обоими исходами.

    На этом же держится идемпотентность реконсиляции: второй заход в
    записанную судьбу не пишет ничего.
    """
    _open_wave(state, 1, "charter")
    close(state)
    before = dict(al.wave_records(state)[1])
    for again in (
        lambda st: al.complete_wave(st, 1),
        lambda st: al.obsolete_wave(st, 1, ("y",), "v1:y", "ещё раз"),
    ):
        with pytest.raises(RuntimeError, match="уже закрыта"):
            again(state)
    assert al.wave_records(rs.load("r-approve"))[1] == before


def test_obsolete_keeps_both_compositions(state: rs.RunState) -> None:
    """Оба состава и причина — иначе запись говорит «стало иначе», не
    говоря, чем было и чем стало."""
    _open_wave(state, 1, "charter", "requirements")
    actual = ("charter", "requirements", "design")
    al.obsolete_wave(
        state, 1, actual, bd.composition_fingerprint(actual), "появился узел"
    )
    record = rs.load("r-approve").ops["approve-wave-1"]
    assert record["status"] == al.WAVE_OBSOLETE
    assert record["intent"]["dag"] == ["charter", "requirements"]
    assert record["actual"]["dag"] == list(actual)
    assert record["actual"]["fingerprint"] != record["intent"]["fingerprint"]
    assert record["reason"] == "появился узел"


def test_foreign_op_keys_are_not_read_as_requests(state: rs.RunState) -> None:
    """Ключ соседней операции заявкой не считается: разбор строгий."""
    state.ops["approve-node-cache"] = {"status": "completed"}
    _start(state, 1, 1, 1)
    assert [nums for nums, _ in al.requests(state)] == [(1, 1, 1)]


# --- Две живые заявки над узлом ------------------------------------------


def test_terminal_request_over_a_node_does_not_block_a_new_one(
    state: rs.RunState,
) -> None:
    """Запрет §I12 — про ЖИВЫЕ заявки, и только про них.

    Первая редакция запрещала candidate над любым узлом в
    `approval_pending` и тем запирала воркстрим: восстановление после
    смерти заявки становилось невозможным.
    """
    first = _start(state, 1, 1, 1)
    found = al.live_request_over(state, "design")
    assert found is not None and found[0] == (1, 1, 1)
    assert al.live_request_over(state, "acceptance") is None

    al.invalidate_request(state, first, "self-hash разошёлся")
    assert al.live_request_over(state, "design") is None


def test_fingerprint_ignores_order_but_not_membership() -> None:
    """Состав — МНОЖЕСТВО узлов: перестановка не смена состава, а узел — да."""
    assert bd.composition_fingerprint(("a", "b")) == bd.composition_fingerprint(
        ("b", "a")
    )
    assert bd.composition_fingerprint(("a", "b")) != bd.composition_fingerprint(
        ("a", "b", "c")
    )


def test_wave_record_is_not_read_as_a_request(state: rs.RunState) -> None:
    """Запись о волне не имеет формы заявки и заявкой не считается."""
    _open_wave(state, 3, "charter")
    _start(state, 3, 0, 1)
    assert [nums for nums, _ in al.requests(state)] == [(3, 0, 1)]
