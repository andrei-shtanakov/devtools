"""close_shipped: свежая сверка периода и основания перед каждым шагом (§7.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.close import SHIPPED, QueueRef, plan_close
from conductor.actions.owner_queue import make_question, queue_questions
from conductor.markers import h1, make, parse_body, with_marker
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, world
from tests.conductor.slice1_world import (
    UMB,
    World,
    commit,
    outcomes,
)

CLOSE_TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:a#1\n"
}
# вопрос о PR, влитом не в ветку по умолчанию: основание (б) — ответ владельца
Q = make_question("a#1", SHIPPED, "a!5 влит|period:0", "закрыть?", ("close", "keep"))


def extras(closed_by: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": "0",
            "period_start": "2026-09-01T00:00:00Z",
            "closed_by": closed_by,
        }
    }


MERGED = {
    "repo": "a",
    "number": 5,
    "merged": True,
    "merged_at": "2026-09-10T00:00:00Z",
    "merge_sha": "m5",
    "base_is_default": True,
}


def close_world(tmp: Path, by_answer: bool) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT)
    w.client.add_comment(UMB, 9, "own", f"Q-{Q['question_id']}: close")
    w.client.pull("own/a", 5, merged=True, merge_commit_sha="m5", closing=["own/a#1"])
    result, inp = world(
        CLOSE_TODOS,
        [record("a", 1)],
        issue_extras=extras([] if by_answer else [MERGED]),
    )
    notes: list[dict[str, Any]] = []
    answers = {"a#1": Q} if by_answer else {}
    queue = QueueRef(UMB, 9, "own")
    recs = plan_close(result, inp, BOT, answers, w.fresh, notes, queue)
    assert [[s.mutation.op for s in r.steps] for r in recs] == [["comment", "close"]]
    return w, recs


@pytest.mark.parametrize("by_answer", [True, False])
def test_close_twin_closes(tmp_path: Path, by_answer: bool) -> None:
    w, recs = close_world(tmp_path, by_answer)
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2
    assert w.client.issue("own/a", 1)["state"] == "closed"


def _reopen(w: World) -> None:
    w.client.issue("own/a", 1, timeline=[{"event": "reopened", "node_id": "RE_9"}])


def _drop_answer(w: World) -> None:
    w.client.issue(UMB, 9)["comments"].clear()


def _conflict(w: World) -> None:
    w.client.add_comment(UMB, 9, "own", f"Q-{Q['question_id']}: keep")


def _unlink_pr(w: World) -> None:
    w.client.pull("own/a", 5, closing=[])


@pytest.mark.parametrize(
    ("by_answer", "change", "reason"),
    [
        (True, _drop_answer, "ответ изменился"),
        (True, _conflict, "ответ изменился"),
        (True, _reopen, "период изменился"),
        (False, _reopen, "период изменился"),
        (
            False,
            lambda w: w.client.pulls.clear(),
            "основание (а): PR основания не прочитан",
        ),
        (False, _unlink_pr, "основание (а): PR больше не закрывает issue"),
        (True, _unlink_pr, "PR больше не закрывает issue"),
    ],
)
def test_close_removed_before_first_step(
    tmp_path: Path, by_answer: bool, change, reason
) -> None:
    w, recs = close_world(tmp_path, by_answer)
    change(w)
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports) == [("removed", reason), ("skipped_dependent", "removed")]
    assert w.client.sent == []


@pytest.mark.parametrize(
    ("by_answer", "change", "reason"),
    [
        (True, _drop_answer, "ответ изменился"),
        (True, _reopen, "период изменился"),
        (False, _reopen, "период изменился"),
    ],
)
def test_close_removed_between_steps(
    tmp_path: Path, by_answer: bool, change, reason
) -> None:
    """Комментарий отправлен, затем основание изменилось — закрытия нет."""
    w, recs = close_world(tmp_path, by_answer)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [("success", ""), ("removed", reason)]
    assert [s[0] for s in w.client.sent] == ["POST"]
    assert w.client.issue("own/a", 1)["state"] == "open"


def test_close_removed_when_issue_closed_meanwhile(tmp_path: Path) -> None:
    w, recs = close_world(tmp_path, by_answer=False)
    w.client.issue("own/a", 1, state="closed", state_reason="completed")
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports)[0] == ("removed", "субъект закрыт")
    assert w.client.sent == []


def test_close_comment_removed_when_marker_appeared(tmp_path: Path) -> None:
    """Маркер уже есть по свежему чтению — комментарий снят (и закрытие —
    до следующего прогона, который спланирует только шаг 2)."""
    w, recs = close_world(tmp_path, by_answer=False)
    w.client.add_comment("own/a", 1, BOT, recs[0].steps[0].mutation.text)
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports)[0] == ("removed", "маркер уже есть")
    assert w.client.sent == []


def _delete_confirmation(w: World) -> None:
    w.client.issue("own/a", 1)["comments"].clear()


def _edit_confirmation(w: World) -> None:
    w.client.issue("own/a", 1)["comments"][-1]["updated_at"] = "2026-10-02T00:00:00Z"


@pytest.mark.parametrize("by_answer", [True, False])
@pytest.mark.parametrize("change", [_delete_confirmation, _edit_confirmation])
def test_close_needs_valid_confirmation(
    tmp_path: Path, by_answer: bool, change
) -> None:
    """F2: подтверждение удалено или отредактировано после шага 1 — закрытия нет."""
    w, recs = close_world(tmp_path, by_answer)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [
        ("success", ""),
        ("removed", "подтверждения в треде нет"),
    ]
    assert w.client.issue("own/a", 1)["state"] == "open"


def test_close_completion_from_previous_run_needs_confirmation(tmp_path: Path) -> None:
    """F2, доведение: маркер был при планировании (план — только шаг 2), но
    исчез до отправки или в треде маркер другого доказательства."""
    w, recs = close_world(tmp_path, by_answer=False)
    text = recs[0].steps[0].mutation.text
    only_close = PlanRecord(
        recs[0].action,
        recs[0].subject,
        recs[0].revision,
        1,
        recs[0].steps[1:],
        recs[0].revalidate,
        recs[0].authority,
    )
    planned = parse_body(text)
    assert planned is not None
    other = make(
        "close",
        node=planned.get("node"),
        period=planned.get("period"),
        evidence=h1("evidence", "merged:другой"),
    )
    w.client.add_comment("own/a", 1, BOT, with_marker("x", other))
    assert outcomes(w.run([only_close], "close_shipped")) == [
        ("removed", "подтверждения в треде нет")
    ]
    w.client.add_comment("own/a", 1, BOT, text)
    assert outcomes(w.run([only_close], "close_shipped")) == [("success", "")]


DONE = "- [x] g @owner:github:own @id:goal @epic:eco.focus1\n"


HEADER = "slug: goal\nfrom: devtools\n"


def legacy_world(
    tmp: Path, labels: tuple[str, ...] = ("inbox",)
) -> tuple[World, list[PlanRecord]]:
    """Legacy-заявка a#1 склеена со slug goal (метка inbox или распознанная
    шапка протокола без метки — правила ядра); пункт выполнен — ядро задаёт
    GR-SHIPPED-OPEN, владелец ответил close (основание (б))."""
    w = World(tmp)
    commit(tmp / "a", DONE)
    body = HEADER
    w.client.issue("own/a", 1, body=body, labels=list(labels))
    result, inp = world(
        {"a": DONE},
        [record("a", 1, labels=list(labels), body=body)],
        issue_extras=extras([]),
    )
    q = next(q for q in queue_questions(result, inp, []) if q["reason"] == SHIPPED)
    w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT)
    w.client.add_comment(UMB, 9, "own", f"Q-{q['question_id']}: close")
    recs = plan_close(
        result, inp, BOT, {"a#1": q}, w.fresh, [], QueueRef(UMB, 9, "own")
    )
    assert len(recs) == 1
    return w, recs


@pytest.mark.parametrize("labels", [("inbox",), ()])
def test_legacy_twin_closes(tmp_path: Path, labels: tuple[str, ...]) -> None:
    """Двойники: заявка с меткой inbox и корректная шапка без метки."""
    w, recs = legacy_world(tmp_path, labels)
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


def test_label_removed_but_header_kept_still_closes(tmp_path: Path) -> None:
    """Ядро признаёт и шапку без метки: снятие одной метки склейку не снимает."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, labels=[])
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


