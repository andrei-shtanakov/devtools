"""Интервью личного пути без терминала (#766 срез 4): протокол и ядро."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lite import i18n, pending
from lite.personal import interviewer
from lite.personal import remote_interview as ri
from lite.personal.brief import Brief, brief_from_json, render_brief
from lite.structure import FormError

BRIEF: dict[str, Any] = {
    "goals": [{"id": "G-01", "title": "Быстро", "text": "Стало быстро."}],
    "functions": [
        {
            "id": "FR-01",
            "title": "Умеет",
            "goals": ["G-01"],
            "criteria": ["команда печатает итог"],
            "words": "чтоб печатал",
        }
    ],
    "context": {
        "users": "только я",
        "auth": "нет",
        "network": "нет",
        "local": "читает ~/notes",
        "platform": "macOS",
    },
}


def _bare() -> dict[str, Any]:
    """BRIEF плюс FR-02 без критерия."""
    data = copy.deepcopy(BRIEF)
    data["functions"].append(
        {
            "id": "FR-02",
            "title": "Экспорт",
            "goals": ["G-01"],
            "criteria": [],
            "words": "и экспорт",
        }
    )
    return data


def _entries(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / interviewer.TRANSCRIPT
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def _qid(run_dir: Path) -> str:
    rec = pending.load(run_dir)
    assert rec is not None
    return str(rec["id"])


def _posted(run_dir: Path) -> str:
    assert ri.step(run_dir, "заметки") is None
    return _qid(run_dir)


def _confirm_posted(run_dir: Path) -> str:
    qid = _posted(run_dir)
    assert ri.submit(run_dir, qid, BRIEF, subject="заметки").ok
    return _qid(run_dir)


def test_step_posts_need_brief(tmp_path: Path) -> None:
    assert ri.step(tmp_path, "заметки") is None
    rec = pending.load(tmp_path)
    assert rec is not None
    assert rec["kind"] == pending.INTERVIEW_PERSONAL and "заметки" in rec["text"]
    assert rec["text"] == ri.NEED_BRIEF.format(subject="заметки")
    data = rec["data"]
    assert data["transcript"] == str(tmp_path / "interview.jsonl")
    assert data["max_questions"] == interviewer.MAX_QUESTIONS
    assert data["max_checks_in_row"] == interviewer.MAX_CHECKS_IN_ROW
    assert data["questions"] == 0 and data["checks_in_row"] == 0
    assert data["subject"] == "заметки" and data["lang"] == "ru"
    assert data["prompt"] == str(interviewer.PROMPT_PATHS["ru"])
    assert Path(data["prompt"]).is_absolute()


def test_step_posts_english_prompt(tmp_path: Path) -> None:
    ri.step(tmp_path, "notes", lang="en")
    rec = pending.load(tmp_path)
    assert rec is not None
    assert rec["data"]["prompt"] == str(interviewer.PROMPT_PATHS["en"])


def test_need_brief_english(tmp_path: Path) -> None:
    with i18n.language("en"):
        ri.step(tmp_path, "notes", lang="en")
    rec = pending.load(tmp_path)
    assert rec is not None
    assert rec["text"] == i18n.EN[ri.NEED_BRIEF].format(subject="notes")


def test_note_appends_and_caps(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    ri.note(tmp_path, qid, "agent", "Как часто?", ("раз в день", " ", "раз в неделю"))
    ri.note(tmp_path, qid, "human", "раз в день")
    assert _entries(tmp_path) == [
        {
            "role": "agent",
            "kind": "ask",
            "text": "Как часто?",
            "options": ["раз в день", "раз в неделю"],
        },
        {"role": "human", "text": "раз в день"},
    ]
    for n in range(interviewer.MAX_QUESTIONS - 1):
        ri.note(tmp_path, qid, "agent", f"вопрос {n}")
    before = (tmp_path / interviewer.TRANSCRIPT).read_text(encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ri.note(tmp_path, qid, "agent", "ещё вопрос")
    assert str(exc.value) == ri.CAP_REFUSAL
    after = (tmp_path / interviewer.TRANSCRIPT).read_text(encoding="utf-8")
    assert after == before
    ri.note(tmp_path, qid, "human", "человеку можно и после лимита")


def test_note_refuses_bad_input(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    for role, text, options in (
        ("human", "да", ("вариант",)),
        ("agent", "   ", ()),
        ("check", "x", ()),
    ):
        with pytest.raises(ValueError):
            ri.note(tmp_path, qid, role, text, options)
    assert not (tmp_path / interviewer.TRANSCRIPT).exists()


def test_note_stale_refused(tmp_path: Path) -> None:
    with pytest.raises(pending.PendingError) as exc:
        ri.note(tmp_path, "interview-personal:brief:00000000", "human", "x")
    assert str(exc.value) == pending.NO_PENDING
    _posted(tmp_path)
    with pytest.raises(pending.PendingError) as exc:
        ri.note(tmp_path, "interview-personal:brief:00000000", "human", "x")
    assert str(exc.value) == pending.STALE
    assert not (tmp_path / interviewer.TRANSCRIPT).exists()


def test_note_refused_on_brief_confirm(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    before = (tmp_path / interviewer.TRANSCRIPT).read_text(encoding="utf-8")
    with pytest.raises(pending.PendingError) as exc:
        ri.note(tmp_path, qid, "human", "x")
    assert str(exc.value) == pending.STALE
    assert (tmp_path / interviewer.TRANSCRIPT).read_text(encoding="utf-8") == before


def test_submit_form_error(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    with pytest.raises(FormError):
        ri.submit(tmp_path, qid, {"brief": BRIEF}, subject="заметки")
    with pytest.raises(pending.PendingError):
        ri.submit(tmp_path, "x", BRIEF, subject="заметки")


def test_submit_problems_logged_code(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    got = ri.submit(tmp_path, qid, _bare(), subject="заметки")
    assert not got.ok and got.problems and got.summary == ""
    assert any("FR-02" in p for p in got.problems)
    brief_entry, check_entry = _entries(tmp_path)
    assert brief_entry["role"] == "agent" and brief_entry["kind"] == "brief"
    assert json.loads(brief_entry["text"]) == _bare()
    assert check_entry == {"role": "check", "text": "\n".join(got.problems)}
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.INTERVIEW_PERSONAL
    assert not (tmp_path / ri.DRAFT).exists()


def test_submit_forced_after_three_checks(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    for _ in range(interviewer.MAX_CHECKS_IN_ROW):
        assert not ri.submit(tmp_path, qid, _bare(), subject="заметки").ok
    got = ri.submit(tmp_path, qid, _bare(), subject="заметки")
    assert got.ok and got.problems == []
    assert "Уточнить" in got.summary and "Экспорт" in got.summary


def test_submit_forced_at_question_cap(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    for n in range(interviewer.MAX_QUESTIONS):
        ri.note(tmp_path, qid, "agent", f"вопрос {n}")
    assert ri.submit(tmp_path, qid, _bare(), subject="заметки").ok


def test_submit_accept_uncovered(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    got = ri.submit(tmp_path, qid, _bare(), subject="заметки", accept_uncovered=True)
    assert got.ok and "Экспорт" in got.summary
    draft = json.loads((tmp_path / ri.DRAFT).read_text(encoding="utf-8"))
    assert [f["id"] for f in draft["functions"]] == ["FR-01"]


def test_submit_nothing_to_build(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    data = copy.deepcopy(BRIEF)
    data["functions"][0]["criteria"] = []
    got = ri.submit(tmp_path, qid, data, subject="заметки", accept_uncovered=True)
    assert not got.ok and got.problems == [ri.NOTHING_TO_BUILD_REMOTE]
    assert [e.get("kind") for e in _entries(tmp_path)] == ["brief"]
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.INTERVIEW_PERSONAL


def test_submit_ok_posts_confirm_with_summary(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    got = ri.submit(tmp_path, qid, BRIEF, subject="заметки")
    summary = render_brief(brief_from_json(BRIEF))
    assert got.ok and got.problems == [] and got.summary == summary
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.BRIEF_CONFIRM
    assert rec["text"].startswith(summary)
    assert rec["text"] == summary + "\n\n" + interviewer.CONFIRM
    draft = json.loads((tmp_path / ri.DRAFT).read_text(encoding="utf-8"))
    assert brief_from_json(draft) == brief_from_json(BRIEF)
    assert _entries(tmp_path)[-1] == {
        "role": "agent",
        "kind": "summary",
        "text": summary,
    }


@pytest.mark.parametrize("word", ["1", "да", "Верно", "ok"])
def test_confirm_yes_returns_brief(tmp_path: Path, word: str) -> None:
    qid = _confirm_posted(tmp_path)
    assert ri.step(tmp_path, "заметки") is None  # ответа ещё нет — ждём
    assert _qid(tmp_path) == qid
    pending.record(tmp_path, qid, word)
    got = ri.step(tmp_path, "заметки")
    assert isinstance(got, Brief) and got == brief_from_json(BRIEF)
    assert not (tmp_path / ri.DRAFT).exists()
    assert pending.load(tmp_path) is None


@pytest.mark.parametrize("word", ["1.", "да.", "Да!", "ок.", " ДА ", "верно…"])
def test_confirm_yes_with_punctuation(tmp_path: Path, word: str) -> None:
    """Minor 1: «да.»/«Да!»/«1.» — согласие, не поправка."""
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, word)
    assert ri.step(tmp_path, "заметки") == brief_from_json(BRIEF)


def test_confirm_yes_phrase_stays_correction(tmp_path: Path) -> None:
    """Нечёткого сравнения нет: «Да, всё верно» — поправка словами."""
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "Да, всё верно")
    assert ri.step(tmp_path, "заметки") is None
    assert _entries(tmp_path)[-1]["text"] == "поправить: Да, всё верно"


def test_confirm_yes_on_changed_draft_asks_again(tmp_path: Path) -> None:
    """Important 1: черновик не тот, что видел человек, — брифа нет,
    снова «нужен бриф»."""
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "да")
    draft = json.loads((tmp_path / ri.DRAFT).read_text(encoding="utf-8"))
    draft["goals"][0]["text"] = "ТЕКСТ, КОТОРОГО ЧЕЛОВЕК НЕ ВИДЕЛ"
    (tmp_path / ri.DRAFT).write_text(json.dumps(draft, ensure_ascii=False))
    assert ri.step(tmp_path, "заметки") is None
    assert not (tmp_path / ri.DRAFT).exists()
    assert not (tmp_path / "brief.json").exists()
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.INTERVIEW_PERSONAL


def test_confirm_yes_on_broken_draft_asks_again(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "да")
    (tmp_path / ri.DRAFT).write_text(json.dumps({"goals": "x"}))
    assert ri.step(tmp_path, "заметки") is None
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.INTERVIEW_PERSONAL


def test_confirm_text_independent_of_ambient_language(tmp_path: Path) -> None:
    """Сводку английского прогона подтверждают и при русском окружении."""
    ri.step(tmp_path, "notes", lang="en")
    with i18n.language("ru"):
        assert ri.submit(tmp_path, _qid(tmp_path), BRIEF, subject="notes", lang="en").ok
    pending.record(tmp_path, _qid(tmp_path), "yes")
    with i18n.language("en"):
        assert ri.step(tmp_path, "notes", lang="en") == brief_from_json(
            BRIEF, lang="en"
        )


def test_confirm_fix_appends_correction(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "поправить: раз в месяц")
    assert ri.step(tmp_path, "заметки") is None
    assert _entries(tmp_path)[-1] == {"role": "human", "text": "поправить: раз в месяц"}
    rec = pending.load(tmp_path)
    assert rec is not None and rec["kind"] == pending.INTERVIEW_PERSONAL
    assert not (tmp_path / ri.DRAFT).exists()


def test_confirm_fix_free_text_and_numbered(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "2. не раз в неделю")
    ri.step(tmp_path, "заметки")
    assert _entries(tmp_path)[-1]["text"] == "поправить: не раз в неделю"


def test_confirm_fix_english_prefix(tmp_path: Path) -> None:
    ri.step(tmp_path, "notes", lang="en")
    qid = _qid(tmp_path)
    assert ri.submit(tmp_path, qid, BRIEF, subject="notes", lang="en").ok
    pending.record(tmp_path, _qid(tmp_path), "fix: monthly")
    ri.step(tmp_path, "notes", lang="en")
    assert _entries(tmp_path)[-1] == {"role": "human", "text": "fix: monthly"}


def test_resubmit_drops_old_yes(tmp_path: Path) -> None:
    first = _confirm_posted(tmp_path)
    pending.record(tmp_path, first, "да")
    other = copy.deepcopy(BRIEF)
    other["functions"][0]["criteria"] = ["команда печатает другой итог"]
    assert ri.submit(tmp_path, first, other, subject="заметки").ok
    second = _qid(tmp_path)
    assert second != first and not pending.answered(tmp_path)
    assert ri.step(tmp_path, "заметки") is None  # «да» на прежнюю сводку пропал
    pending.record(tmp_path, second, "да")
    assert ri.step(tmp_path, "заметки") == brief_from_json(other)


def test_reply_bare_two_refused(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    wordless = ("2", " 2 ", "", "  ", "2.", "2)", "2:", "поправить", "Поправить:")
    for text in (*wordless, "fix", "fix:", "FIX: ", "2. поправить:"):
        with pytest.raises(pending.PendingError) as exc:
            pending.record(tmp_path, qid, text)
        assert str(exc.value) == pending.CONFIRM_FIX_REFUSAL
    pending.record(tmp_path, qid, "не знаю")


def test_reply_refused_on_need_brief(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    with pytest.raises(pending.PendingError) as exc:
        pending.record(tmp_path, qid, "да")
    assert str(exc.value) == pending.PERSONAL_REPLY_REFUSAL


def test_reset_clears_question_reply_and_draft(tmp_path: Path) -> None:
    qid = _confirm_posted(tmp_path)
    pending.record(tmp_path, qid, "да")
    ri.reset(tmp_path)
    assert pending.load(tmp_path) is None
    assert not (tmp_path / pending.REPLIES_FILE).exists()
    assert not (tmp_path / ri.DRAFT).exists()
    assert (tmp_path / interviewer.TRANSCRIPT).exists()  # журнал остаётся
    ri.reset(tmp_path)  # повторно — без ошибки


def test_status(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    ri.note(tmp_path, qid, "agent", "Как часто?")
    ri.submit(tmp_path, qid, _bare(), subject="заметки")
    got = ri.status(tmp_path, "заметки")
    assert got == {
        "track": "personal",
        "subject": "заметки",
        "questions": 1,
        "max_questions": interviewer.MAX_QUESTIONS,
        "checks_in_row": 1,
        "draft": None,
    }
    ri.submit(tmp_path, qid, BRIEF, subject="заметки")
    got = ri.status(tmp_path, "заметки")
    assert got["draft"] == json.loads((tmp_path / ri.DRAFT).read_text())
    assert got["checks_in_row"] == 1


def test_status_has_no_uncovered(tmp_path: Path) -> None:
    (tmp_path / ri.DRAFT).write_text(json.dumps(_bare()), encoding="utf-8")
    assert "uncovered" not in ri.status(tmp_path, "заметки")


def test_need_brief_text_uses_run_language(tmp_path: Path) -> None:
    qid = _posted(tmp_path)
    with i18n.language("ru"):
        ri.submit(tmp_path, qid, _bare(), subject="notes", lang="en")
    rec = pending.load(tmp_path)
    assert rec is not None
    other = tmp_path / "other"
    other.mkdir()
    qid2 = _posted(other)
    with i18n.language("en"):
        ri.submit(other, qid2, _bare(), subject="notes", lang="en")
    again = pending.load(other)
    assert again is not None
    assert rec["text"] == again["text"]
    assert rec["text"] == i18n.EN[ri.NEED_BRIEF].format(subject="notes")


def test_no_agent_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("агент интервьюера зван без терминала")

    monkeypatch.setattr(interviewer, "_next_turn", boom)
    monkeypatch.setattr(interviewer, "interview", boom)
    qid = _posted(tmp_path)
    ri.note(tmp_path, qid, "agent", "Как часто?", ("раз в день",))
    ri.note(tmp_path, qid, "human", "1. раз в день")
    assert not ri.submit(tmp_path, qid, _bare(), subject="заметки").ok
    assert ri.submit(tmp_path, qid, BRIEF, subject="заметки").ok
    pending.record(tmp_path, _qid(tmp_path), "да")
    assert ri.step(tmp_path, "заметки") == brief_from_json(BRIEF)


def test_settle_shared_with_terminal() -> None:
    entries: list[dict[str, Any]] = []
    good = brief_from_json(BRIEF)
    bare = brief_from_json(_bare())
    assert interviewer.settle(good, entries) == (good, [])
    none, problems = interviewer.settle(bare, entries)
    assert none is None and problems
    forced, rest = interviewer.settle(bare, entries, force=True)
    assert forced is not None and rest == []
    assert [f.id for f in forced.functions] == ["FR-01"]
    checks = [{"role": "check", "text": "x"}] * interviewer.MAX_CHECKS_IN_ROW
    assert interviewer.settle(bare, checks)[0] is not None
