"""Тесты видов вопросов интервью в `lite.pending` (#766 срез 3)."""

from __future__ import annotations

from lite import pending


def _q(kind: str = pending.INTERVIEW_QUESTION, key: str = "q1", text: str = "t"):
    return pending.Question(kind, key, text)


def test_post_writes_data_and_keeps_asker_behaviour(tmp_path):
    q = _q(text="первый")
    pending.post(tmp_path, q, {"coverage": ["FR-01"]})
    rec = pending.load(tmp_path)
    assert rec["id"] == q.id
    assert rec["kind"] == pending.INTERVIEW_QUESTION
    assert rec["data"] == {"coverage": ["FR-01"]}
    pending.post(tmp_path, _q(text="второй"))
    assert "data" not in pending.load(tmp_path)

    ask = pending.asker(tmp_path)
    same = _q(text="второй")
    assert ask(same) is None
    pending.record(tmp_path, same.id, "пропустить")
    assert ask(same) == "пропустить"
    # тот же вопрос не стирает replies.json
    assert ask(same) is None
    pending.record(tmp_path, same.id, "skip")
    assert ask(same) == "skip"
    ask(same)
    pending.record(tmp_path, same.id, "skip")
    ask(_q(text="другой"))  # смена вопроса стирает ответы
    assert not (tmp_path / pending.REPLIES_FILE).exists()


def test_interview_kinds_set():
    assert pending.INTERVIEW_KINDS == {
        pending.INTERVIEW_QUESTION,
        pending.INTERVIEW_TOPUP,
        pending.INTERVIEW_REPAIR,
    }


def test_interview_reply_accepts_skip_only():
    rec = {"id": "interview-question:q1:abc", "key": "q1"}
    for kind in pending.INTERVIEW_KINDS:
        assert pending.check(kind, "пропустить", rec) is None
        assert pending.check(kind, " Skip ", rec) is None
        reason = pending.check(kind, "FR-01 должен кэшировать", rec)
        assert reason == pending.INTERVIEW_REFUSAL.format(qid=rec["id"])
    assert "interview-question:q1:abc" in pending.check(
        pending.INTERVIEW_QUESTION, "что-то", rec
    )


def test_interview_refusal_without_rec_uses_empty_id():
    reason = pending.check(pending.INTERVIEW_TOPUP, "текст", None)
    assert reason == pending.INTERVIEW_REFUSAL.format(qid="")


def test_verdict_reply_accepts_text():
    rec = {"id": "interview-repair:verdict:FR-03:x", "key": "verdict:FR-03"}
    kind = pending.INTERVIEW_REPAIR
    assert pending.check(kind, "да", rec) is None
    assert pending.check(kind, "нужен кэш", rec) is None
    assert pending.check(kind, "  ", rec) is not None


def test_owner_and_assumptions_accept_text():
    kind = pending.INTERVIEW_REPAIR
    for key in ("owner:process", "assumptions", "category:NFR-01"):
        rec = {"id": f"interview-repair:{key}:x", "key": key}
        assert pending.check(kind, "Иван, владелец продукта", rec) is None
        assert pending.check(kind, "пропустить", rec) is None
        assert pending.check(kind, "  ", rec) is not None


def test_nfr_key_refuses_text_accepts_unknown():
    kind = pending.INTERVIEW_REPAIR
    for key in ("nfr:perf", "nfr:cost"):
        rec = {"id": f"interview-repair:{key}:x", "key": key}
        assert pending.check(kind, "неизвестно", rec) is None
        assert pending.check(kind, "Пока не решили.", rec) is None
        assert pending.check(kind, "пропустить", rec) is None
        reason = pending.check(kind, "ответ за 2 секунды", rec)
        assert reason is not None and "dfl answer --items" in reason
    table = {"id": "interview-repair:table:стоить:x", "key": "table:стоить"}
    assert pending.check(kind, "неизвестно", table) is not None