def _unrecognize(w: World) -> None:
    """Метка inbox и поле from сняты, slug остался — ядро склейку не видит."""
    w.client.issue("own/a", 1, body="slug: goal\n", labels=[])


def test_core_no_longer_glues_after_unrecognize() -> None:
    body = "slug: goal\n"
    result, _ = world({"a": DONE}, [record("a", 1, body=body, labels=[])])
    assert not any(f.code == SHIPPED for f in result.findings)


def _retract(w: World) -> None:
    commit(w.tmp / "a", DONE.replace("[x]", "[ ]"), "retract completion")


def _relink(w: World) -> None:
    w.client.issue("own/a", 1, body="slug: other\nfrom: devtools\n")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_retract, "пункт больше не выполнен"),
        (_relink, "склейка заявки с пунктом снята"),
        (_unrecognize, "склейка заявки с пунктом снята"),
    ],
)
def test_legacy_basis_rechecked_before_first_step(
    tmp_path: Path, change, reason
) -> None:
    """F1: ответ close не заменяет доказательство — снятие [x] или склейки
    до отправки снимает запись (воспроизведение владельца)."""
    w, recs = legacy_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "close_shipped"))[0] == ("removed", reason)
    assert w.client.sent == [] and w.client.issue("own/a", 1)["state"] == "open"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_retract, "пункт больше не выполнен"),
        (_relink, "склейка заявки с пунктом снята"),
        (_unrecognize, "склейка заявки с пунктом снята"),
    ],
)
def test_legacy_basis_rechecked_between_steps(tmp_path: Path, change, reason) -> None:
    w, recs = legacy_world(tmp_path)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [("success", ""), ("removed", reason)]
    assert w.client.issue("own/a", 1)["state"] == "open"


