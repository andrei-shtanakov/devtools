"""`dfl answer --items f.json --id <id>`: раскладка скила проверяется dfl
(#766 срез 3, решение R1).

Окружение — фикстура `env` (`tests/test_lite_dfl.py`); discovery подменена
(`_discovery_answer` пишет присланный YAML в журнал, `load_session`), агент —
фейк, падающий при вызове: раскладку сделал скил. `dfl.run` после ответа —
подмена-журнал.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from governance import interview as iv
from governance import run_lock
from governance import run_state as rs
from lite import dfl, pending
from lite import gate_repair as gr
from lite import interview_remote as ir
from lite.interview_state import Entry, SessionView
from lite.structure import Question as Asked
from tests.test_lite_dfl import Env, _stopped, _waiting, env  # noqa: F401

RUN = "WS-1-a"
ITEMS = "items.json"


@dataclass
class Sent:
    """Подмена `_discovery_answer`: (вопрос, supersede, YAML как словарь)."""

    code: int = 20
    calls: list[tuple[str | None, bool, dict[str, Any]]] = field(default_factory=list)

    def __call__(
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
        self.calls.append((question, supersede, payload))
        reason = "payload missing text" if self.code in (1, 2) else ""
        envelope = {"operation": {"status": "ok", "reason": reason}}
        return iv.DiscoveryReply(self.code, envelope, "")


def _customer_view() -> SessionView:
    """customer: цель G-01 у ответа `customer.goals.01`."""
    goal = {"id": "G-01", "body": "Цель", "Priority": "Must"}
    return SessionView(
        frame="customer",
        required={"goals": "G", "jobs": "J", "functions": "FR"},
        optional={"metrics": "M"},
        entries=[Entry("G-01", "Цель", {"Priority": "Must"})],
        declared=[],
        holders={"G-01": "customer.goals.01"},
        answers={"customer.goals.01": {"text": "цель", "entries": [goal]}},
        answered_keys=["goals"],
        readiness="incomplete",
        asked={"customer.goals.01": "goals"},
    )


def _repair_view() -> SessionView:
    """FR-01 и IF-01 у ответа `q.a`, AP-01, S-01, CON-01 — у `q.b`."""
    a = [
        {"id": "FR-01", "body": "Отчёт", "Priority": "Must"},
        {"id": "IF-01", "body": "API склада"},
    ]
    b = [
        {"id": "AP-01", "body": "Пакетная загрузка"},
        {"id": "S-01", "body": "Склад"},
        {"id": "CON-01", "body": "Бюджет"},
    ]
    records = [*a, *b]
    return SessionView(
        frame="engineer",
        required={"functions": "FR", "interfaces": "IF", "systems": "S"},
        optional={"approaches": "AP", "constraints": "CON"},
        entries=[
            Entry(r["id"], r["body"], {k: v for k, v in r.items() if k[0].isupper()})
            for r in records
        ],
        declared=[],
        holders={
            **{r["id"]: "q.a" for r in a},
            **{r["id"]: "q.b" for r in b},
        },
        answers={
            "q.a": {"text": "ответ а", "entries": a},
            "q.b": {"text": "ответ б", "entries": b},
        },
        answered_keys=["functions", "interfaces", "systems"],
        readiness="incomplete",
        asked={"q.a": "functions", "q.b": "systems"},
    )


@dataclass
class Fakes:
    sent: Sent
    runs: list[tuple[Any, ...]]


def _fakes(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    view: SessionView,
) -> Fakes:
    monkeypatch.setattr(dfl, "load_session", lambda sid: view)
    sent = Sent()
    monkeypatch.setattr(dfl, "_discovery_answer", sent)
    monkeypatch.setattr(dfl, "_chat", lambda state: pytest.fail("разговор"))

    def no_agent(*a: object, **kw: object) -> object:
        pytest.fail("агент без терминала")

    monkeypatch.setattr(dfl.st, "claude_agent", no_agent)
    runs: list[tuple[Any, ...]] = []

    def fake_run(*a: Any, **kw: Any) -> int:
        runs.append((*a, kw))
        return 0

    monkeypatch.setattr(dfl, "run", fake_run)
    return Fakes(sent, runs)


def _rd() -> Path:
    return rs.run_dir(RUN)


def _post(found: tuple[pending.Question, dict] | None) -> dict:
    assert found is not None
    q, data = found
    pending.post(_rd(), q, data)
    rec = pending.load(_rd())
    assert rec is not None
    return rec


def _items(env: Env, text: str, items: list[dict]) -> None:  # noqa: F811
    doc = {"text": text, "items": items}
    (env.tmp / "target" / ITEMS).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def _question_rec(view: SessionView) -> dict:
    return _post(ir._question(Asked("q-1", "functions", "Что делает?"), view))


def test_items_answer_question(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(
        env,
        "Нужна выгрузка под новую цель",
        [
            {"prefix": "G", "ref": "g-a", "body": "Новая цель", "priority": "must"},
            {
                "prefix": "FR",
                "body": "Выгрузка",
                "acceptance": "файл за минуту",
                "traces": ["g-a"],
            },
        ],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    assert len(fakes.sent.calls) == 1
    question, supersede, payload = fakes.sent.calls[0]
    assert (question, supersede) == ("q-1", False)
    assert payload["text"] == "Нужна выгрузка под новую цель"
    ids = [e["id"] for e in payload["entries"]]
    assert ids == ["G-02", "FR-01"]
    assert payload["entries"][1]["traces"] == ["G-02"]
    assert payload["entries"][0]["Priority"] == "Must"
    assert pending.load(_rd()) is None
    assert len(fakes.runs) == 1 and fakes.runs[0][-1] == {"chat": False}
    assert ir.load_marks(_rd()) == {"skipped": [], "done": []}


def test_items_stale_id_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(env, "да", [])
    assert env.run("answer", "--items", ITEMS, "--id", "чужой") == 2
    assert "вопрос уже другой" in capsys.readouterr().err
    assert fakes.sent.calls == [] and fakes.runs == []
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]


def test_items_no_pending_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes = _fakes(env, monkeypatch, _customer_view())
    _waiting(RUN, "WS-1", "customer")
    _items(env, "да", [])
    assert env.run("answer", "--items", ITEMS, "--id", "x") == 2
    assert "нет вопроса, ждущего ответа" in capsys.readouterr().err
    assert fakes.sent.calls == []


def test_items_not_interview_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes = _fakes(env, monkeypatch, _customer_view())
    _waiting(RUN, "WS-1", "customer")
    rec = _post((pending.Question(pending.MANUAL, "m1", "Проверьте"), {}))
    _items(env, "да", [])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "не вопрос интервью" in capsys.readouterr().err
    assert fakes.sent.calls == []


def test_items_bad_form_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(
        env,
        "поправка",
        [{"prefix": "FR", "id": "FR-09", "body": "Нет такого", "acceptance": "x"}],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    err = capsys.readouterr().err
    assert "раскладка не принята" in err
    assert "id «FR-09» нет среди записанных пунктов" in err
    assert fakes.sent.calls == [] and fakes.runs == []
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]


def test_items_missing_criterion_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(env, "выгрузка", [{"prefix": "FR", "body": "Выгрузка"}])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "нет критерия" in capsys.readouterr().err
    assert fakes.sent.calls == []


@pytest.mark.parametrize(
    "content",
    ["не json", json.dumps({"items": []}), json.dumps({"text": "  ", "items": []})],
)
def test_items_bad_file_refused_with_form_hint(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    (env.tmp / "target" / ITEMS).write_text(content, encoding="utf-8")
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert '{"text":' in capsys.readouterr().err
    assert fakes.sent.calls == []


def test_items_discovery_refusal_keeps_pending(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    fakes.sent.code = 2
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(env, "без пунктов", [])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "discovery не принял ответ (payload missing text)" in (
        capsys.readouterr().err
    )
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]
    assert fakes.runs == []


def test_items_topup_appends_to_holder(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._top_ups(view)[0])
    assert rec["key"] == "topic:jobs"
    _items(env, "Сверяем остатки", [{"prefix": "J", "body": "Сверка остатков"}])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    assert len(fakes.sent.calls) == 1
    question, supersede, payload = fakes.sent.calls[0]
    assert (question, supersede) == ("customer.goals.01", True)
    assert payload["text"] == "цель\n\nСверяем остатки"
    assert [e["id"] for e in payload["entries"]] == ["G-01", "J-01"]
    assert payload["entries"][0] == {"id": "G-01", "body": "Цель", "Priority": "Must"}
    assert ir.load_marks(_rd())["done"] == ["topic:jobs"]
    assert pending.load(_rd()) is None and len(fakes.runs) == 1


def test_items_repair_criterion_updates_entry_and_marks_done(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Правила терминала (`gate_repair._criterion_items`): из раскладки —
    только критерий; body, Priority, traces — прежние; слова человека — в
    `Preference`, текст — заметка «Уточнение к …»."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    _items(
        env,
        "Отчёт готов за минуту",
        [
            {
                "prefix": "FR",
                "id": "FR-01",
                "body": "Переписанный отчёт",
                "priority": "Could",
                "acceptance": "отчёт за минуту",
            }
        ],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    assert len(fakes.sent.calls) == 1
    question, supersede, payload = fakes.sent.calls[0]
    assert (question, supersede) == ("q.a", True)
    assert payload["text"] == "ответ а\n\nУточнение к «Отчёт»: Отчёт готов за минуту"
    assert [e["id"] for e in payload["entries"]] == ["FR-01", "IF-01"]
    fr = payload["entries"][0]
    assert fr["Acceptance"] == "отчёт за минуту"
    assert (fr["body"], fr["Priority"]) == ("Отчёт", "Must")
    assert fr["Preference"] == "Отчёт готов за минуту"
    assert ir.load_marks(_rd())["done"] == ["criterion:FR-01"]
    assert pending.load(_rd()) is None and len(fakes.runs) == 1


def _traces_items() -> list[dict]:
    return [
        {"prefix": "IF", "id": "IF-01", "body": "API склада", "traces": ["S-01"]},
        {
            "prefix": "AP",
            "id": "AP-01",
            "body": "Пакетная загрузка",
            "traces": ["S-01", "CON-01"],
        },
    ]


def test_items_repair_traces_resends_each_holder(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GC-13: IF-01 у `q.a`, AP-01 у `q.b` — каждому держателю своя группа и
    заметка терминала, слова человека чужим ответам не дописываются."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    assert rec["data"]["holder"] == "q.a"
    _items(env, "Оба про склад, подход ещё про бюджет", _traces_items())
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    assert [(q, sup) for q, sup, _p in fakes.sent.calls] == [
        ("q.a", True),
        ("q.b", True),
    ]
    a, b = (p for _q, _s, p in fakes.sent.calls)
    assert [e["id"] for e in a["entries"]] == ["FR-01", "IF-01"]
    assert [e["id"] for e in b["entries"]] == ["AP-01", "S-01", "CON-01"]
    assert a["entries"][1]["traces"] == ["S-01"]
    assert b["entries"][0]["traces"] == ["S-01", "CON-01"]
    assert a["text"] == f"ответ а\n\n{gr.TRACES_NOTE}"
    assert b["text"] == f"ответ б\n\n{gr.TRACES_NOTE}"
    assert ir.load_marks(_rd())["done"] == ["traces:IF-01,AP-01"]


def test_items_repair_traces_subset_accepted(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Связи только части пунктов — как в терминале: остальные вернутся
    находками следующего прохода."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    _items(env, "Только API про склад", _traces_items()[1:])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    assert [q for q, _s, _p in fakes.sent.calls] == ["q.b"]
    assert ir.load_marks(_rd())["done"] == ["traces:IF-01,AP-01"]


def test_items_repair_retry_does_not_duplicate_note(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор после частичного отказа: ответ держателя уже кончается
    заметкой — она не дописывается второй раз, записи заменяются по id."""
    view = _repair_view()
    view.answers["q.a"]["text"] = f"ответ а\n\n{gr.TRACES_NOTE}"
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    _items(env, "Оба про склад", _traces_items())
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    a, b = (p for _q, _s, p in fakes.sent.calls)
    assert a["text"] == f"ответ а\n\n{gr.TRACES_NOTE}"
    assert [e["id"] for e in a["entries"]] == ["FR-01", "IF-01"]
    assert b["text"] == f"ответ б\n\n{gr.TRACES_NOTE}"


