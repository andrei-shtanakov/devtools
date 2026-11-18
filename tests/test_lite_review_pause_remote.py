"""Пауза просмотра «дальше» без терминала (#766, Task 4)."""

from __future__ import annotations

import json

import pytest

from governance import run_state as rs
from governance import runner
from lite import dfl, pending
from lite import interview_chat as ic
from lite import review_pause as rp
from lite.personal import state
from tests.test_lite_dfl import Env, _bind_streams, _read, _tty, env  # noqa: F401
from tests.test_lite_dfl_owner_review import (
    DESIGN,
    RUN_ID,
    _design,
    _steps,
)
from tests.test_lite_dfl_owner_review import _author as _enterprise_author
from tests.test_lite_personal_flow import FakeOps, checker
from tests.test_lite_personal_review import (
    _advance,
    _author,
    _bundle,
    _lines,
    _no_read,
    _run,
    personal_root,  # noqa: F401
)


def _waiting_line(run) -> str:
    assert run.stop_reason.startswith("ждёт ответа:"), run.stop_reason
    return run.stop_reason


def _reply(run, text: str = "дальше") -> None:
    rd = state.run_dir(run.run_id)
    pending.record(rd, str(pending.load(rd)["id"]), text)


def test_personal_pause_remote_waits_then_continues(tmp_path, capsys) -> None:
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert run.step == "design"
    _waiting_line(run)
    rec = pending.load(state.run_dir(run.run_id))
    assert rec is not None and rec["kind"] == pending.REVIEW
    assert rec["links"] == [f"file://{(_bundle(run) / '20-design.md').resolve()}"]
    assert not getattr(run, "stopped", False)
    _reply(run)
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert run.step == "code-pr"
    assert pending.load(state.run_dir(run.run_id)) is None


def test_personal_pause_terminal_uses_recorded_reply(tmp_path) -> None:
    """Терминал: записанный `dfl reply` берётся вместо ввода."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    _reply(run)
    assert _advance(run, FakeOps(run), read=_no_read, tty=True) == 0
    assert run.step == "code-pr"


def test_personal_pause_terminal_live_answer_clears_pending(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert _advance(run, FakeOps(run), read=_lines("дальше"), tty=True) == 0
    assert run.step == "code-pr"
    assert pending.load(state.run_dir(run.run_id)) is None


def test_personal_remarks_without_tty_are_revised(tmp_path) -> None:
    """Срез 2 (#766): недоигранное замечание без терминала правится автором
    (прежде — стоп «замечания — dfl run в терминале»)."""
    run = _run(tmp_path, ("design",))
    rd = state.run_dir(run.run_id)
    rp.record(rd, "design", "добавь логи")
    author = _author(DESIGN + "\nЛоги пишутся в stderr.\n")
    assert _advance(run, FakeOps(run), read=_no_read, launch=author, tty=False) == 0
    assert "замечания — dfl run в терминале" not in run.stop_reason
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    assert [(r.text, r.applied) for r in rp.all_remarks(rd)] == [("добавь логи", True)]
    _waiting_line(run)  # перепроверен, пауза снова


def test_pause_manual_edit_then_done_rechecks(tmp_path) -> None:
    """Файл правлен руками после показа, «дальше» через reply — перепроверка."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    node = _bundle(run) / "20-design.md"
    node.write_text(DESIGN + "\nправка руками\n", encoding="utf-8")
    _reply(run)
    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=_no_read, check=check, tty=False) == 0
    # одна перепроверка (как в терминале), затем пауза снова: вопрос заново
    assert check.calls == ["personal-design-vs-brief"]  # type: ignore[attr-defined]
    assert run.step == "design"
    _waiting_line(run)
    _reply(run)
    assert _advance(run, FakeOps(run), read=_no_read, check=check, tty=False) == 0
    assert run.step == "code-pr"


def test_enterprise_pause_remote_waits_then_continues(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    seen = _steps(env, monkeypatch)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("без tty"))
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "ждёт ответа:" in out
    rd_dir = rs.run_dir(RUN_ID)
    rec = pending.load(rd_dir)
    assert rec is not None and rec["kind"] == pending.REVIEW
    assert rec["key"] == "design"
    assert seen == []
    pending.record(rd_dir, str(rec["id"]), "дальше")
    assert env.run("run", "--subject", "t") == 0  # второй узел ждёт
    rec = pending.load(rd_dir)
    assert rec is not None and rec["key"] == "acceptance"
    assert seen == []
    pending.record(rd_dir, str(rec["id"]), "дальше")
    assert env.run("run", "--subject", "t") == 0
    assert runner.review_pause_passed(rs.load(RUN_ID))
    assert seen == [{"status": "completed", "revised": False}]


