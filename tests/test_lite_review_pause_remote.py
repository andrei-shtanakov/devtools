"""Пауза просмотра «дальше» без терминала (#766, Task 4)."""

from __future__ import annotations

import pytest

from governance import run_state as rs
from governance import runner
from lite import interview_chat as ic
from lite import pending
from lite import review_pause as rp
from lite.personal import state
from tests.test_lite_dfl import Env, _bind_streams, _read, _tty, env  # noqa: F401
from tests.test_lite_dfl_owner_review import (
    DESIGN,
    RUN_ID,
    _steps,
)
from tests.test_lite_personal_flow import FakeOps, checker
from tests.test_lite_personal_review import (
    _advance,
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


def test_personal_remarks_without_tty_keep_old_hint(tmp_path) -> None:
    run = _run(tmp_path, ("design",))
    rp.record(state.run_dir(run.run_id), "design", "добавь логи")
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    assert "замечания — dfl run в терминале" in run.stop_reason


def test_pause_manual_edit_then_done_rechecks(tmp_path) -> None:
    """Файл правлен руками после показа, «дальше» через reply — перепроверка."""
    run = _run(tmp_path, ("design",))
    assert _advance(run, FakeOps(run), read=_no_read, tty=False) == 0
    node = _bundle(run) / "20-design.md"
    node.write_text(DESIGN + "\nправка руками\n", encoding="utf-8")
    _reply(run)
    check = checker("PASS")
    assert _advance(run, FakeOps(run), read=_no_read, check=check, tty=False) == 0
    # перепроверка, затем пауза снова: вопрос записан заново
    assert check.calls[0] == "personal-design-vs-brief"  # type: ignore[attr-defined]
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


def test_enterprise_remarks_without_tty_keep_old_hint(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    _steps(env, monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    rp.record(rs.run_dir(RUN_ID), "design", "уточнить D-01")
    _read(capfd)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "замечания — dfl run в терминале" in out


def test_ask_remote_unit(tmp_path) -> None:
    path = tmp_path / "20-design.md"
    path.write_text("x", encoding="utf-8")
    ask = pending.asker(tmp_path)
    assert rp.ask_remote("design", "дизайн", [path], ask=ask) is None
    q = rp.question("design", "дизайн", [path])
    assert q.accepts == "«дальше»" and q.links == (f"file://{path.resolve()}",)
    pending.record(tmp_path, q.id, "ок")
    assert rp.ask_remote("design", "дизайн", [path], ask=ask) == ""
