"""`dfl run` без терминала: вопросы интервью через pending (#766 срез 3).

Окружение — фикстура `env` (`tests/test_lite_dfl.py`); discovery подменена
(`_discovery_status`, `_discovery_answer`, `load_session`), агент — фейк,
падающий при вызове: путь без терминала его не зовёт.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governance import interview as iv
from governance import run_lock, spec_loop
from governance import run_state as rs
from lite import dfl, pending
from lite import interview_remote as ir
from lite.interview_state import Entry, InterviewStateError, SessionView
from tests.test_lite_dfl import (  # noqa: F401
    Chat,
    Discovery,
    Env,
    _question_reply,
    _stopped,
    _tty,
    _waiting,
    env,
)

RUN = "WS-1-a"
INCOMPLETE = "status вернул 11: findings в /r/interview-findings.json"


def _view() -> SessionView:
    """customer: цели покрыты, задачи и функции — нет (добор двух тем)."""
    return SessionView(
        frame="customer",
        required={"goals": "G", "jobs": "J", "functions": "FR"},
        optional={"metrics": "M"},
        entries=[Entry("G-01", "Цель", {"Priority": "Must"})],
        declared=[],
        holders={"G-01": "customer.goals.01"},
        answers={"customer.goals.01": {"text": "цель", "entries": []}},
        answered_keys=["goals"],
        readiness="incomplete",
        asked={"customer.goals.01": "goals"},
    )


def _status(code: int) -> iv.DiscoveryReply:
    envelope: dict[str, Any] = {"next_action": {}, "findings": []}
    return iv.DiscoveryReply(code, envelope, "")


def _rd() -> Path:
    return rs.run_dir(RUN)


def _pending() -> dict | None:
    return pending.load(_rd())


@pytest.fixture()
def fakes(env: Env, monkeypatch: pytest.MonkeyPatch) -> Discovery:  # noqa: F811
    """Сессия и агент: вид сессии, discovery без сети, агент падает."""
    monkeypatch.setattr(dfl, "load_session", lambda sid: _view())
    disc = Discovery()
    monkeypatch.setattr(dfl, "_discovery_answer", disc)
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("разговор"))

    def no_agent(*a: object, **kw: object) -> object:
        pytest.fail("агент без терминала")

    monkeypatch.setattr(dfl.st, "claude_agent", no_agent)
    monkeypatch.setattr(
        spec_loop, "main", lambda argv: env.spec_calls.append(argv) or 0
    )
    return disc


def _incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    _stopped(INCOMPLETE)
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _status(11))


def test_waiting_question_posted_silently(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        _waiting(RUN, "WS-1", "customer")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    _waiting(RUN, "WS-1", "customer")  # оба вызова — продолжение прогона
    real = dfl._remote_interview
    monkeypatch.setattr(dfl, "_remote_interview", lambda t, s, r, code: code)
    assert env.run("run", "--subject", "тема") == 0
    before = capsys.readouterr().out
    assert _pending() is None
    monkeypatch.setattr(dfl, "_remote_interview", real)
    assert env.run("run", "--subject", "тема") == 0
    assert capsys.readouterr().out == before
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.INTERVIEW_QUESTION
    assert rec["key"] == "q-1" and "Кто ваш клиент?" in rec["text"]
    assert rec["data"]["prompt"] and fakes.calls == []


def test_topup_wait_line_and_skip_via_reply(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _incomplete(monkeypatch)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.INTERVIEW_TOPUP
    assert rec["key"] == "topic:jobs"
    out = capsys.readouterr().out
    assert f"ждёт ответа: {rec['text']}" in out and rec["id"] in out
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    assert env.run("run") == 0
    nxt = _pending()
    assert nxt is not None and nxt["key"] == "topic:functions"
    assert ir.load_marks(_rd())["skipped"] == ["topic:jobs"]
    assert "ждёт ответа: Не хватает: функции" in capsys.readouterr().out
    assert len(env.spec_calls) == 2 and fakes.calls == []


def test_pass_exhausted_steps_once_and_new_pass_asks_again(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пропущены все темы прохода: отметки стёрты, шаг ещё раз (снова неполный —
    прежний вывод стопа, без нового вопроса); следующий `dfl run` — новый
    проход с первой темы."""
    _incomplete(monkeypatch)
    for key in ("topic:jobs", "topic:functions"):
        assert env.run("run") == 0
        rec = _pending()
        assert rec is not None and rec["key"] == key
        assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    capsys.readouterr()
    calls = len(env.spec_calls)
    assert env.run("run") == 0
    assert len(env.spec_calls) == calls + 2  # шаг run и шаг конца прохода
    assert _pending() is None
    assert not (_rd() / ir.PASS_FILE).exists()
    assert "ждёт ответа" not in capsys.readouterr().out
    assert env.run("run") == 0
    again = _pending()
    assert again is not None and again["key"] == "topic:jobs"
    assert len(env.spec_calls) == calls + 3


def _next_question(qid: str, text: str) -> iv.DiscoveryReply:
    action = {"session_id": "s", "question_id": qid, "question_text": text}
    return iv.DiscoveryReply(20, {"next_action": action}, "")


