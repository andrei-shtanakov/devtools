"""Замечания к этапу через `dfl reply` на личном пути (#766 срез 2, Task 3).

Без терминала: пауза → `reply` с замечанием → `dfl run` правит этап (один
платный вызов автора), вопрос «Удалить …?» ждёт `reply` → `dfl run` без
автора, перепроверка, пауза снова → «дальше». Фейки — как в
`tests/test_lite_personal_review.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lite import owner_review as orv
from lite import pending
from lite import review_pause as rp
from lite.personal import state
from tests.test_lite_personal_flow import FakeOps, checker
from tests.test_lite_personal_review import (  # noqa: F401
    DECOMP_EDGE,
    DESIGN,
    DESIGN_EDGE,
    NO_D02,
    _advance,
    _author,
    _bundle,
    _lines,
    _no_read,
    _run,
    personal_root,
)

REMARK = "убери D-02"
CONFIRM = "Удалить D-02 — «Поиск — построчный фильтр по подстроке»? 1. удалить"
PAID = re.compile(
    r"платных вызовов: (?:авторинг (\d+), )?сортировка (\d+), "
    r"переписывание (\d+), проверка (\d+)"
)


def _reply(run: state.PersonalRun, text: str) -> dict:
    """`dfl reply` на ждущий вопрос прогона; возвращает сам вопрос."""
    rd = state.run_dir(run.run_id)
    rec = pending.load(rd)
    assert rec is not None
    pending.record(rd, str(rec["id"]), text)
    return rec


def _paid(out: str) -> tuple[int, int, int, int]:
    """Сумма строк «платных вызовов» за все `dfl run` вывода."""
    total = [0, 0, 0, 0]
    for match in PAID.finditer(out):
        for i, value in enumerate(match.groups()):
            total[i] += int(value or 0)
    return (total[0], total[1], total[2], total[3])


def _remote_cycle(tmp_path: Path, author, check) -> state.PersonalRun:
    """Пауза → «убери D-02» → «1» → «дальше» без терминала."""
    run = _run(tmp_path, ("design",))
    ops = FakeOps(run)

    def go() -> int:
        return _advance(run, ops, read=_no_read, launch=author, check=check, tty=False)

    assert go() == 0
    assert _reply(run, REMARK)["kind"] == pending.REVIEW
    assert go() == 0
    assert _reply(run, "1")["kind"] == pending.REMARKS_CONFIRM
    assert go() == 0
    assert _reply(run, "дальше")["kind"] == pending.REVIEW
    assert go() == 0
    return run


def test_remarks_by_reply_revise_and_continue(tmp_path, capsys) -> None:
    run = _run(tmp_path, ("design",))
    ops = FakeOps(run)
    author = _author(NO_D02)
    check = checker("PASS")
    rd = state.run_dir(run.run_id)

    def go() -> int:
        return _advance(run, ops, read=_no_read, launch=author, check=check, tty=False)

    assert go() == 0  # пауза: вопрос этапа ждёт reply
    _reply(run, REMARK)

    assert go() == 0  # правка: автор зван, вопрос об удалении записан
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REMARKS_CONFIRM
    assert rec["text"].startswith(CONFIRM)
    assert run.step == "design" and run.stop_reason.startswith("ждёт ответа: Удалить")
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == DESIGN
    assert [r.text for r in rp.pending(rd, "design")] == [REMARK]
    assert (rd / orv.CANDIDATE_FILE).exists()
    assert check.calls == [DESIGN_EDGE]  # type: ignore[attr-defined]
    _reply(run, "1")

    assert go() == 0  # автор не зван, этап перепроверен, пауза снова
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == NO_D02
    assert check.calls == [DESIGN_EDGE, DESIGN_EDGE]  # type: ignore[attr-defined]
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REVIEW
    assert rec["key"] == "design"
    assert run.step == "design"
    assert not (rd / orv.CANDIDATE_FILE).exists()
    remarks = rp.all_remarks(rd)
    assert [(r.text, r.applied) for r in remarks] == [(REMARK, True)]
    _reply(run, "дальше")

    assert go() == 0
    assert run.step == "code-pr"
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    assert check.calls == [  # type: ignore[attr-defined]
        DESIGN_EDGE,
        DESIGN_EDGE,
        DECOMP_EDGE,
    ]
    assert not run.stop_reason
    assert "замечания — dfl run в терминале" not in capsys.readouterr().out


def test_review_reply_consumed_before_revise(tmp_path) -> None:
    """Ответ паузы снят до правки: `revise_by_owner` со `store` не видит
    чужого вопроса, в `pending.json` — свежий вопрос «Удалить …?», а
    ответ на вопрос паузы не переживает его."""
    run = _run(tmp_path, ("design",))
    ops = FakeOps(run)
    author = _author(NO_D02)
    rd = state.run_dir(run.run_id)
    seen: list[dict | None] = []
    real = orv.revise_by_owner

    def spy(*args, **kwargs):
        seen.append(pending.load(rd))
        return real(*args, **kwargs)

    assert _advance(run, ops, read=_no_read, launch=author, tty=False) == 0
    review = _reply(run, REMARK)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orv, "revise_by_owner", spy)
        assert _advance(run, ops, read=_no_read, launch=author, tty=False) == 0
    assert seen == [None]
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REMARKS_CONFIRM
    assert rec["id"] != review["id"]
    assert not pending.answered(rd)


def test_brief_remarks_by_reply_refused(tmp_path) -> None:
    run = _run(tmp_path, ("brief",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    rd = state.run_dir(run.run_id)
    rec = pending.load(rd)
    assert rec is not None and rec["key"] == "brief"
    with pytest.raises(pending.PendingError) as exc:
        pending.record(rd, str(rec["id"]), "цель шире")
    assert str(exc.value) == pending.REVIEW_REFUSAL
    assert not pending.answered(rd)


def test_brief_remark_without_tty_still_stops(tmp_path) -> None:
    """Недоигранное замечание к брифу без терминала — прежний стоп NO_TTY."""
    run = _run(tmp_path, ("brief",))
    assert _advance(run, FakeOps(run), read=_lines()) == 0
    rd = state.run_dir(run.run_id)
    rp.record(rd, "brief", "цель шире")
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert run.stop_reason.startswith("ждёт просмотра этапа «бриф»")
    assert [r.text for r in rp.pending(rd, "brief")] == ["цель шире"]


def test_remote_waiting_is_code_0_not_stopped(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    author = _author(NO_D02)
    assert _advance(run, FakeOps(run), read=_no_read, launch=author, tty=False) == 0
    _reply(run, REMARK)
    assert _advance(run, FakeOps(run), read=_no_read, launch=author, tty=False) == 0
    assert run.stop_reason == pending.wait_text(state.run_dir(run.run_id))


def test_terminal_continues_remote_candidate(tmp_path) -> None:
    """Кандидат, оставленный прогоном без терминала, терминал доигрывает
    без второго вызова автора."""
    run = _run(tmp_path, ("design",))
    author = _author(NO_D02)
    assert _advance(run, FakeOps(run), read=_no_read, launch=author, tty=False) == 0
    _reply(run, REMARK)
    assert _advance(run, FakeOps(run), read=_no_read, launch=author, tty=False) == 0
    read = _lines("1", "дальше")
    assert _advance(run, FakeOps(run), read=read, launch=author) == 0
    assert run.step == "code-pr"
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == NO_D02


def test_remote_cycle_paid_calls_equal_terminal(tmp_path, capsys) -> None:
    term_author, term_check = _author(NO_D02), checker("PASS")
    (tmp_path / "t").mkdir()
    (tmp_path / "r").mkdir()
    term = _run(tmp_path / "t", ("design",))
    read = _lines(REMARK, "", "1", "дальше")
    assert (
        _advance(term, FakeOps(term), read=read, launch=term_author, check=term_check)
        == 0
    )
    assert term.step == "code-pr"
    term_paid = _paid(capsys.readouterr().out)

    rem_author, rem_check = _author(NO_D02), checker("PASS")
    rem = _remote_cycle(tmp_path / "r", rem_author, rem_check)
    assert rem.step == "code-pr"
    rem_paid = _paid(capsys.readouterr().out)

    assert term_paid == rem_paid
    assert term_paid[2] == 1  # переписывание — один раз
    assert len(rem_author.prompts) == len(term_author.prompts) == 1  # type: ignore[attr-defined]
    assert rem_check.calls == term_check.calls  # type: ignore[attr-defined]
