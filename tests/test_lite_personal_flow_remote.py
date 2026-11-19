"""Шаг интервью личного пути без терминала (#766 срез 4, Task 2).

`advance(remote=True, tty=False)`: интервью ведёт скил (`remote_interview`),
агент интервьюера не зовётся (R8 — фейк падает); в терминале удалённое
состояние снимается до интервьюера (R5); замечание к брифу на паузе ведёт
к повторному интервью без терминала (R6). Фейки — как в
`tests/test_lite_personal_flow.py`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from lite import pending
from lite import review_pause as rp
from lite.personal import flow, interviewer, state
from lite.personal import remote_interview as ri
from lite.personal.brief import brief_from_json, render_brief
from tests.test_lite_personal_flow import FakeOps, checker
from tests.test_lite_personal_remote_interview import BRIEF as BRIEF_DATA
from tests.test_lite_personal_review import (  # noqa: F401
    BRIEF,
    _author,
    _bundle,
    _run,
    personal_root,
)

REMARK = "добавь экспорт"


def _boom(*_a: object, **_k: object) -> str:
    raise AssertionError("агент интервьюера без терминала не зовётся (R8)")


@pytest.fixture(autouse=True)
def no_terminal_interview(monkeypatch: pytest.MonkeyPatch) -> None:
    """Терминальный интервьюер на удалённом пути не зовётся."""
    monkeypatch.setattr(flow, "interview", _boom)


def _go(run: state.PersonalRun, *, remote: bool = True, tty: bool = False) -> int:
    return flow.advance(
        run,
        FakeOps(run),
        read=_boom,
        agent=_boom,
        launch=_author(),
        check=checker("PASS"),
        tty=lambda: tty,
        remote=remote,
    )


def _rec(run: state.PersonalRun) -> dict:
    rec = pending.load(state.run_dir(run.run_id))
    assert rec is not None
    return rec


def _brief_confirmed(run: state.PersonalRun) -> None:
    """Скил ведёт разговор и сдаёт бриф, человек отвечает «да»."""
    rd = state.run_dir(run.run_id)
    qid = str(_rec(run)["id"])
    ri.note(rd, qid, "agent", "Что должно работать?")
    ri.note(rd, qid, "human", "команда печатает итог")
    assert ri.submit(rd, qid, BRIEF_DATA, subject=run.subject).ok
    rec = _rec(run)
    assert rec["kind"] == pending.BRIEF_CONFIRM
    pending.record(rd, str(rec["id"]), "да")


def _entries(rd: Path) -> list[dict]:
    path = rd / interviewer.TRANSCRIPT
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def test_no_tty_interview_posts_need_brief(tmp_path: Path) -> None:
    run = _run(tmp_path, ())
    assert _go(run) == 0
    assert run.step == "interview"
    assert _rec(run)["kind"] == pending.INTERVIEW_PERSONAL
    assert run.stop_reason.startswith("ждёт ответа")
    assert not (_bundle(run) / "00-brief.md").exists()


def test_no_tty_confirmed_brief_moves_to_design(tmp_path: Path) -> None:
    run = _run(tmp_path, ())
    assert _go(run) == 0
    _brief_confirmed(run)
    assert _go(run) == 0
    rd = state.run_dir(run.run_id)
    brief = brief_from_json(BRIEF_DATA)
    written = json.loads((rd / flow.BRIEF_JSON).read_text(encoding="utf-8"))
    assert written == json.loads(json.dumps(asdict(brief)))
    assert (_bundle(run) / "00-brief.md").read_text(encoding="utf-8") == (
        render_brief(brief)
    )
    approvals = (rd / flow.APPROVALS).read_text(encoding="utf-8").splitlines()
    assert approvals == [json.dumps(flow.BRIEF_APPROVAL, ensure_ascii=False)]
    assert run.step not in ("interview", "design")
    assert not (rd / ri.DRAFT).exists()


def test_tty_resets_remote_state(tmp_path: Path, monkeypatch) -> None:
    """R5 (Review Focus 4): висящий `brief-confirm` и черновик сняты до
    терминального интервьюера."""
    run = _run(tmp_path, ())
    assert _go(run) == 0
    rd = state.run_dir(run.run_id)
    qid = str(_rec(run)["id"])
    assert ri.submit(rd, qid, BRIEF_DATA, subject=run.subject).ok
    assert _rec(run)["kind"] == pending.BRIEF_CONFIRM and (rd / ri.DRAFT).exists()
    seen: list[tuple[object, bool]] = []

    def terminal(run_dir: Path, *_a: object, **_k: object):
        seen.append((pending.load(run_dir), (run_dir / ri.DRAFT).exists()))
        return BRIEF

    monkeypatch.setattr(flow, "interview", terminal)
    assert _go(run, remote=False, tty=True) == 0
    assert seen == [(None, False)]
    assert not (rd / pending.REPLIES_FILE).exists()
    assert (_bundle(run) / "00-brief.md").exists()


def test_no_tty_brief_remarks_reinterview(tmp_path: Path) -> None:
    """R6 (Review Focus 5): замечание к брифу через `dfl reply` → повторное
    интервью скилом → после подтверждения пауза брифа снова, замечание
    применено один раз."""
    run = _run(tmp_path, ("brief",))
    rd = state.run_dir(run.run_id)
    assert _go(run) == 0
    _brief_confirmed(run)
    assert _go(run) == 0  # бриф записан, пауза брифа ждёт reply
    rec = _rec(run)
    assert rec["kind"] == pending.REVIEW and rec["key"] == "brief"
    pending.record(rd, str(rec["id"]), REMARK)

    assert _go(run) == 0  # замечание → интервью заново
    fix = f"поправить: {REMARK}"
    assert [e for e in _entries(rd) if e.get("text") == fix] == [
        {"role": "human", "text": fix}
    ]
    assert _rec(run)["kind"] == pending.INTERVIEW_PERSONAL
    assert run.step == "interview"
    assert [r.text for r in rp.pending(rd, "brief")] == [REMARK]

    _brief_confirmed(run)
    assert _go(run) == 0  # бриф подтверждён → пауза брифа снова
    rec = _rec(run)
    assert rec["kind"] == pending.REVIEW and rec["key"] == "brief"
    assert run.step == "interview"
    assert rp.pending(rd, "brief") == []
    assert [(r.text, r.applied) for r in rp.all_remarks(rd)] == [(REMARK, True)]
    assert sum(e.get("text") == fix for e in _entries(rd)) == 1
