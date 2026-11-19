"""`dfl run` без терминала: вопросы интервью через pending (#766 срез 3).

Окружение — фикстура `env` (`tests/test_lite_dfl.py`); discovery подменена
(`_discovery_status`, `_discovery_answer`, `load_session`), агент — фейк,
падающий при вызове: путь без терминала его не зовёт.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from governance import interview as iv
from governance import run_lock, spec_loop
from governance import run_state as rs
from governance.discovery_contract import gate_check as gc
from lite import dfl, pending
from lite import interview_remote as ir
from lite.interview_state import Entry, InterviewStateError, SessionView
from tests.test_lite_dfl import (  # noqa: F401
    REPO,
    URL,
    Chat,
    Discovery,
    Env,
    _question_reply,
    _run,
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


def test_waiting_question_announced_once_without_answer_file_hint(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """devtools#814: без терминала вопрос объявляет строка «ждёт ответа»
    (один раз), а не терминальная подсказка `dfl answer --file`."""
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _question_reply())

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        _waiting(RUN, "WS-1", "customer")
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    _waiting(RUN, "WS-1", "customer")
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.INTERVIEW_QUESTION
    assert rec["key"] == "q-1" and "Кто ваш клиент?" in rec["text"]
    assert rec["data"]["prompt"] and fakes.calls == []
    assert out.count("Кто ваш клиент?") == 1
    assert f"ждёт ответа: {rec['text']}" in out and rec["id"] in out
    assert dfl.ANSWER_HINT not in out


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
    out = capsys.readouterr().out
    # devtools#814: только новый вопрос, без уже отвеченного и без `--file`
    assert f"ждёт ответа: {nxt['text']}" in out
    assert "Кто ваш клиент?" not in out and dfl.ANSWER_HINT not in out


def test_no_question_after_repeat_step_points_to_interview(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пропуск применён, повторный шаг оставил прогон ждать, а статус
    discovery вопроса не дал — не молчание: «ждёт ответа; вопрос: dfl
    interview» (ревью #817)."""
    _waiting(RUN, "WS-1", "customer")

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(argv)
        return 0

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        return _question_reply() if not fakes.calls else _status(1)

    monkeypatch.setattr(spec_loop, "main", spec_main)
    monkeypatch.setattr(dfl, "_discovery_status", status)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    capsys.readouterr()
    assert env.run("run") == 0
    assert fakes.flags == [("q-1", False)]
    assert f"прогон {RUN} ждёт ответа; вопрос: dfl interview" in capsys.readouterr().out


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


def _interview_json(env: Env, capsys: pytest.CaptureFixture[str]) -> dict:  # noqa: F811
    capsys.readouterr()
    assert env.run("interview", "--json") == 0
    return json.loads(capsys.readouterr().out)


