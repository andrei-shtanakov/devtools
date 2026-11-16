"""Ручные проверки в конце прогона dfl (#771): сбор, тексты, публикация."""

from __future__ import annotations

from governance import manual_checks as mc

BEH = """## Проверки

#### BEH-01: add дописывает заметку
- **checked_by**: `kind: pytest` `target: tests/test_add.py::test_add`

#### BEH-02: порог надёжности ≥ 9/10
- **checked_by**: `kind: manual` `target: docs/reliability.md`

#### BEH-03: ручной снятый
- **checked_by**: `kind: manual` `target: docs/x.md` `status: waived`
"""

DT = """## Задачи

#### DT-01: Команда add · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo

#### DT-02: Надёжность · type: verify · owner: qa
scenarios: [BEH-02, BEH-03]
depends_on: [DT-01]
parallel_group: solo

#### DT-03: Ещё раз надёжность · type: verify · owner: qa
scenarios: [BEH-02]
depends_on: [DT-02]
parallel_group: solo
"""

CHECK = mc.ManualCheck("BEH-02", "порог надёжности ≥ 9/10", "TASK-002", "docs/r.md")


def test_collect_manual_with_task_numbers() -> None:
    assert mc.collect(BEH, DT) == [
        mc.ManualCheck(
            "BEH-02", "порог надёжности ≥ 9/10", "TASK-002", "docs/reliability.md"
        )
    ]


def test_collect_skips_waived_beh() -> None:
    assert "BEH-03" not in [c.beh_id for c in mc.collect(BEH, DT)]


def test_collect_one_question_per_beh() -> None:
    assert [c.beh_id for c in mc.collect(BEH, DT)] == ["BEH-02"]


def test_collect_none_when_no_manual() -> None:
    text = BEH.split("#### BEH-02")[0]
    dt = DT.split("#### DT-02")[0]
    assert mc.collect(text, dt) == []


def test_question_form() -> None:
    assert mc.question(CHECK) == (
        "Ручная проверка BEH-02 (TASK-002): «порог надёжности ≥ 9/10»\n"
        "шаги: docs/r.md\n"
        "1. выполнено · 2. не выполнено · 3. принять с оговоркой"
    )
    bare = mc.ManualCheck("BEH-02", "t", "TASK-002", None)
    assert mc.question(bare).splitlines() == [
        "Ручная проверка BEH-02 (TASK-002): «t»",
        "1. выполнено · 2. не выполнено · 3. принять с оговоркой",
    ]


NOT_DONE = {"verdict": mc.NOT_DONE, "note": "5/10", "at": "2026-11-16", "issue": 24}
CAVEAT = {"verdict": mc.CAVEAT, "note": "до SDK", "at": "2026-11-16", "issue": None}
DONE = {"verdict": mc.DONE, "note": "", "at": "2026-11-16", "issue": None}


def test_issue_body_starts_with_marker() -> None:
    body = mc.issue_body(CHECK, NOT_DONE, run_id="R1", pr_url="https://x/pull/9")
    assert body.splitlines()[0] == mc.issue_marker("R1", "BEH-02")
    assert mc.issue_marker("R1", "BEH-02") == (
        "<!-- dfl-manual-check run=R1 beh=BEH-02 -->"
    )
    assert "5/10" in body and "https://x/pull/9" in body and "TASK-002" in body
    assert mc.issue_title(CHECK) == (
        "Ручная проверка не выполнена: BEH-02 — порог надёжности ≥ 9/10"
    )


def test_pr_comment_has_marker_and_rows() -> None:
    text = mc.pr_comment([CHECK], {"BEH-02": NOT_DONE}, run_id="R1")
    assert text.splitlines()[0] == "<!-- dfl-manual-checks run=R1 -->"
    assert "| BEH | задача | ответ | оговорка / комментарий | issue |" in text
    assert "| BEH-02 | TASK-002 | не выполнено | 5/10 | #24 |" in text


B1 = mc.ManualCheck("BEH-01", "a", "TASK-001", None)


def test_summary_empty_when_all_done() -> None:
    assert mc.summary([CHECK], {"BEH-02": DONE}) == ""


