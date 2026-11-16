"""Вопрос по ручной проверке и проход ответов (#771, lite.manual_ask)."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from governance import manual_checks as mc
from lite import manual_ask as ma

C1 = mc.ManualCheck("BEH-01", "первая", "TASK-001", "docs/a.md")
C2 = mc.ManualCheck("BEH-02", "вторая", "TASK-002", None)

BEH = """#### BEH-01: первая
- **checked_by**: `kind: manual` `target: docs/a.md`

#### BEH-02: вторая
- **checked_by**: `kind: manual` `target: docs/b.md`
"""
DT = """#### DT-01: a · type: verify · owner: qa
scenarios: [BEH-01]
depends_on: []
parallel_group: solo

#### DT-02: b · type: verify · owner: qa
scenarios: [BEH-02]
depends_on: [DT-01]
parallel_group: solo
"""


def _reader(lines: Iterable[str]):
    it = iter(lines)
    return lambda _prompt: next(it, None)


def test_ask_one_done(capsys) -> None:
    assert ma.ask_one(C1, _reader(["1"])) == {"verdict": mc.DONE, "note": ""}
    assert "Ручная проверка BEH-01 (TASK-001): «первая»" in capsys.readouterr().out


def test_ask_one_not_done_with_note() -> None:
    got = ma.ask_one(C1, _reader(["2", "5/10", ""]))
    assert got == {"verdict": mc.NOT_DONE, "note": "5/10"}


def test_ask_one_not_done_without_note() -> None:
    got = ma.ask_one(C1, _reader(["не выполнено", ""]))
    assert got == {"verdict": mc.NOT_DONE, "note": ""}


def test_ask_one_caveat_requires_text() -> None:
    got = ma.ask_one(C1, _reader(["3", "", "", "5/10 до SDK", ""]))
    assert got == {"verdict": mc.CAVEAT, "note": "5/10 до SDK"}


def test_ask_one_unknown_repeats(capsys) -> None:
    assert ma.ask_one(C1, _reader(["может быть", "1"]))["verdict"] == mc.DONE
    assert capsys.readouterr().out.count("Ручная проверка BEH-01") == 2


@pytest.mark.parametrize("lines", [["/стоп"], [], ["3", "/стоп"]])
def test_ask_one_stop_word_is_none(lines) -> None:
    assert ma.ask_one(C1, _reader(lines)) is None


class _Ops:
    def __init__(self, boom: bool = False) -> None:
        self.boom = boom
        self.calls: list[str] = []

    def find_issue(self, *a, **kw):
        self.calls.append("find_issue")
        return None

    def create_issue(self, *a, **kw):
        self.calls.append("create_issue")
        return 24

    def caller_login(self) -> str:
        return "me"

    def pr_comments_fact(self, *a):
        from governance.facts import Fact, Outcome

        return Fact(Outcome.FOUND, [])

    def comment(self, *a):
        if self.boom:
            raise RuntimeError("gh down")
        self.calls.append("comment")


def _settle(record: dict, lines: Iterable[str], *, tty: bool = True, ops=None,
            texts=lambda: (BEH, DT)) -> str | None:  # fmt: skip
    return ma.settle(
        record,
        load_texts=texts,
        tty=tty,
        read=_reader(lines),
        ops=ops if ops is not None else _Ops(),
        repo_slug="o/r",
        pr=9,
        pr_url="https://github.com/o/r/pull/9",
        run_id="R1",
        save=lambda: None,
    )


def test_settle_no_checks_is_silent(capsys) -> None:
    record: dict = {}
    ops = _Ops()
    assert (
        _settle(record, [], ops=ops, texts=lambda: ("# пусто\n#### BEH-09: x\n", ""))
        is None
    )
    assert record["status"] == "completed" and ops.calls == []
    assert capsys.readouterr().out == ""


def test_settle_no_tty_stops() -> None:
    record: dict = {}
    assert _settle(record, [], tty=False) == ma.NO_TTY.format(n=2)
    assert record["status"] == "started" and len(record["checks"]) == 2


def test_settle_stop_word_keeps_given_answers() -> None:
    record: dict = {}
    assert _settle(record, ["1", "/стоп"]) == ma.PAUSED
    assert list(record["answers"]) == ["BEH-01"]
    assert record["answers"]["BEH-01"]["at"]
    assert _settle(record, ["2", ""]) is None
    assert record["answers"]["BEH-02"]["verdict"] == mc.NOT_DONE
    assert record["answers"]["BEH-02"]["issue"] == 24
    assert record["status"] == "completed"


def test_settle_unreadable_bundle_stops() -> None:
    def broken() -> tuple[str, str]:
        raise OSError("нет файла")

    reason = _settle({}, [], texts=broken)
    assert reason is not None and reason.startswith("бандл прогона не прочитан")


def test_settle_publish_error_stops() -> None:
    reason = _settle({}, ["1", "1"], ops=_Ops(boom=True))
    assert reason is not None and reason.startswith("публикация ответов не удалась")
    assert "gh down" in reason