def test_interview_json_shape(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`dfl interview --json`: вопрос с темой, покрытие, находки, готовность;
    stdout — только JSON (без баннера режима test)."""
    _waiting(RUN, "WS-1", "customer")
    action = {"question_id": "q-7", "coverage_key": "jobs", "question_text": "Что?"}
    reply = iv.DiscoveryReply(20, {"next_action": action}, "")
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: reply)
    out = _interview_json(env, capsys)
    assert out["run_id"] == RUN and out["subject"] == "тема"
    assert out["question"] == {
        "id": "q-7",
        "topic": "jobs",
        "label": "задачи пользователей",
        "text": "Что?",
    }
    assert out["coverage"] == ir.coverage(_view())
    assert out["findings"] == ir.findings(_view())
    assert out["readiness"] == "incomplete" and out["unavailable"] is None
    failed = iv.DiscoveryReply(1, {"operation": {"reason": "упал"}}, "")
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: failed)
    out = _interview_json(env, capsys)
    assert out["question"] is None and out["unavailable"] == "упал"
    assert fakes.calls == []


GOALS = ("q-goals", "goals", "Зачем это нужно?")
FUNCTIONS = ("q-functions", "functions", "Что система должна делать?")


@dataclass
class Session:
    """Фейк discovery сквозного теста: вопросы по очереди, ответы, проверка
    брифа. Темы покрыты пунктами; FR без числа в критерии — GC-08; Must-FR
    без вердикта `X` — GC-05; находок нет — код 0."""

    questions: list[tuple[str, str, str]]
    answers: dict[str, dict] = field(default_factory=dict)
    asked: dict[str, str] = field(default_factory=dict)
    sent: list[tuple[str, bool, dict]] = field(default_factory=list)

    def answer(
        self,
        session_id: str,
        role: str,
        file: Path,
        cwd: Path,
        *,
        question: str | None = None,
        supersede: bool = False,
    ) -> iv.DiscoveryReply:
        payload = yaml.safe_load(file.read_text(encoding="utf-8"))
        assert question is not None
        self.sent.append((question, supersede, payload))
        if not supersede:
            assert question == self.questions.pop(0)[0]
        self.asked.setdefault(question, question.removeprefix("q-"))
        self.answers.pop(question, None)  # последний ответ — в конце
        self.answers[question] = payload
        return iv.DiscoveryReply(20, {"operation": {"status": "ok"}}, "")

    def status(self, session_id: str, cwd: Path) -> iv.DiscoveryReply:
        if self.questions:
            qid, key, text = self.questions[0]
            action = {"question_id": qid, "coverage_key": key, "question_text": text}
            return iv.DiscoveryReply(20, {"next_action": action}, "")
        lines = self._findings()
        return iv.DiscoveryReply(11 if lines else 0, {"findings": lines}, "")

    def view(self, session_id: str) -> SessionView:
        held = {
            e["id"]: (qid, e)
            for qid, a in self.answers.items()
            for e in a.get("entries") or []
        }
        return SessionView(
            frame="customer",
            required={"goals": "G", "functions": "FR"},
            optional={"metrics": "M"},
            entries=[
                Entry(eid, e["body"], {k: v for k, v in e.items() if k[0].isupper()})
                for eid, (_q, e) in held.items()
            ],
            declared=list(held),
            holders={eid: qid for eid, (qid, _e) in held.items()},
            answers=dict(self.answers),
            answered_keys=[self.asked[q] for q in self.answers],
            readiness="incomplete" if self._findings() else "ready",
            findings=self._findings(),
            asked=dict(self.asked),
        )

    def _findings(self) -> list[str]:
        entries = [e for a in self.answers.values() for e in a.get("entries") or []]
        prefixes = {e["id"].rsplit("-", 1)[0] for e in entries}
        lines = [
            f"required topic '{key}' is not covered"
            for key, prefix in (("goals", "G"), ("functions", "FR"))
            if prefix not in prefixes
        ]
        judged = {e.get("target") for e in entries if e["id"].startswith("X-")}
        for e in entries:
            if not e["id"].startswith("FR-"):
                continue
            if not any(c.isdigit() for c in str(e.get("Acceptance", ""))):
                lines.append(f"GC-08 error [{e['id']}] нет **Acceptance** (числа)")
            if e.get("Priority") == "Must" and e["id"] not in judged:
                lines.append(
                    "GC-05 error [coverage.feasibility_review] "
                    f"Must-требование {e['id']} без вердикта"
                )
        return lines


def _pending_json(env: Env, capsys: pytest.CaptureFixture[str]) -> dict:  # noqa: F811
    capsys.readouterr()
    assert env.run("pending", "--json") == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec is not None and rec["answered"] is False
    return rec


def _answer_items(env: Env, qid: str, text: str, items: list[dict]) -> None:  # noqa: F811
    doc = json.dumps({"text": text, "items": items}, ensure_ascii=False)
    (env.tmp / "target" / "items.json").write_text(doc, encoding="utf-8")
    assert env.run("answer", "--items", "items.json", "--id", qid) == 0


def test_enterprise_interview_end_to_end_without_terminal(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Спека §7: интервью до следующей стадии только через `dfl run`,
    `dfl pending --json`, `dfl reply`, `dfl answer --items`; агент и разговор
    в терминале не зовутся (фейки `fakes` падают при вызове)."""
    sess = Session([GOALS, FUNCTIONS])
    monkeypatch.setattr(dfl, "load_session", sess.view)
    monkeypatch.setattr(dfl, "_discovery_status", sess.status)
    monkeypatch.setattr(dfl, "_discovery_answer", sess.answer)
    target = dfl.user_targets.UserTarget(REPO, (env.tmp / "target").resolve(), URL)
    monkeypatch.setattr(dfl.user_targets, "load", lambda: {REPO: target})

    def spec_main(argv: list[str]) -> int:
        """Шаг раннера: статус прогона — по состоянию фейка discovery."""
        env.spec_calls.append(list(argv))
        state = _waiting(RUN, "WS-1", "customer")
        assert state.interview is not None
        code = sess.status("", Path()).code
        if code == 11:
            state.status = "stopped_interview"
            state.interview["stop_reason"] = INCOMPLETE
        elif code == 0:
            state.status = "brief_ready"
        rs.save(state)
        return 1 if code == 11 else 0  # как `spec_loop._report_interview_stop`

    monkeypatch.setattr(spec_loop, "main", spec_main)

    # Новый прогон без терминала: вопрос 1 — в pending, с промптом и покрытием.
    assert env.run("run", "--subject", "тема") == 0
    q1 = _pending_json(env, capsys)
    assert (q1["kind"], q1["key"]) == (pending.INTERVIEW_QUESTION, "q-goals")
    assert ir.HUMAN_PLACEHOLDER in q1["data"]["prompt"]
    assert [t["covered"] for t in q1["data"]["coverage"]] == [False, False, False]
    _answer_items(
        env,
        q1["id"],
        "Хотим меньше ручной сверки",
        [{"prefix": "G", "body": "Меньше ручной сверки", "priority": "must"}],
    )
    assert sess.sent[-1][:2] == ("q-goals", False)
    assert [e["id"] for e in sess.sent[-1][2]["entries"]] == ["G-01"]

    # Вопрос 2 — пропуск через `dfl reply`; конец интервью с неполной темой:
    # тот же `dfl run` ставит первый пункт свежего прохода (добор).
    q2 = _pending_json(env, capsys)
    assert q2["key"] == "q-functions" and q2["text"].startswith("Вопрос 2 (")
    assert env.run("reply", "--id", q2["id"], "--text", "пропустить") == 0
    capsys.readouterr()
    assert env.run("run") == 0
    assert "ждёт ответа: Не хватает" in capsys.readouterr().out
    assert sess.sent[-1] == ("q-functions", False, {"text": "пропустить"})
    assert rs.load(RUN).status == "stopped_interview"

    # Добор непокрытой темы — раскладкой скила держателю темы.
    top = _pending_json(env, capsys)
    assert (top["kind"], top["key"]) == (pending.INTERVIEW_TOPUP, "topic:functions")
    _answer_items(
        env,
        top["id"],
        "Нужен отчёт по складу, быстро",
        [
            {
                "prefix": "FR",
                "body": "Отчёт по складу",
                "priority": "must",
                "acceptance": "быстро",
            }
        ],
    )
    assert sess.sent[-1][:2] == ("q-functions", True)
    assert [e["id"] for e in sess.sent[-1][2]["entries"]] == ["FR-01"]

    # Находка GC-08: критерий — раскладкой с обновлением FR-01.
    fix = _pending_json(env, capsys)
    assert (fix["kind"], fix["key"]) == (pending.INTERVIEW_REPAIR, "criterion:FR-01")
    _answer_items(
        env,
        fix["id"],
        "Отчёт за минуту",
        [
            {
                "prefix": "FR",
                "id": "FR-01",
                "body": "Отчёт по складу",
                "acceptance": "отчёт готов за 1 минуту",
            }
        ],
    )
    fr = sess.sent[-1][2]["entries"][0]
    assert (fr["id"], fr["Acceptance"]) == ("FR-01", "отчёт готов за 1 минуту")

    # Находка GC-05: вердикт реализуемости — `dfl reply да`.
    verdict = _pending_json(env, capsys)
    assert verdict["key"] == "verdict:FR-01"
    seen = _interview_json(env, capsys)
    assert seen["readiness"] == "incomplete" and seen["question"] is None
    assert [t["covered"] for t in seen["coverage"]] == [True, True, False]
    assert any("GC-05" in f["raw"] for f in seen["findings"])
    assert env.run("reply", "--id", verdict["id"], "--text", "да") == 0
    assert env.run("run") == 0
    x = sess.sent[-1][2]["entries"][-1]
    assert (x["id"], x["target"], x["status"]) == ("X-01", "FR-01", "resolved")

    # Шаг дальше: бриф собран, ждущих вопросов нет.
    assert rs.load(RUN).status == "brief_ready"
    capsys.readouterr()
    assert env.run("pending", "--json") == 0
    assert json.loads(capsys.readouterr().out) is None
    assert not (_rd() / ir.PASS_FILE).exists()
    assert fakes.calls == []


def _stops_incomplete_after_send(env: Env, fakes: Discovery) -> Any:  # noqa: F811
    """Шаг раннера: до отправки ответа прогон ждёт, после — остановлен
    неполным с кодом 1 (как настоящий `spec_loop._report_interview_stop`)."""

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if not fakes.calls:
            return 0
        _stopped(INCOMPLETE)
        return 1

    return spec_main


def test_answer_file_in_terminal_keeps_terminal_path(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Финальное ревью I1: `dfl answer --file` в терминале — прежний путь:
    код шага (неполный стоп — 1), без pending и строки «ждёт ответа»."""
    _tty(monkeypatch)
    _waiting(RUN, "WS-1", "customer")
    monkeypatch.setattr(dfl, "_discovery_status", lambda s, c: _status(11))
    monkeypatch.setattr(spec_loop, "main", _stops_incomplete_after_send(env, fakes))
    (env.tmp / "target" / "answer.yaml").write_text("text: да\n", encoding="utf-8")
    assert env.run("answer", "--file", "answer.yaml") == 1
    assert rs.load(RUN).status == "stopped_interview"
    assert _pending() is None and not (_rd() / ir.PASS_FILE).exists()
    assert "ждёт ответа" not in capsys.readouterr().out


def test_reply_skip_ending_interview_posts_topup(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Финальное ревью I2: пропуск последнего вопроса через `dfl reply`, шаг
    остановил интервью неполным — тот же `dfl run` ставит добор со строкой
    «ждёт ответа» и кодом 0 (не «нет вопросов» у неполного стопа)."""
    _waiting(RUN, "WS-1", "customer")

    def status(session_id: str, cwd: Path) -> iv.DiscoveryReply:
        return _status(11) if fakes.calls else _question_reply()

    monkeypatch.setattr(dfl, "_discovery_status", status)
    monkeypatch.setattr(spec_loop, "main", _stops_incomplete_after_send(env, fakes))
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.INTERVIEW_QUESTION
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    capsys.readouterr()
    assert env.run("run") == 0
    assert fakes.flags == [("q-1", False)]
    top = _pending()
    assert top is not None and top["kind"] == pending.INTERVIEW_TOPUP
    assert top["key"] == "topic:jobs"
    assert f"ждёт ответа: {top['text']}" in capsys.readouterr().out


def _step_to(env: Env, status: str) -> Any:  # noqa: F811
    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        state = rs.load(RUN)
        state.status = status
        rs.save(state)
        return 0

    return spec_main


def test_stale_interview_pending_dropped_by_run(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Финальное ревью I3: интервью кончилось вне `dfl run` без терминала
    (например, `dfl correct`), шаг довёл прогон до `brief_ready` — вопрос
    интервью, ответы и отметки прохода сняты."""
    _incomplete(monkeypatch)
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.INTERVIEW_TOPUP
    ir.mark(_rd(), "criterion:FR-01", "done")
    assert env.run("reply", "--id", rec["id"], "--text", "пропустить") == 0
    monkeypatch.setattr(spec_loop, "main", _step_to(env, "brief_ready"))
    assert env.run("run") == 0
    assert _pending() is None
    for name in (pending.REPLIES_FILE, ir.PASS_FILE):
        assert not (_rd() / name).exists()


def test_stale_clear_keeps_other_kinds(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Снятие устаревшего интервью не трогает вопрос другого вида."""
    _incomplete(monkeypatch)
    pending.post(_rd(), pending.Question(pending.MANUAL, "m1", "Проверьте"))
    monkeypatch.setattr(spec_loop, "main", _step_to(env, "brief_ready"))
    assert env.run("run") == 0
    rec = _pending()
    assert rec is not None and rec["kind"] == pending.MANUAL


def test_interview_wait_line_names_subject_when_several(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Финальное ревью M3: в репо несколько тем — строка «ждёт ответа» несёт
    `--subject`, иначе `dfl pending`/`dfl reply` прогон не выберут."""
    _incomplete(monkeypatch)
    _run("WS-2-a", "WS-2", "customer", "waiting_interview", subject="другая")
    assert env.run("run", "--subject", "тема") == 0
    out = capsys.readouterr().out
    assert "ждёт ответа: Не хватает" in out and "--subject 'тема'" in out


# --- контракт брифа v1.3: починка GC-18…GC-21 без терминала (#813) ---

PERS_QID = "customer.personas.01"
FUNCS_QID = "customer.functions.01"
NFR_QID = "customer.nfr.01"
OUT_QID = "customer.out_of_scope.01"
CON_QID = "customer.constraints.01"
_OWNERS = ("process", "data", "quality", "operations")
_CATS = ("делать", "не делать", "стоить", "доказывать")


def _row(category: str, eid: str, **extra: str) -> dict[str, Any]:
    return {"words": f"слова {category}", "category": category,
            "constraint": [eid], "assumption": None, **extra}  # fmt: skip


def _complete_answers() -> dict[str, dict]:
    """Бриф, которому v1.3 ничего не находит; тесты портят по одному месту."""
    owners = [
        {"id": f"P-0{n}", "body": f"Владелец {d}", "owns": [d]}
        for n, d in enumerate(_OWNERS, 2)
    ]
    return {
        "customer.goals.01": {"text": "цель", "entries": [{"id": "G-01", "body": "Цель"}]},
        PERS_QID: {
            "text": "аналитики",
            "entries": [{"id": "P-01", "body": "Аналитик"}, *owners],
        },
        FUNCS_QID: {
            "text": "запуск одной командой",
            "entries": [{"id": "FR-01", "body": "Запуск", "Acceptance": "1 команда",
                         "Preference": "запуск одной командой"}],
            "translations": [_row("делать", "FR-01"), _row("не делать", "OUT-01")],
        },  # fmt: skip
        NFR_QID: {
            "text": "быстро и дёшево",
            "entries": [{"id": "NFR-01", "body": "Быстро и дёшево",
                         "category": ["perf", "cost"], "Target": "2 с"}],
            "translations": [_row("стоить", "NFR-01"), _row("доказывать", "NFR-01")],
        },  # fmt: skip
        OUT_QID: {"text": "без мобилки",
                  "entries": [{"id": "OUT-01", "body": "Без мобильного клиента"}]},
    }  # fmt: skip


@dataclass
class V13Session:
    """Фейк discovery для v1.3: ответы и находки GC-18…GC-21 по ним."""

    answers: dict[str, dict]
    sent: list[tuple[str, bool, dict]] = field(default_factory=list)

    def answer(
        self,
        session_id: str,
        role: str,
        file: Path,
        cwd: Path,
        *,
        question: str | None = None,
        supersede: bool = False,
    ) -> iv.DiscoveryReply:
        payload = yaml.safe_load(file.read_text(encoding="utf-8"))
        assert question is not None and supersede
        self.sent.append((question, supersede, payload))
        self.answers.pop(question, None)
        self.answers[question] = payload
        return iv.DiscoveryReply(20, {"operation": {"status": "ok"}}, "")

    def status(self, session_id: str, cwd: Path) -> iv.DiscoveryReply:
        lines = self.findings()
        return iv.DiscoveryReply(11 if lines else 0, {"findings": lines}, "")

    def _entries(self) -> list[dict]:
        return [e for a in self.answers.values() for e in a.get("entries") or []]

    def view(self, session_id: str) -> SessionView:
        held = {e["id"]: q for q, a in self.answers.items() for e in a["entries"]}
        return SessionView(
            frame="customer",
            required={},
            optional={},
            entries=[
                Entry(
                    e["id"],
                    e["body"],
                    {k: v for k, v in e.items() if k not in ("id", "body")},
                )
                for e in self._entries()
            ],  # fmt: skip
            declared=list(held),
            holders=held,
            answers=dict(self.answers),
            answered_keys=[],
            readiness="incomplete" if self.findings() else "ready",
            findings=[],
            asked={q: q.split(".")[1] for q in self.answers},
        )

    def findings(self) -> list[str]:
        entries = self._entries()
        nfrs = [e for e in entries if e["id"].startswith("NFR-")]
        lines = [
            f"GC-20 error [{e['id']}] нет **Category**: без неё perf/cost-цель "
            "не проверить"
            for e in nfrs
            if not e.get("category")
        ]
        lines += [
            f"GC-20 error [Category: {k}] нет ни одной NFR категории {k}"
            for k in ("perf", "cost")
            if not any(k in (e.get("category") or []) for e in nfrs)
        ]
        owned = {d for e in entries if e["id"][0] in "PQ" for d in e.get("owns") or []}
        lines += [
            f"GC-21 error [owns: {d}] владелец не назван: нет P или открытого Q"
            for d in _OWNERS
            if d not in owned
        ]
        rows = [r for a in self.answers.values() for r in a.get("translations") or []]
        if not rows:
            return [
                *lines,
                "GC-18 error [Translation table] секции нет или в ней нет строк",
            ]
        seen = {r["category"] for r in rows}
        lines += [
            f"GC-19 error [Translation table] нет ни одной строки категории «{c}»"
            for c in _CATS
            if c not in seen
        ]
        lines += [
            f"GC-18 error [Translation table#{n}] у допущения нет подтверждающего"
            for n, r in enumerate(rows, 1)
            if r.get("assumption") and not (r.get("confirmer") and r.get("due"))
        ]
        return lines


def _v13(monkeypatch: pytest.MonkeyPatch, answers: dict[str, dict]) -> V13Session:
    sess = V13Session(answers)
    monkeypatch.setattr(dfl, "load_session", sess.view)
    monkeypatch.setattr(dfl, "_discovery_status", sess.status)
    monkeypatch.setattr(dfl, "_discovery_answer", sess.answer)
    _stopped(INCOMPLETE)
    return sess


def _reply(env: Env, key: str, text: str) -> dict:  # noqa: F811
    rec = _pending()
    assert rec is not None and rec["key"] == key
    assert env.run("reply", "--id", rec["id"], "--text", text) == 0
    return rec


def _without_owner(domain: str) -> dict[str, dict]:
    answers = _complete_answers()
    pers = answers[PERS_QID]
    pers["entries"] = [e for e in pers["entries"] if domain not in e.get("owns", [])]
    return answers


def test_owner_reply_writes_p_with_owns(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = _v13(monkeypatch, _without_owner("process"))
    assert env.run("run") == 0
    rec = _reply(env, "owner:process", "Иван, владелец продукта")
    assert "процесс" in rec["text"]
    assert env.run("run") == 0
    qid, supersede, payload = sess.sent[-1]
    assert (qid, supersede) == (PERS_QID, True)
    new = payload["entries"][-1]
    assert new["id"].startswith("P-") and new["owns"] == ["process"]
    assert "Иван, владелец продукта" in new["body"]
    assert sess.findings() == [] and fakes.calls == []


def test_owner_unknown_reply_writes_open_q(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = _v13(monkeypatch, _without_owner("data"))
    assert env.run("run") == 0
    _reply(env, "owner:data", "никто")
    assert env.run("run") == 0
    new = sess.sent[-1][2]["entries"][-1]
    assert new["id"].startswith("Q-") and new["owns"] == ["data"]
    assert new["owner_role"] == "analyst" and "никто" not in new["body"]
    assert len(sess.sent) == 1


def test_owner_skip_reply_marks_skipped(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«не знаю» — пропуск, как везде в dfl: ничего не записано."""
    sess = _v13(monkeypatch, _without_owner("quality"))
    assert env.run("run") == 0
    _reply(env, "owner:quality", "не знаю")
    assert env.run("run") == 0
    assert sess.sent == []


def test_assumptions_reply_bad_date_refused_question_kept(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = _complete_answers()
    rows = answers[FUNCS_QID]["translations"]
    rows[0]["assumption"] = "одна команда"
    sess = _v13(monkeypatch, answers)
    assert env.run("run") == 0
    for bad in ("владелец продукта, до конца месяца", "владелец, 01.01.2020"):
        rec = _reply(env, "assumptions", bad)
        assert rec["data"]["rows"] == [
            {"words": "слова делать", "category": "делать", "constraint": ["FR-01"],
             "assumption": "одна команда"},
        ]  # fmt: skip
        capsys.readouterr()
        assert env.run("run") == 2
        err = capsys.readouterr().err
        assert "дата" in err
        again = _pending()
        assert again is not None and again["id"] == rec["id"]
        assert sess.sent == []
    _reply(env, "assumptions", "владелец продукта, 2099-12-01")
    assert env.run("run") == 0
    qid, _sup, payload = sess.sent[-1]
    assert qid == FUNCS_QID
    row = payload["translations"][0]
    assert (row["confirmer"], row["due"]) == ("владелец продукта", "2099-12-01")
    assert sess.findings() == []


def test_table_auto_rows_sent_once(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строки из `Preference` — без вопроса, один раз за проход; затем
    вопрос по недостающей категории; второй `dfl run` строки не шлёт."""
    answers = _complete_answers()
    for a in answers.values():
        a.pop("translations", None)
    sess = _v13(monkeypatch, answers)
    assert env.run("run") == 0
    assert len(sess.sent) == 1
    qid, _sup, payload = sess.sent[0]
    assert qid == FUNCS_QID
    assert payload["translations"] == [
        {"words": "запуск одной командой", "category": "делать",
         "constraint": ["FR-01"], "assumption": "1 команда"},
    ]  # fmt: skip
    assert "table:auto" in ir.load_marks(_rd())["done"]
    rec = _pending()
    assert rec is not None and rec["key"] == "table:не делать"
    assert ir.HUMAN_PLACEHOLDER in rec["data"]["prompt"]
    assert rec["data"]["holder"] == OUT_QID
    assert env.run("run") == 0
    assert len(sess.sent) == 1
    assert (_pending() or {}).get("id") == rec["id"]


def test_category_reply_unknown_word_refused(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = _complete_answers()
    answers[NFR_QID]["entries"].append({"id": "NFR-02", "body": "Отчёт красивый"})
    sess = _v13(monkeypatch, answers)
    assert env.run("run") == 0
    rec = _reply(env, "category:NFR-02", "фиолетовая")
    assert rec["data"]["hypothesis"] == []
    capsys.readouterr()
    assert env.run("run") == 2
    assert "категори" in capsys.readouterr().err
    assert (_pending() or {}).get("id") == rec["id"] and sess.sent == []
    _reply(env, "category:NFR-02", "безопасность")
    assert env.run("run") == 0
    nfr = sess.sent[-1][2]["entries"][-1]
    assert (nfr["id"], nfr["category"]) == ("NFR-02", ["security"])
    assert sess.sent[-1][0] == NFR_QID


def test_nfr_unknown_reply_writes_nfr_and_q(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«неизвестно» на `nfr:cost` — `NFR` с целью из нового `Q` аналитика."""
    answers = _complete_answers()
    answers[NFR_QID]["entries"][0]["category"] = ["perf"]
    sess = _v13(monkeypatch, answers)
    assert env.run("run") == 0
    _reply(env, "nfr:cost", "неизвестно")
    assert env.run("run") == 0
    qid, _sup, payload = sess.sent[-1]
    nfr, q = payload["entries"][-2:]
    assert qid == NFR_QID
    assert (nfr["id"], nfr["category"]) == ("NFR-02", ["cost"])
    assert nfr["Target"] == f"значение из {q['id']}"
    assert q["id"].startswith("Q-") and q["owner_role"] == "analyst"
    assert sess.findings() == []


# --- сквозной: починка v1.3 без терминала до брифа без GC-18…GC-21 (#813) ---

_V13_TOPICS = {"nfr": "NFR", "constraints": "CON", "out_of_scope": "OUT"}
_V13_RULES = (gc.check_translation_table, gc.check_numeric_targets, gc.check_owners)
_TABLE_HEAD = (
    "| слова | категория | ограничение | допущение | подтверждает | срок |\n"
    "|---|---|---|---|---|---|"
)


def _entry_lines(e: dict[str, Any]) -> list[str]:
    """Пункт брифа в формате секций контракта §2 (как рендерит discovery)."""
    lines = [f"- **{e['id']}** {e['body']}"]
    lines += [f"  **{k}**: {e[k]}" for k in ("Acceptance", "Target") if e.get(k)]
    if e.get("category"):
        lines.append(f"  **Category**: {', '.join(e['category'])}")
    if e.get("owns"):
        lines.append(f"  owns: {', '.join(e['owns'])}")
    if e.get("resolved") is True or e.get("status") == "resolved":
        lines.append("  resolved: true")
    return lines


def _row_line(r: dict[str, Any]) -> str:
    cells = [r["words"], r["category"], ", ".join(r["constraint"])]
    cells += [r.get(k) or "—" for k in ("assumption", "confirmer", "due")]
    return "| " + " | ".join(cells) + " |"


def brief_body(answers: dict[str, dict]) -> str:
    """Тело брифа из ответов сессии: пункты и `## Translation table`."""
    held = {e["id"]: e for a in answers.values() for e in a.get("entries") or []}
    lines = [line for e in held.values() for line in _entry_lines(e)]
    rows = [r for a in answers.values() for r in a.get("translations") or []]
    if rows:
        lines += ["", gc.TRANSLATION_HEADING, "", _TABLE_HEAD]
        lines += [_row_line(r) for r in rows]
    return "\n".join(lines) + "\n"


class GateV13Session(V13Session):
    """`V13Session`, чьи находки GC-18…GC-21 считает сам vendored
    `gate_check` (его правила v1.3) по брифу из того, что сессия держит;
    темы — для раскладки скила (`dfl answer --items` проверяет префиксы)."""

    def view(self, session_id: str) -> SessionView:
        return replace(super().view(session_id), optional=_V13_TOPICS)

    def findings(self) -> list[str]:
        body = brief_body(self.answers)
        brief = gc.Brief(meta={}, entries=gc.parse_body(body), body=body)
        out: list[str] = []

        def report(rule: str, ref: str, msg: str) -> None:
            out.append(str(gc.Finding(rule, "error", ref, msg)))

        for rule in _V13_RULES:
            rule(brief, report)
        return out


def _live_answers() -> dict[str, dict]:
    """Как в живом прогоне: четыре NFR без категории, ни perf, ни cost,
    владельцев нет, таблица пуста, у FR/NFR/OUT есть `Preference`."""
    nfrs = [
        {"id": "NFR-01", "body": "Доступ только сотрудникам склада"},
        {"id": "NFR-02", "body": "Работает без отказов днём"},
        {"id": "NFR-03", "body": "Хранить отчёты по закону",
         "Target": "5 лет хранения", "Preference": "храним, как требует закон"},
        {"id": "NFR-04", "body": "Понятные подписи в отчёте"},
    ]  # fmt: skip
    return {
        "customer.goals.01": {
            "text": "меньше сверки",
            "entries": [{"id": "G-01", "body": "Меньше ручной сверки"}],
        },
        PERS_QID: {
            "text": "аналитики",
            "entries": [{"id": "P-01", "body": "Аналитик склада"}],
        },
        FUNCS_QID: {
            "text": "запуск одной командой",
            "entries": [
                {
                    "id": "FR-01",
                    "body": "Запуск отчёта",
                    "Acceptance": "1 команда",
                    "Preference": "запуск одной командой",
                }
            ],
        },
        NFR_QID: {"text": "надёжно и по закону", "entries": nfrs},
        OUT_QID: {
            "text": "без мобилки",
            "entries": [
                {
                    "id": "OUT-01",
                    "body": "Без мобильного клиента",
                    "Preference": "телефон не нужен",
                }
            ],
        },
    }


def _key() -> str:
    rec = _pending()
    assert rec is not None and rec["answered"] is False
    return str(rec["key"])


def _reply_run(env: Env, key: str, text: str) -> dict:  # noqa: F811
    rec = _reply(env, key, text)
    assert env.run("run") == 0
    return rec


def _items_for(env: Env, key: str, text: str, items: list[dict]) -> None:  # noqa: F811
    rec = _pending()
    assert rec is not None and rec["key"] == key
    _answer_items(env, rec["id"], text, items)


def test_customer_v13_repair_end_to_end_without_terminal(
    env: Env,  # noqa: F811
    fakes: Discovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Дизайн §9: customer-бриф живого прогона чинится только `dfl run`,
    `dfl reply` и `dfl answer --items` (агент и разговор — фейки, падающие
    при вызове); итог — vendored `gate_check` не находит GC-18…GC-21."""
    sess = GateV13Session(_live_answers())
    monkeypatch.setattr(dfl, "load_session", sess.view)
    monkeypatch.setattr(dfl, "_discovery_status", sess.status)
    monkeypatch.setattr(dfl, "_discovery_answer", sess.answer)
    _stopped(INCOMPLETE)
    start = sess.findings()
    assert {f.split()[0] for f in start} == {"GC-18", "GC-20", "GC-21"}
    assert sum("нет **Category**" in f for f in start) == 4

    # Категории NFR: гипотеза — «да», без гипотезы — словом.
    assert env.run("run") == 0
    for eid, hypothesis in (
        ("NFR-01", ["security"]),
        ("NFR-02", ["availability"]),
        ("NFR-03", ["compliance"]),
    ):
        rec = _reply_run(env, f"category:{eid}", "да")
        assert rec["data"]["hypothesis"] == hypothesis
    rec = _reply_run(env, "category:NFR-04", "доступность")
    assert rec["data"]["hypothesis"] == []
    assert not any("нет **Category**" in f for f in sess.findings())

    # perf — раскладкой скила; cost — «неизвестно» (NFR с целью из Q).
    _items_for(
        env,
        "nfr:perf",
        "отчёт не дольше пары секунд",
        [{"prefix": "NFR", "body": "Отчёт быстро", "target": "не более 2 секунд"}],
    )
    _reply_run(env, "nfr:cost", "неизвестно")
    assert not any(f.startswith("GC-20") for f in sess.findings())

    # Владельцы: имя — P, «никто» — открытый Q.
    for domain, text in (
        ("process", "Иван Петров, начальник склада"),
        ("data", "никто"),
        ("quality", "Мария, старший аналитик"),
        ("operations", "служба поддержки"),
    ):
        _reply_run(env, f"owner:{domain}", text)
    assert not any(f.startswith("GC-21") for f in sess.findings())

    # Строки из `Preference` — без вопроса; «стоить» — вопросом.
    assert "table:auto" in ir.load_marks(_rd())["done"]
    rows = [r for a in sess.answers.values() for r in a.get("translations") or []]
    assert sorted(r["category"] for r in rows) == ["делать", "доказывать", "не делать"]
    _items_for(
        env,
        "table:стоить",
        "не дороже 100 тысяч",
        [{"prefix": "CON", "body": "Бюджет до 100 тыс. ₽",
          "acceptance": "не более 100 000 ₽"}],
    )  # fmt: skip

    # Допущения — один ответ «кто, срок» на все строки.
    rec = _reply_run(env, "assumptions", "владелец продукта, 2099-12-01")
    assert len(rec["data"]["rows"]) == 3

    assert sess.findings() == []
    assert gc.translation_rows(brief_body(sess.answers)) is not None
    assert _pending() is None
    assert all(sup for _q, sup, _p in sess.sent) and fakes.calls == []