def test_summary_not_done_and_caveat() -> None:
    answers = {"BEH-02": {**NOT_DONE, "issue": 24}, "BEH-01": CAVEAT}
    assert mc.summary([B1, CHECK], answers) == (
        "ручные проверки — не выполнено: 1 (BEH-02 → issue #24); с оговоркой: 1"
    )


def test_summary_closed_issue_reads_done_later() -> None:
    got = mc.summary([CHECK], {"BEH-02": NOT_DONE}, closed=frozenset({24}))
    assert got == "ручные проверки — выполнено позже: 1 (BEH-02, issue #24 закрыт)"


def test_pending() -> None:
    assert mc.pending(False, None) is False
    assert mc.pending(True, None) is True
    assert mc.pending(True, {"status": "started"}) is True
    assert mc.pending(True, {"status": "completed"}) is False


def test_unanswered_reads_record() -> None:
    record = {
        "checks": [mc.as_dict(B1), mc.as_dict(CHECK)],
        "answers": {"BEH-01": DONE},
    }
    assert mc.unanswered(record) == [CHECK]


# --- публикация (Task 3) ----------------------------------------------------


class _Comment:
    def __init__(self, body: str) -> None:
        self.body = body


class _Ops:
    def __init__(self, found: int | None = None, comments: list[str] = ()) -> None:
        self.found, self.comments = found, list(comments)
        self.calls: list[tuple] = []

    def find_issue(self, slug, prefix, *, labels=("inbox",), state="open", author=None):
        self.calls.append(("find_issue", prefix, labels, state, author))
        return self.found

    def create_issue(self, slug, title, body, *, labels=("inbox",)):
        self.calls.append(("create_issue", title, labels))
        return 24

    def pr_comments_fact(self, slug, pr):
        from governance.facts import Fact, Outcome

        return Fact(Outcome.FOUND, [_Comment(b) for b in self.comments])

    def comment(self, slug, pr, body):
        self.calls.append(("comment", pr, body.splitlines()[0]))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _record(*answers: tuple[mc.ManualCheck, dict]) -> dict:
    return {
        "status": "started",
        "checks": [mc.as_dict(c) for c, _a in answers],
        "answers": {c.beh_id: dict(a) for c, a in answers},
        "commented": False,
    }


def _publish(ops, record, saves: list | None = None) -> None:
    log = saves if saves is not None else []
    mc.publish(
        ops,
        record,
        repo_slug="o/r",
        pr=9,
        pr_url="https://github.com/o/r/pull/9",
        run_id="R1",
        save=lambda: log.append(list(ops.names())),
    )


def test_publish_creates_issue_only_for_not_done() -> None:
    ops = _Ops()
    record = _record((CHECK, {**NOT_DONE, "issue": None}), (B1, DONE))
    _publish(ops, record)
    creates = [c for c in ops.calls if c[0] == "create_issue"]
    assert creates == [("create_issue", mc.issue_title(CHECK), ())]
    assert record["answers"]["BEH-02"]["issue"] == 24
    assert record["status"] == "completed" and record["commented"] is True


def test_publish_recovers_issue_by_marker() -> None:
    ops = _Ops(found=31)
    record = _record((CHECK, {**NOT_DONE, "issue": None}))
    _publish(ops, record)
    assert "create_issue" not in ops.names()
    assert (
        "find_issue",
        mc.issue_marker("R1", "BEH-02"),
        (),
        "all",
        "@me",
    ) in ops.calls
    assert record["answers"]["BEH-02"]["issue"] == 31


def test_publish_comment_once() -> None:
    ops = _Ops()
    record = _record((B1, DONE))
    _publish(ops, record)
    _publish(ops, record)
    assert ops.names().count("comment") == 1


def test_publish_skips_comment_found_by_marker() -> None:
    ops = _Ops(comments=[mc.comment_marker("R1") + "\nстарый"])
    record = _record((B1, DONE))
    _publish(ops, record)
    assert "comment" not in ops.names() and record["commented"] is True


def test_publish_saves_after_issue() -> None:
    saves: list = []
    ops = _Ops()
    _publish(ops, _record((CHECK, {**NOT_DONE, "issue": None})), saves)
    assert saves[0] == ["find_issue", "create_issue"]