def test_items_repair_partial_refusal_keeps_pending(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    real = fakes.sent.__call__

    def second_refused(*a: Any, **kw: Any) -> iv.DiscoveryReply:
        fakes.sent.code = 2 if fakes.sent.calls else 20
        return real(*a, **kw)

    monkeypatch.setattr(dfl, "_discovery_answer", second_refused)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    _items(env, "Оба про склад", _traces_items())
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "discovery не принял ответ" in capsys.readouterr().err
    assert [q for q, _s, _p in fakes.sent.calls] == ["q.a", "q.b"]
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]
    assert ir.load_marks(_rd())["done"] == [] and fakes.runs == []


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"prefix": "S", "body": "Новая система"}],
        [{"prefix": "FR", "id": "FR-01", "body": "Отчёт", "acceptance": "x"}],
    ],
)
def test_items_repair_traces_empty_new_or_foreign_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    items: list[dict],
) -> None:
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    _items(env, "ответ", items)
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "раскладка не принята" in capsys.readouterr().err
    assert fakes.sent.calls == []
    assert ir.load_marks(_rd())["done"] == []


@pytest.mark.parametrize(
    "extra",
    [
        [{"prefix": "S", "body": "Новая система"}],
        [{"prefix": "IF", "id": "IF-01", "body": "API склада", "traces": ["S-01"]}],
        [{"prefix": "Q", "ref": "q-1", "body": "Ни к чему", "owner_role": "analyst"}],
    ],
)
def test_items_repair_criterion_foreign_new_or_unmentioned_q_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[dict],
) -> None:
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    fr = {"prefix": "FR", "id": "FR-01", "body": "Отчёт", "acceptance": "за минуту"}
    _items(env, "ответ", [fr, *extra])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "раскладка не принята" in capsys.readouterr().err
    assert fakes.sent.calls == []
    assert ir.load_marks(_rd())["done"] == []