def test_question_skip_applied_then_step_once(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пропуск вопроса discovery: голый текст ответом; вопросов больше нет —
    шаг один раз (как `dfl answer`); шаг оставил прогон ждать ответа — его
    вопрос в `pending.json`, молча."""
    _waiting(RUN, "WS-1", "customer")
    stepped_after_send: list[bool] = []

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(argv)
        if fakes.calls:
            stepped_after_send.append(True)
        return 0

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        if not fakes.calls:
            return _question_reply()
        if stepped_after_send:
            return _next_question("q-2", "Что болит?")
        return _status(0)

    monkeypatch.setattr(spec_loop, "main", spec_main)
    monkeypatch.setattr(dfl, "_discovery_status", status)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    calls = len(env.spec_calls)
    capsys.readouterr()
    assert env.run("run") == 0
    assert fakes.flags == [("q-1", False)]
    assert len(env.spec_calls) == calls + 2
    nxt = _pending()
    assert nxt is not None and nxt["kind"] == pending.INTERVIEW_QUESTION
    assert nxt["key"] == "q-2" and "Что болит?" in nxt["text"]
    assert "ждёт ответа" not in capsys.readouterr().out


def test_discovery_refusal_is_dfl_error(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _waiting(RUN, "WS-1", "customer")
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    fakes.code = 2
    assert env.run("run") == 2
    assert "payload missing text" in capsys.readouterr().err
    again = _pending()  # вопрос снова виден `dfl pending`
    assert again is not None and again["id"] == rec["id"] and again["data"]


def test_content_reply_for_topup_is_dfl_error(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ответ по существу мимо `dfl reply` (тот бы отказал) — `DflError`."""
    _incomplete(monkeypatch)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    replies = {rec["id"]: "задачи такие"}
    (_rd() / pending.REPLIES_FILE).write_text(json.dumps(replies), encoding="utf-8")
    capsys.readouterr()
    assert env.run("run") == 2
    assert "через dfl answer --items" in capsys.readouterr().err
    assert (_pending() or {}).get("id") == rec["id"]


def test_unreadable_session_keeps_marks_no_extra_step(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сессия не читается — не «проход исчерпан»: отметки целы, шаг один."""
    _incomplete(monkeypatch)
    ir.mark(_rd(), "topic:jobs", "skipped")

    def broken(session_id: str) -> SessionView:
        raise InterviewStateError("discovery show: boom")

    monkeypatch.setattr(dfl, "load_session", broken)
    assert env.run("run") == 0
    assert len(env.spec_calls) == 1
    assert ir.load_marks(_rd())["skipped"] == ["topic:jobs"]
    assert _pending() is None


def test_remote_refused_while_terminal_chats(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Замок прогона у терминала — `dfl run` без терминала: «прогон занят»."""
    _incomplete(monkeypatch)
    with run_lock.run_lock(RUN):
        assert env.run("run") == 2
    assert "исполняется другим процессом" in capsys.readouterr().err
    assert _pending() is None


def test_terminal_chat_holds_run_lock(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _incomplete(monkeypatch)
    _tty(monkeypatch)
    busy: list[bool] = []

    def chat(state: rs.RunState) -> object:
        try:
            with run_lock.run_lock(state.run_id):
                busy.append(False)
        except run_lock.LockBusy:
            busy.append(True)
        return dfl.ic.ChatResult.FINISHED

    monkeypatch.setattr(dfl, "_chat", chat)
    assert env.run("run") == 0
    assert busy == [True]


def test_terminal_chat_discards_pending(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _incomplete(monkeypatch)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    assert env.run("run") == 0  # отметка + вопрос по второй теме
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    for name in (pending.PENDING_FILE, pending.REPLIES_FILE, ir.PASS_FILE):
        assert (_rd() / name).exists()
    _tty(monkeypatch)
    chat = Chat()
    monkeypatch.setattr(dfl, "_chat", chat)
    assert env.run("run") == 0
    assert chat.states == [RUN]
    for name in (pending.PENDING_FILE, pending.REPLIES_FILE, ir.PASS_FILE):
        assert not (_rd() / name).exists()


def test_answer_file_discards_pending(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _waiting(RUN, "WS-1", "customer")
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    ir.mark(_rd(), "topic:jobs", "skipped")
    seen: list[bool] = []
    real = dfl.run

    def run_after(*a: Any, **kw: Any) -> int:
        seen.append((_rd() / pending.REPLIES_FILE).exists())
        seen.append((_rd() / ir.PASS_FILE).exists())
        return real(*a, **kw)

    monkeypatch.setattr(dfl, "run", run_after)
    (env.tmp / "target" / "answer.yaml").write_text("text: да\n", encoding="utf-8")
    assert env.run("answer", "--file", "answer.yaml") == 0
    assert seen == [False, False] and fakes.flags == [(None, False)]


def test_no_agent_called(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Добор, пропуск и конец прохода — без агента (фейк падает при вызове)
    и без отправки в discovery."""
    _incomplete(monkeypatch)
    for _ in range(2):
        assert env.run("run") == 0
        rec = _pending()
        assert rec is not None
        assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    assert env.run("run") == 0
    assert fakes.calls == []
    assert ir.load_marks(_rd()) == {"skipped": [], "done": []}
