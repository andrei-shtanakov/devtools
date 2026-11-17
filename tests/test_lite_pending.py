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
    q = pending.Question(pending.REVIEW, "a", "x")
    pending.asker(tmp_path)(q)
    with pytest.raises(pending.PendingError, match="в терминале"):
        pending.record(tmp_path, q.id, "поправь требования")
    assert not (tmp_path / pending.REPLIES_FILE).exists()


def test_check_review_accepts_only_done_words_in_slice1():
    assert pending.check(pending.REVIEW, "дальше") is None
    assert "в терминале" in pending.check(pending.REVIEW, "поправь требования")


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