CHANGED = {
    "delete": "# пунктов нет\n",
    "rename": DONE.replace("@id:goal", "@id:other"),
    "backtick": DONE.replace("@id:goal", "`@id:goal`"),
}


@pytest.mark.parametrize("labels", [("inbox",), ()], ids=["inbox", "header"])
@pytest.mark.parametrize("between", [False, True], ids=["before", "between"])
@pytest.mark.parametrize("change", sorted(CHANGED))
def test_item_identity_rechecked_canonically(
    tmp_path: Path, labels: tuple[str, ...], between: bool, change: str
) -> None:
    """Раунд 3 (Codex): пункт исчез по каноническому разбору — удалён,
    переименован или `@id` в бэктиках; снимок прогона его помнит, но ядро
    на свежем TODO не видит ни пункта, ни склейки — записи нет."""
    w, recs = legacy_world(tmp_path, labels)
    text = CHANGED[change]
    rebuilt, _ = world({"a": text}, [record("a", 1, labels=list(labels), body=HEADER)])
    assert "todo://a/goal" not in rebuilt.graph.nodes
    assert not rebuilt.graph.out("a#1", "accepted_as")

    def remove() -> None:
        commit(tmp_path / "a", text, "identity gone")

    if not between:
        remove()
    reports = w.run(recs, "close_shipped", between=remove if between else None)
    assert ("removed", "пункта больше нет") in outcomes(reports)
    assert w.client.issue("own/a", 1)["state"] == "open"
    assert [s[0] for s in w.client.sent] == (["POST"] if between else [])


@pytest.mark.parametrize(
    "body",
    [
        "slug: goal\n",
        "описание\nslug: goal\nfrom: devtools\n",
        "slug: goal\nfrom: unknown-repo\n",
    ],
)
def test_header_without_label_must_be_valid(tmp_path: Path, body: str) -> None:
    """Без метки ядро требует полную шапку с известным отправителем."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, body=body, labels=[])
    rebuilt, _ = world({"a": DONE}, [record("a", 1, body=body, labels=[])])
    assert not rebuilt.graph.out("a#1", "accepted_as")
    assert outcomes(w.run(recs, "close_shipped"))[0] == (
        "removed",
        "склейка заявки с пунктом снята",
    )


def test_inbox_label_does_not_require_header(tmp_path: Path) -> None:
    """С меткой inbox ядро склеивает и по одному slug — двойник."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, body="slug: goal\n", labels=["inbox"])
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


@pytest.mark.parametrize("labels", [("inbox",), ()], ids=["inbox", "header"])
@pytest.mark.parametrize("between", [False, True], ids=["before", "between"])
def test_duplicate_id_refuses_write(
    tmp_path: Path, labels: tuple[str, ...], between: bool
) -> None:
    """Раунд 4 (Codex, R4-1): `@id` дважды — ядро берёт последний (открытый)
    узел; неоднозначная идентичность снимает запись, а не выбирает первый."""
    w, recs = legacy_world(tmp_path, labels)
    text = DONE + DONE.replace("[x]", "[ ]")
    rebuilt, _ = world({"a": text}, [record("a", 1, labels=list(labels), body=HEADER)])
    assert rebuilt.graph.nodes["todo://a/goal"].is_open

    def change() -> None:
        commit(tmp_path / "a", text, "duplicate id")

    if not between:
        change()
    reports = w.run(recs, "close_shipped", between=change if between else None)
    assert ("removed", "идентичность пункта неоднозначна (@id дважды)") in outcomes(
        reports
    )
    assert w.client.issue("own/a", 1)["state"] == "open"
