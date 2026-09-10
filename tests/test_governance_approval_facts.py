"""Типизированные исходы фактов §I12: что установлено, а что нет.

Ось всех тестов файла одна и та же: неустановленный факт НЕ открывает дверь
в терминальный статус. Поэтому почти каждый идёт парой — «вот исход, который
установлен» против «вот внешне такой же, который нет», — иначе assert об
отсутствии вакуумен: он не отличает «классификатор осторожен» от
«классификатор всегда возвращает одно и то же».
"""

from __future__ import annotations

import subprocess

import pytest

from governance import approval_facts as af
from governance import ops as ops_mod
from governance.approval_facts import Disposition, MergeEvent, Outcome
from governance.ops import RealOps


class _StubOps:
    """Заглушка ops: каждый примитив отвечает тем, чем его зарядили.

    Разные заряды дают разные ответы — именно это и делает сравнение
    осмысленным. Стаб, отвечающий одинаково на любой вход, доказывал бы
    только сам себя.
    """

    def __init__(self, **answers: object) -> None:
        self.answers = answers
        self.calls: list[tuple] = []

    def _answer(self, name: str, *args: object) -> object:
        self.calls.append((name, *args))
        value = self.answers[name]
        if isinstance(value, Exception):
            raise value
        return value

    def find_pr(self, repo_slug: str, branch: str, *, any_state: bool = False):
        return self._answer("find_pr", repo_slug, branch, any_state)

    def pr_facts(self, repo_slug: str, pr: int):
        return self._answer("pr_facts", repo_slug, pr)

    def show_file(self, target_dir: str, ref: str, path: str):
        return self._answer("show_file", target_dir, ref, path)

    def close_pr(self, repo_slug: str, pr: int, comment: str):
        return self._answer("close_pr", repo_slug, pr, comment)


def _merged(login: str = "andrei-shtanakov") -> dict:
    return {
        "state": "MERGED",
        "mergedBy": {"login": login},
        "mergedAt": "2026-09-10T08:00:00Z",
        "mergeCommit": {"oid": "abc123"},
    }


# --- Что такое «установленный факт» -------------------------------------


def test_only_unavailable_is_not_established() -> None:
    """Единственная строка, способная молча обнулить всё правило §I12.

    Попади `UNAVAILABLE` в `ESTABLISHED` — терминализация начала бы
    происходить по сетевым сбоям, и снаружи это выглядело бы как
    работающая механика. Перечень поэтому проверяется по ВСЕМУ enum'у, а
    не по паре примеров.
    """
    for outcome in Outcome:
        expected = outcome is not Outcome.UNAVAILABLE
        assert af.Fact(outcome).established is expected, outcome


# --- `None` от двух примитивов — два разных ответа ----------------------


def test_absent_pr_is_established_but_absent_file_is_not() -> None:
    """Одинаковый на вид `None` классифицируется ПО-РАЗНОМУ, и это суть.

    `ops.find_pr` отдаёт `None` ТОЛЬКО когда таких PR нет (сбой запроса он
    поднимает исключением) — значит факт установлен. `ops.show_file`
    отдаёт `None` и когда ревизии нет, и когда файла в ней нет (#177) —
    значит не установлен. Различие живёт в контрактах примитивов, а не в
    «контексте», и ровно его контракт запрещает угадывать.

    Пара обязательна: без первой половины вторая («тут не бывает
    `ABSENT`») ничего не доказывает — `ABSENT` могло бы не бывать нигде.
    """
    ops = _StubOps(find_pr=None, show_file=None)

    absent_pr = af.search_pr(ops, "owner/repo", "spec/ws-approve-1-1-1")
    assert absent_pr.outcome is Outcome.ABSENT
    assert absent_pr.established

    absent_file = af.read_blob_text(ops, "/tmp/t", "master", "spec/20.md")
    assert absent_file.outcome is Outcome.UNAVAILABLE
    assert not absent_file.established
    assert "#177" in absent_file.detail


def test_search_pr_failure_is_unavailable_not_absence() -> None:
    """Сбой запроса — не «PR нет»: иначе повтор открыл бы второй PR."""
    ops = _StubOps(find_pr=RuntimeError("gh pr list rc=1"))
    fact = af.search_pr(ops, "owner/repo", "spec/ws-approve-1-1-1")
    assert fact.outcome is Outcome.UNAVAILABLE
    assert not fact.established


