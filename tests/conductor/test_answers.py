"""Ответы владельца и производный вопрос (спека среза 1, §6.2; О §5.10)."""

from conductor.actions.answers import (
    answers_and_resolutions,
    canonical,
    derived_question,
    parse_answers,
    resolve,
)

Q = {
    "question_id": "ab12cd34",
    "subject": "a#1",
    "reason": "GR-SHIPPED-OPEN",
    "evidence": "e",
    "question": "a#1: закрыть?",
    "options": ["close", "keep"],
    "default": "keep",
}
OWNER = "own"


def c(cid: int, body: str, author: str = OWNER) -> dict:
    return {
        "id": cid,
        "author": author,
        "body": body,
        "created_at": "t",
        "updated_at": "t",
    }


def test_parse_name_number_foreign_and_stale() -> None:
    comments = [
        c(1, "Q-ab12cd34: close"),
        c(2, "Q-ab12cd34: 2"),
        c(3, "Q-ab12cd34: 9"),  # номер вне диапазона
        c(4, "Q-ab12cd34: close", author="someone"),
        c(5, "Q-ffffffff: close"),  # вопроса нет — устарел
    ]
    got = parse_answers(comments, OWNER, {"ab12cd34": Q})
    assert [(a.comment_id, a.value) for a in got["ab12cd34"]] == [
        (1, "close"),
        (2, "keep"),
    ]
    assert set(got) == {"ab12cd34"}


def test_single_value_is_chosen() -> None:
    by_q = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: 1")], OWNER, {"ab12cd34": Q}
    )
    assert resolve(Q, by_q).chosen == "close"


def test_conflict_makes_derived_question() -> None:
    by_q = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")], OWNER, {"ab12cd34": Q}
    )
    res = resolve(Q, by_q)
    assert res.chosen is None and res.derived is not None
    assert res.derived["options"] == ["close", "keep"]  # keep не дублируется


def test_derived_id_changes_on_any_new_answer_even_same_value() -> None:
    two = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")], OWNER, {"ab12cd34": Q}
    )["ab12cd34"]
    three = (
        two
        + parse_answers([c(3, "Q-ab12cd34: close")], OWNER, {"ab12cd34": Q})["ab12cd34"]
    )
    assert (
        derived_question(Q, two)["question_id"]
        != derived_question(Q, three)["question_id"]
    )
    assert canonical(three) == ["1:close", "2:keep", "3:close"]


def test_confirmation_chooses_and_conflict_on_confirmation_blocks() -> None:
    base = [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")]
    res, open_derived = answers_and_resolutions(base, OWNER, [Q])
    did = res["ab12cd34"].derived["question_id"]
    assert [d["question_id"] for d in open_derived] == [did]
    res, open_derived = answers_and_resolutions(
        base + [c(3, f"Q-{did}: close")], OWNER, [Q]
    )
    assert res["ab12cd34"].chosen == "close" and open_derived == []
    res, _ = answers_and_resolutions(
        base + [c(3, f"Q-{did}: close"), c(4, f"Q-{did}: keep")], OWNER, [Q]
    )
    assert res["ab12cd34"].blocked and res["ab12cd34"].chosen is None


def test_keep_confirmation_executes_nothing() -> None:
    base = [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")]
    did = answers_and_resolutions(base, OWNER, [Q])[0]["ab12cd34"].derived[
        "question_id"
    ]
    res, _ = answers_and_resolutions(base + [c(3, f"Q-{did}: keep")], OWNER, [Q])
    assert res["ab12cd34"].chosen is None and not res["ab12cd34"].blocked
