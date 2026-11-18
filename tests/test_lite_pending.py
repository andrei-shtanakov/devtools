"""Тесты `lite.pending`: вопрос наружу, ответ внутрь (#766)."""

from __future__ import annotations

import json

import pytest

from governance import manual_checks as mc
from lite import pending


def test_question_id_stable_and_content_bound():
    a = pending.Question(pending.REVIEW, "design", "этап «дизайн» готов")
    same = pending.Question(pending.REVIEW, "design", "этап «дизайн» готов")
    assert a.id == same.id
    assert a.id != pending.Question(pending.REVIEW, "design", "другой текст").id
    assert a.id.startswith("review:design:")


def test_asker_writes_pending_then_returns_recorded_reply(tmp_path):
    q = pending.Question(pending.REVIEW, "design", "посмотрите")
    ask = pending.asker(tmp_path)
    assert ask(q) is None
    assert pending.load(tmp_path)["id"] == q.id
    pending.record(tmp_path, q.id, "дальше")
    assert ask(q) == "дальше"
    assert pending.load(tmp_path) is None
    assert ask(q) is None  # ответ израсходован — вопрос снова записан
    assert pending.load(tmp_path)["id"] == q.id


def test_record_refuses_stale_id(tmp_path):
    pending.asker(tmp_path)(pending.Question(pending.REVIEW, "a", "x"))
    with pytest.raises(pending.PendingError, match="вопрос уже другой"):
        pending.record(tmp_path, "review:a:00000000", "дальше")


def test_record_refuses_without_pending(tmp_path):
    with pytest.raises(pending.PendingError, match="нет вопроса"):
        pending.record(tmp_path, "review:a:00000000", "дальше")


def test_record_refuses_bad_answer(tmp_path):
    q = pending.Question(pending.REVIEW, "brief", "x")
    pending.asker(tmp_path)(q)
    with pytest.raises(pending.PendingError, match="в терминале"):
        pending.record(tmp_path, q.id, "поправь требования")
    assert not (tmp_path / pending.REPLIES_FILE).exists()


def test_check_review_accepts_remarks_since_slice2():
    assert pending.check(pending.REVIEW, "дальше") is None
    assert pending.check(pending.REVIEW, "поправь требования") is None


def test_check_manual():
    assert pending.check(pending.MANUAL, "1") is None
    assert pending.check(pending.MANUAL, "не выполнено") is None
    assert pending.check(pending.MANUAL, "3") is not None  # оговорка без текста
    assert pending.check(pending.MANUAL, "с оговоркой: только в Chrome") is None
    assert pending.check(pending.MANUAL, "может быть") is not None
    assert pending.parse_manual("2: письмо не пришло") == {
        "verdict": mc.NOT_DONE,
        "note": "письмо не пришло",
    }


def test_take_without_reply_writes_nothing(tmp_path):
    q = pending.Question(pending.MANUAL, "b1", "проверьте")
    assert pending.take(tmp_path, q) is None
    assert list(tmp_path.iterdir()) == []


def test_take_consumes_reply_and_clears_pending(tmp_path):
    q = pending.Question(pending.MANUAL, "b1", "проверьте")
    pending.asker(tmp_path)(q)
    pending.record(tmp_path, q.id, "1")
    assert pending.take(tmp_path, q) == "1"
    assert pending.load(tmp_path) is None
    assert json.loads((tmp_path / pending.REPLIES_FILE).read_text()) == {}


def test_wait_text(tmp_path):
    assert pending.wait_text(tmp_path) == ""
    q = pending.Question(pending.REVIEW, "design", "первая\nвторая")
    pending.asker(tmp_path)(q)
    assert pending.wait_text(tmp_path) == pending.wait_line(q)
    assert pending.wait_line(q).startswith("ждёт ответа: первая — вопрос: dfl pending")
    assert f"--id {q.id}" in pending.wait_line(q)


def test_load_broken_file_is_none(tmp_path):
    (tmp_path / pending.PENDING_FILE).write_text("{oops")
    assert pending.load(tmp_path) is None