def test_search_pr_found_carries_the_number() -> None:
    ops = _StubOps(find_pr=407)
    fact = af.search_pr(ops, "owner/repo", "spec/ws-approve-1-1-1")
    assert (fact.outcome, fact.value) == (Outcome.FOUND, 407)


# --- Пустой файл — это файл ---------------------------------------------


def test_empty_file_is_found_not_missing() -> None:
    """`""` и `None` — РАЗНЫЕ ответы `show_file`, и сворачивать их нельзя.

    Свернув их проверкой на ложность, реализация завела бы ту же
    эвристику через заднюю дверь: пустой узел читался бы как
    «прочитать не удалось» и вечно оставлял бы заявку живой.
    """
    ops = _StubOps(show_file="")
    fact = af.read_blob_text(ops, "/tmp/t", "master", "spec/20.md")
    assert fact.outcome is Outcome.FOUND
    assert fact.value == ""


def test_read_blob_text_found_carries_the_text() -> None:
    ops = _StubOps(show_file="---\nstatus: approved\n---\n\nтело\n")
    fact = af.read_blob_text(ops, "/tmp/t", "master", "spec/20.md")
    assert fact.outcome is Outcome.FOUND
    assert fact.value is not None and "status: approved" in fact.value


# --- Состояние PR и акт мержа -------------------------------------------


def test_read_pr_failure_is_unavailable() -> None:
    """`gh pr view` роняется одинаково на несуществующий PR и на сеть."""
    ops = _StubOps(pr_facts=RuntimeError("gh: connection reset"))
    fact = af.read_pr(ops, "owner/repo", 407)
    assert fact.outcome is Outcome.UNAVAILABLE


def test_read_pr_found_returns_raw_facts() -> None:
    ops = _StubOps(pr_facts=_merged())
    fact = af.read_pr(ops, "owner/repo", 407)
    assert fact.outcome is Outcome.FOUND
    assert fact.value == _merged()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("OPEN", Disposition.OPEN),
        ("MERGED", Disposition.MERGED),
        ("CLOSED", Disposition.CLOSED_UNMERGED),
    ],
)
def test_disposition_reads_the_state_as_is(state: str, expected) -> None:
    fact = af.disposition({"state": state})
    assert (fact.outcome, fact.value) == (Outcome.FOUND, expected)


def test_unknown_state_is_unavailable_not_open() -> None:
    """Молчаливый дефолт «наверное открыт» и есть запрещённая эвристика."""
    fact = af.disposition({"state": "QUEUED"})
    assert fact.outcome is Outcome.UNAVAILABLE
    assert fact.value is None


def test_closed_unmerged_is_absent_but_open_is_not() -> None:
    """Пара, на которой держится разница `abandoned` и «ждём человека».

    Закрытый без мержа PR — установленное отсутствие акта: человек решил
    не одобрять, заявка терминализуется. Открытый — тоже «мержа нет», но
    ответом на вопрос «состоялся ли акт» это не является: прими его за
    установленное отсутствие, и живая заявка была бы похоронена своим же
    ожиданием.
    """
    closed = af.merge_event({"state": "CLOSED"})
    assert closed.outcome is Outcome.ABSENT
    assert closed.established

    still_open = af.merge_event({"state": "OPEN"})
    assert still_open.outcome is Outcome.UNAVAILABLE
    assert not still_open.established


def test_merge_event_found_carries_login_time_and_commit() -> None:
    fact = af.merge_event(_merged())
    assert fact.outcome is Outcome.FOUND
    assert fact.value == MergeEvent(
        "andrei-shtanakov", "2026-09-10T08:00:00Z", "abc123"
    )


@pytest.mark.parametrize(
    ("missing", "name"),
    [
        ("mergedBy", "mergedBy.login"),
        ("mergedAt", "mergedAt"),
        ("mergeCommit", "mergeCommit.oid"),
    ],
)
def test_incomplete_merge_facts_are_unavailable(missing: str, name: str) -> None:
    """Неполное событие не подписывает узел и не сверяется фазой 3.

    И не терминализует: «часть величин не пришла» — это не прочитанный
    факт о заявке, а неполный ответ форджи.
    """
    facts = {**_merged(), missing: None}
    fact = af.merge_event(facts)
    assert fact.outcome is Outcome.UNAVAILABLE
    assert name in fact.detail


# --- Подпись создаёт только авторизованная учётка ------------------------


