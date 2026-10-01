"""Белый список мутаций и фактическая цель (спека среза 1, §4.5, §5.1 шаг 8)."""

from conductor.gh_app import CallResult
from conductor.gh_write import (
    KLASS,
    Mutation,
    check_target,
    request_of,
    verify,
)
from conductor.http import Response
from tests.conductor.fake_app import FakeClient, ok

ALLOWED = {
    ("POST", "/repos/o/r/issues/1/comments"),
    ("POST", "/repos/o/r/issues"),
    ("PATCH", "/repos/o/r/issues/1"),
    ("POST", "/graphql"),
}


def test_whitelist_is_closed() -> None:
    seen = set()
    for op in KLASS:
        number = None if op == "create" else 1
        m = Mutation(op, "o/r", number, text="t", title="q")  # type: ignore[arg-type]
        method, path, _, _ = request_of(m, "I_1")
        seen.add((method, path))
    assert seen == ALLOWED
    assert KLASS == {
        "comment": "create",
        "create": "create",
        "body": "update",
        "close": "update",
        "pin": "update",
    }


def test_close_body_is_completed_only() -> None:
    _, _, body, _ = request_of(Mutation("close", "o/r", 1), None)
    assert body == {"state": "closed", "state_reason": "completed"}


def test_check_target_existing_ok_and_moved() -> None:
    client = FakeClient()
    assert (
        check_target(client, Mutation("comment", "o/r", 1, text="x")).node_id == "I_1"
    )
    client.override[("GET", "/repos/o/r/issues/1")] = CallResult(
        "moved", Response(301, {"location": "/repos/o/z/issues/9"}, b"")
    )
    assert check_target(client, Mutation("comment", "o/r", 1)).reason == "TARGET-MOVED"
    client.override[("GET", "/repos/o/r/issues/1")] = ok(
        {"number": 1, "repository_url": "https://api.github.com/repos/o/other"}
    )
    assert check_target(client, Mutation("comment", "o/r", 1)).reason == "TARGET-MOVED"


def test_check_target_create_checks_repo_name() -> None:
    client = FakeClient()
    assert check_target(client, Mutation("create", "o/r", text="b", title="q")).ok
    client.override[("GET", "/repos/o/r")] = ok({"full_name": "o/renamed"})
    assert not check_target(client, Mutation("create", "o/r")).ok


def _sent(client: FakeClient, m: Mutation, node_id: str | None = None):
    method, path, body, graphql = request_of(m, node_id)
    return client.call(
        KLASS[m.op], method, path, auth="token", body=body, graphql=graphql
    )


def test_verify_rereads_target_for_every_op() -> None:
    bot = "conductor[bot]"
    client = FakeClient()
    comment = Mutation("comment", "o/r", 1, text="x")
    assert verify(client, comment, _sent(client, comment).response, bot) is None
    body = Mutation("body", "o/r", 1, text="b")
    assert verify(client, body, _sent(client, body).response, bot) is None
    close = Mutation("close", "o/r", 1)
    assert verify(client, close, _sent(client, close).response, bot) is None
    pin = Mutation("pin", "o/r", 1)
    assert verify(client, pin, _sent(client, pin, "I_1").response, bot, "I_1") is None
    create = Mutation("create", "o/r", text="q", title="t", labels=("owner-queue",))
    assert verify(client, create, _sent(client, create).response, bot) is None


def test_verify_2xx_without_visible_effect_is_uncertain() -> None:
    """Регрессия P2-1: ответ мутации 2xx эффекта не доказывает."""
    bot = "conductor[bot]"
    client = FakeClient()
    client.apply = False  # GitHub «принял», но чтение эффекта не показывает
    for m, node in (
        (Mutation("comment", "o/r", 1, text="x"), None),
        (Mutation("body", "o/r", 1, text="b"), None),
        (Mutation("close", "o/r", 1), None),
        (Mutation("pin", "o/r", 1), "I_1"),
    ):
        resp = _sent(client, m, node).response
        assert verify(client, m, resp, bot, node) is not None, m.op


def test_verify_unreadable_control_read_is_uncertain() -> None:
    bot = "conductor[bot]"
    client = FakeClient()
    close = Mutation("close", "o/r", 1)
    resp = _sent(client, close).response
    client.override[("GET", "/repos/o/r/issues/1")] = CallResult("uncertain", None)
    assert verify(client, close, resp, bot) == "цель не прочитана после записи"
    assert verify(client, close, None, bot) == "ответ мутации не разобран"


def test_check_target_create_repo_name_is_case_insensitive() -> None:
    """Ревью #538: регистр owner/name в манифесте не делает репо «перенесённым»."""
    client = FakeClient()
    client.override[("GET", "/repos/Own/R")] = ok({"full_name": "own/r"})
    assert check_target(client, Mutation("create", "Own/R", text="b", title="q")).ok
