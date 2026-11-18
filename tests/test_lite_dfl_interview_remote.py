"""`dfl run` без терминала: вопросы интервью через pending (#766 срез 3).

Окружение — фикстура `env` (`tests/test_lite_dfl.py`); discovery подменена
(`_discovery_status`, `_discovery_answer`, `load_session`), агент — фейк,
падающий при вызове: путь без терминала его не зовёт.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from governance import interview as iv
from governance import run_lock, spec_loop
from governance import run_state as rs
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
        return 0

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

    # Вопрос 2 — пропуск через `dfl reply`; конец интервью с неполной темой.
    q2 = _pending_json(env, capsys)
    assert q2["key"] == "q-functions" and q2["text"].startswith("Вопрос 2 (")
    assert env.run("reply", "--id", q2["id"], "--text", "пропустить") == 0
    assert env.run("run") == 0
    assert sess.sent[-1] == ("q-functions", False, {"text": "пропустить"})
    assert rs.load(RUN).status == "stopped_interview"

    # Добор непокрытой темы — раскладкой скила держателю темы.
    assert env.run("run") == 0
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