def test_reply_surviving_question_change_is_not_taken(tmp_path):
    """Ответ, записанный по прежнему вопросу после его смены, не проходит
    следующий показ того же вопроса (гонка `dfl reply` и `dfl run`, ревью #803)."""
    first = pending.Question(pending.REVIEW, "design", "дизайн готов")
    second = pending.Question(pending.REVIEW, "acceptance", "приёмка готова")
    ask = pending.asker(tmp_path)
    assert ask(first) is None
    pending.record(tmp_path, first.id, "дальше")
    assert ask(first) == "дальше"  # взят штатно
    assert ask(second) is None  # прогон перешёл ко второму вопросу
    # опоздавший reply на первый вопрос дописан в обход проверки (гонка)
    (tmp_path / pending.REPLIES_FILE).write_text(
        json.dumps({first.id: "дальше"}), encoding="utf-8"
    )
    assert pending.take(tmp_path, first) is None
    assert ask(first) is None  # спрошено заново
    assert not (tmp_path / pending.REPLIES_FILE).exists()


def test_reply_to_same_question_survives_rewrite(tmp_path, monkeypatch):
    """Ответ на тот же вопрос, пришедший между `take` и записью вопроса,
    не стирается (ревью #804)."""
    q = pending.Question(pending.MANUAL, "BEH-01", "кнопка")
    ask = pending.asker(tmp_path)
    assert ask(q) is None
    real_take = pending.take

    def take_then_reply(run_dir, question):
        got = real_take(run_dir, question)
        pending.record(run_dir, question.id, "1")  # `dfl reply` в окне
        return got

    monkeypatch.setattr(pending, "take", take_then_reply)
    assert ask(q) is None
    monkeypatch.setattr(pending, "take", real_take)
    assert ask(q) == "1"


def _rec(kind, key="design", options=()):
    return {"kind": kind, "key": key, "options": list(options)}


def test_review_reply_accepts_remarks_except_brief():
    assert pending.check(pending.REVIEW, "убери D-02", _rec(pending.REVIEW)) is None
    assert pending.check(pending.REVIEW, "дальше", _rec(pending.REVIEW)) is None
    brief = _rec(pending.REVIEW, "brief")
    assert pending.check(pending.REVIEW, "убери", brief) == pending.REVIEW_REFUSAL
    assert pending.check(pending.REVIEW, "дальше", brief) is None
    assert pending.check(pending.REVIEW, "  ", _rec(pending.REVIEW)) == (
        pending.REVIEW_REFUSAL
    )
    assert pending.check(pending.REVIEW, "правка") is None


def test_is_done():
    assert pending.is_done(" Дальше ")
    assert not pending.is_done("убери")


def test_confirm_reply_words():
    rec = _rec(pending.REMARKS_CONFIRM, "design:D-02", ("удалить", "оставить"))
    for ok in ("1", "2", "Удалить", "да", "оставить как было", "НЕТ"):
        assert pending.check(pending.REMARKS_CONFIRM, ok, rec) is None
    assert pending.check(pending.REMARKS_CONFIRM, "снять", rec) is not None


def test_edge_reply_range_and_prefixes():
    rec = _rec(pending.EDGE, "a/b/R1", ("вариант А", "вариант Б"))
    assert pending.check(pending.EDGE, "2", rec) is None
    assert pending.check(pending.EDGE, "3", rec) is not None
    assert pending.check(pending.EDGE, "0", rec) is not None
    assert pending.check(pending.EDGE, "Своё: иначе", rec) is None
    assert pending.check(pending.EDGE, "СВОЕ: иначе", rec) is None
    assert pending.check(pending.EDGE, "не согласен:", rec) is not None
    assert pending.check(pending.EDGE, "не согласен: нет", rec) is None
    assert pending.check(pending.EDGE, "пропустить", rec) is not None


def test_record_checks_against_recorded_question(tmp_path):
    q = pending.Question(pending.EDGE, "a/b/R1", "замечание", options=("А",))
    pending.asker(tmp_path)(q)
    with pytest.raises(pending.PendingError):
        pending.record(tmp_path, q.id, "2")
    pending.record(tmp_path, q.id, "1")