def test_pause_remote_w4_two_nodes(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """W4: два вопроса подряд, по одному на вызов; первый не возвращается."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    _steps(env, monkeypatch)
    asked: list[str] = []
    for _ in range(2):
        assert env.run("run", "--subject", "t") == 0
        rd = rs.run_dir(RUN_ID)
        rec = pending.load(rd)
        assert rec is not None
        asked.append(str(rec["key"]))
        pending.record(rd, str(rec["id"]), "дальше")
    assert asked == ["design", "acceptance"]


def test_enterprise_terminal_uses_recorded_reply(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    seen = _steps(env, monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    rd = rs.run_dir(RUN_ID)
    rec = pending.load(rd)
    assert rec is not None
    pending.record(rd, str(rec["id"]), "дальше")
    _tty(monkeypatch)
    queue = ["дальше"]  # второй узел — живой ответ
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: queue.pop(0))
    assert env.run("run", "--subject", "t") == 0
    assert queue == []
    assert seen == [{"status": "completed", "revised": False}]
    assert pending.load(rd) is None


def test_enterprise_remarks_without_tty_are_revised(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Срез 2 (#766): недоигранное замечание без терминала правится автором
    (прежде — стоп «замечания — dfl run в терминале»)."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    _steps(env, monkeypatch)
    calls = _enterprise_author(monkeypatch, DESIGN + "\nуточнено\n")
    assert env.run("run", "--subject", "t") == 0
    rp.record(rs.run_dir(RUN_ID), "design", "уточнить D-01")
    _read(capfd)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "замечания — dfl run в терминале" not in out
    assert len(calls) == 1
    assert rs.load(RUN_ID).ops["review-pause-4"] == {
        "status": "started",
        "revised": True,
    }


def test_ask_remote_unit(tmp_path) -> None:
    path = tmp_path / "20-design.md"
    path.write_text("x", encoding="utf-8")
    ask = pending.asker(tmp_path)
    assert rp.ask_remote("design", "дизайн", [path], ask=ask) is None
    q = rp.question("design", "дизайн", [path])
    assert q.accepts == rp.REMOTE_ACCEPTS and q.links == (f"file://{path.resolve()}",)
    pending.record(tmp_path, q.id, "ок")
    assert rp.ask_remote("design", "дизайн", [path], ask=ask) == ""


def test_w4_manual_edit_keeps_acked_nodes(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ручная правка первого узла W4 не возвращает его на вопрос: узлы,
    принятые в этой паузе, остаются принятыми (`review-acked.json`)."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    seen = _steps(env, monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    rd = rs.run_dir(RUN_ID)
    _design(env).write_text(DESIGN + "\nправка руками\n", encoding="utf-8")
    asked: list[str] = []
    for _ in range(4):
        rec = pending.load(rd)
        if rec is None:
            break
        asked.append(str(rec["key"]))
        pending.record(rd, str(rec["id"]), "дальше")
        assert env.run("run", "--subject", "t") == 0
    assert asked == ["design", "acceptance"]
    assert seen == [{"status": "completed", "revised": True}]
    assert not (rd / dfl.REVIEW_ACKED).exists()


def test_personal_live_answer_drops_recorded_reply(tmp_path) -> None:
    """Reply, записанный во время живого ввода, не проходит следующую паузу."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    rd = state.run_dir(run.run_id)
    qid = str(pending.load(rd)["id"])

    def read(prompt: str) -> str:
        pending.record(rd, qid, "дальше")  # приходит после take, до ответа
        return "дальше"

    assert _advance(run, FakeOps(run), read=read, tty=True) == 0
    assert run.step == "code-pr"
    replies = rd / pending.REPLIES_FILE
    assert not replies.exists() or qid not in json.loads(replies.read_text("utf-8"))
    run.step = "design"  # та же пауза снова: человека спрашивают заново
    run.stop_reason = ""
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert run.step == "design"
    _waiting_line(run)


def test_enterprise_live_answer_drops_recorded_reply(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    _steps(env, monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    rd = rs.run_dir(RUN_ID)
    rec = pending.load(rd)
    assert rec is not None
    qid = str(rec["id"])
    _tty(monkeypatch)

    def read(prompt: str) -> str:
        if pending.load(rd) is not None and not (rd / pending.REPLIES_FILE).exists():
            pending.record(rd, qid, "дальше")  # во время живого ввода
        return "дальше"

    monkeypatch.setattr(ic, "stdin_read", read)
    assert env.run("run", "--subject", "t") == 0
    replies = rd / pending.REPLIES_FILE
    assert not replies.exists() or qid not in json.loads(replies.read_text("utf-8"))


def test_personal_terminal_recorded_remark_is_revised(tmp_path) -> None:
    """Ревью #808: замечание, записанное `dfl reply`, в терминале не теряется
    и не становится «дальше» — его правит автор."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    _reply(run, "добавь логи")
    author = _author(DESIGN + "\nЛоги пишутся в stderr.\n")
    _advance(run, FakeOps(run), read=lambda prompt: None, launch=author, tty=True)
    assert run.step == "design"  # после правки — пауза снова
    assert len(author.prompts) == 1  # type: ignore[attr-defined]
    rd = state.run_dir(run.run_id)
    assert [(r.text, r.applied) for r in rp.all_remarks(rd)] == [("добавь логи", True)]


def test_enterprise_terminal_recorded_remark_is_revised(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ревью #808: enterprise-путь — записанное замечание идёт в правку."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    _steps(env, monkeypatch)
    calls = _enterprise_author(monkeypatch, DESIGN + "\nуточнено\n")
    assert env.run("run", "--subject", "t") == 0
    rd = rs.run_dir(RUN_ID)
    rec = pending.load(rd)
    assert rec is not None
    pending.record(rd, str(rec["id"]), "уточнить D-01")
    _tty(monkeypatch)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: "дальше")
    env.run("run", "--subject", "t")
    assert len(calls) == 1
    assert [r.text for r in rp.all_remarks(rd)] == ["уточнить D-01"]