def test_allowlist_is_empty_by_default(monkeypatch) -> None:
    """Пустой дефолт значит «подписать не может никто» — fail-closed."""
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    assert af.approver_allowlist() == frozenset()
    fact = af.authorized_signature(
        MergeEvent("andrei-shtanakov", "2026-09-10T08:00:00Z", "abc123")
    )
    assert fact.outcome is Outcome.FORBIDDEN


def test_configured_account_signs_and_review_circuit_never_does(
    monkeypatch,
) -> None:
    """Один и тот же список даёт РАЗНЫЕ ответы на разные учётки.

    Учётка ревью-контура не разрешена и по умолчанию, и при настроенном
    списке: агентский мерж подписи не создаёт, а даёт отказ финализации.
    Это законная дорога в `invalidated` — факт прочитан и однозначен.
    """
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "andrei-shtanakov")
    human = af.authorized_signature(
        MergeEvent("andrei-shtanakov", "2026-09-10T08:00:00Z", "abc123")
    )
    agent = af.authorized_signature(
        MergeEvent("ai-prosto", "2026-09-10T08:00:00Z", "abc123")
    )
    assert human.outcome is Outcome.FOUND
    assert agent.outcome is Outcome.FORBIDDEN
    assert agent.established, "FORBIDDEN — установленный факт, не сбой"
    assert af.APPROVER_ALLOWLIST_ENV in agent.detail


def test_allowlist_drops_empty_items(monkeypatch) -> None:
    """Непустой список случайно не получить: пустые элементы отброшены."""
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, " , ,")
    assert af.approver_allowlist() == frozenset()


# --- Закрытие PR --------------------------------------------------------


def test_unconfirmed_close_is_unavailable_but_confirmed_is_a_fact() -> None:
    """Пара: `False` от `close_pr` неразличим (#177), `True` — установлен."""
    confirmed = af.confirm_closed(_StubOps(close_pr=True), "o/r", 407, "текст")
    assert confirmed.outcome is Outcome.FOUND

    unconfirmed = af.confirm_closed(
        _StubOps(close_pr=False), "o/r", 407, "текст"
    )
    assert unconfirmed.outcome is Outcome.UNAVAILABLE
    assert "#177" in unconfirmed.detail


# --- Граница `find_pr`: characterization против НАСТОЯЩЕГО примитива -----
#
# Владелец 2026-09-10: `None → ABSENT` здесь допустим, потому что это
# ПУБЛИЧНОЕ ПОСТУСЛОВИЕ `find_pr`, а не догадка вызывающего кода. Догадкой
# было бы читать так `show_file` или `close_pr`, у которых постусловие
# свёрнуто. Тесты ниже лочат саму границу: где примитив классифицирует
# исход сам, а где перестаёт.


def _fake_gh(monkeypatch, *, returncode: int, stdout: str) -> None:
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, "boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)


def test_empty_parsed_list_is_the_only_road_to_absent(monkeypatch) -> None:
    """Пустой РАЗОБРАННЫЙ список — единственный источник `ABSENT`.

    Пара обязательна: непустой список даёт `FOUND` с номером, то есть
    примитив различает исходы, а не отвечает одно и то же.
    """
    _fake_gh(monkeypatch, returncode=0, stdout="[]")
    absent = af.search_pr(RealOps(), "owner/repo", "spec/ws-approve-1-1-1")
    assert absent.outcome is Outcome.ABSENT and absent.established

    _fake_gh(monkeypatch, returncode=0, stdout='[{"number": 407}]')
    found = af.search_pr(RealOps(), "owner/repo", "spec/ws-approve-1-1-1")
    assert (found.outcome, found.value) == (Outcome.FOUND, 407)


@pytest.mark.parametrize(
    ("returncode", "stdout", "why"),
    [
        (1, "", "ненулевой код"),
        (0, "не JSON", "битый JSON"),
        (0, '{"number": 1}', "неожиданная форма (объект вместо списка)"),
    ],
)
def test_every_failure_shape_stays_unavailable(
    monkeypatch, returncode: int, stdout: str, why: str
) -> None:
    """Все три формы сбоя примитив поднимает исключением, а не отдаёт `None`.

    Ровно это и делает `ABSENT` выше законным: дорога к `None` одна, и она
    означает «таких PR нет». Сломайся это различие — `search_pr` начал бы
    выдавать установленное отсутствие по сетевой ошибке.
    """
    _fake_gh(monkeypatch, returncode=returncode, stdout=stdout)
    fact = af.search_pr(RealOps(), "owner/repo", "spec/ws-approve-1-1-1")
    assert fact.outcome is Outcome.UNAVAILABLE, why
    assert not fact.established