def test_items_repair_criterion_empty_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    _items(env, "ответ", [])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "раскладка не принята" in capsys.readouterr().err
    assert fakes.sent.calls == []


def test_items_refused_while_run_locked(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    _items(env, "да", [])
    with run_lock.run_lock(RUN):
        assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "исполняется другим процессом" in capsys.readouterr().err
    assert fakes.sent.calls == [] and fakes.runs == []
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]


def test_items_without_id_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes = _fakes(env, monkeypatch, _customer_view())
    _waiting(RUN, "WS-1", "customer")
    _items(env, "да", [])
    assert env.run("answer", "--items", ITEMS) == 2
    assert "--items" in capsys.readouterr().err
    assert env.run("answer", "--id", "x") == 2
    assert fakes.sent.calls == []


@pytest.mark.parametrize(
    "extra", [["--file", "a.yaml"], ["--question", "q-1"], ["--supersede"]]
)
def test_items_with_file_flags_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
) -> None:
    fakes = _fakes(env, monkeypatch, _customer_view())
    _waiting(RUN, "WS-1", "customer")
    _items(env, "да", [])
    assert env.run("answer", "--items", ITEMS, "--id", "x", *extra) == 2
    assert fakes.sent.calls == []


def test_answer_help_lists_items(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        dfl.main(["--cwd", ".", "answer", "--help"])
    out = capsys.readouterr().out
    assert "--items" in out and "--id" in out


def test_items_repair_criterion_new_research_q_to_holder(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Новый Q, на который ссылается критерий, — как в терминале
    (`gate_repair._criterion_items`): допустим и уходит держателю пункта."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    _items(
        env,
        "Быстро, а насколько — узнаем",
        [
            {
                "prefix": "FR",
                "id": "FR-01",
                "body": "Отчёт",
                "acceptance": "отчёт быстрее, чем q-1",
            },
            {
                "prefix": "Q",
                "ref": "q-1",
                "body": "Сколько ждут сейчас?",
                "owner_role": "analyst",
            },
        ],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    [(question, _sup, payload)] = fakes.sent.calls
    assert question == "q.a"
    assert [e["id"] for e in payload["entries"]] == ["FR-01", "IF-01", "Q-01"]
    assert payload["entries"][0]["Acceptance"] == "отчёт быстрее, чем Q-01"


def test_items_repair_criterion_agreement_takes_hypothesis(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Согласие с гипотезой скила — ветка «да» терминала: `Preference` не
    трогается, заметка «Критерий для …» с текстом гипотезы (id Q — текстом)."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    _items(
        env,
        "Да, так",
        [
            {
                "prefix": "FR",
                "id": "FR-01",
                "body": "Отчёт",
                "acceptance": "отчёт быстрее, чем q-1",
            },
            {
                "prefix": "Q",
                "ref": "q-1",
                "body": "Сколько ждут сейчас?",
                "owner_role": "analyst",
            },
        ],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    [(question, _sup, payload)] = fakes.sent.calls
    assert question == "q.a"
    assert payload["text"] == (
        "ответ а\n\nКритерий для «Отчёт»: отчёт быстрее, чем «Сколько ждут сейчас?»"
    )
    assert [e["id"] for e in payload["entries"]] == ["FR-01", "IF-01", "Q-01"]
    assert "Preference" not in payload["entries"][0]
    assert ir.load_marks(_rd())["done"] == ["criterion:FR-01"]


def test_items_repair_traces_only_traces_taken(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Из пункта связей берутся только `traces`: body и прочее — из записи."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    item = {
        "prefix": "IF",
        "id": "IF-01",
        "body": "Совсем другое",
        "priority": "Must",
        "traces": ["S-01", "S-01"],
    }
    _items(env, "API про склад", [item])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 0
    [(question, _sup, payload)] = fakes.sent.calls
    assert question == "q.a"
    assert [e["id"] for e in payload["entries"]] == ["FR-01", "IF-01"]
    assert payload["entries"][1] == {
        "id": "IF-01",
        "body": "API склада",
        "traces": ["S-01"],
    }


@pytest.mark.parametrize(
    ("traces", "bad"),
    [(["CON-01"], "CON-01"), (["S-09"], "S-09"), ([], "—")],
)
def test_items_repair_traces_invalid_target_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    traces: list[str],
    bad: str,
) -> None:
    """IF связывается только с S (`gate_repair._TRACE_TARGETS`), цель —
    действующий пункт; иное — отказ с причиной, а не тихий пропуск."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._traces(["IF-01", "AP-01"], view))
    item = {"prefix": "IF", "id": "IF-01", "body": "API склада", "traces": traces}
    _items(env, "ответ", [item, _traces_items()[1]])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    err = capsys.readouterr().err
    assert "раскладка не принята" in err and f"не годится: {bad}" in err
    assert fakes.sent.calls == []
    assert ir.load_marks(_rd())["done"] == []


def test_items_stale_interview_refused_and_dropped(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Финальное ревью I3: интервью уже кончилось (прогон не ждёт ответа и не
    остановлен неполным) — отказ; устаревший вопрос и отметки сняты, в
    discovery ничего не уходит."""
    view = _customer_view()
    fakes = _fakes(env, monkeypatch, view)
    state = _waiting(RUN, "WS-1", "customer")
    rec = _question_rec(view)
    ir.mark(_rd(), "topic:jobs", "skipped")
    state.status = "brief_ready"
    rs.save(state)
    _items(env, "ответ", [{"prefix": "FR", "body": "Отчёт", "priority": "must"}])
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    assert "уже не ждёт ответа" in capsys.readouterr().err
    assert pending.load(_rd()) is None and not (_rd() / ir.PASS_FILE).exists()
    assert fakes.sent.calls == [] and fakes.runs == []


@pytest.mark.parametrize("text", ["нет", "Не так.", "нет Q-99", "Q-99"])
def test_items_repair_criterion_bare_no_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
) -> None:
    """Финальное ревью M1: голое несогласие или ответ, пустой без id, — как в
    терминале (`gate_repair._confirmation`): в `Preference` не пишется,
    отказ с просьбой сказать словами; вопрос остаётся."""
    view = _repair_view()
    fakes = _fakes(env, monkeypatch, view)
    _stopped("status вернул 11: findings в /r/interview-findings.json")
    rec = _post(ir._criterion("FR-01", view))
    _items(
        env,
        text,
        [{"prefix": "FR", "id": "FR-01", "body": "Отчёт", "acceptance": "за 1 мин"}],
    )
    assert env.run("answer", "--items", ITEMS, "--id", rec["id"]) == 2
    err = capsys.readouterr().err
    assert "своими словами" in err and f"--id {rec['id']}" in err
    assert fakes.sent.calls == [] and fakes.runs == []
    assert (pending.load(_rd()) or {}).get("id") == rec["id"]
