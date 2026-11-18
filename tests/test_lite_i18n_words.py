"""Слова ответа на двух языках (#769 срез 1, Task 3).

Английские слова принимаются в обоих режимах: каждая точка разбора ответа
человека принимает и русские, и английские слова.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance import manual_checks as mc
from lite import edge_journal as ej
from lite import edge_resolve as er
from lite import i18n, interview_chat, manual_ask, pending
from lite import review_pause as rp
from lite.edge_triage import Item, Option
from lite.personal import interviewer
from lite.personal import state as ps
from tests.personal_en import cyrillic
from tests.test_lite_dfl import REPO, SLUG, URL, Env, env  # noqa: F401
from tests.test_lite_dfl_pending import _known_target  # noqa: F401


@pytest.fixture(autouse=True)
def _ru_after() -> object:
    yield
    i18n.set_language("ru")


DONE_WORDS = ["дальше", "да", "ок", "1", "next", "yes", "ok", "done", "Next", " DONE "]


@pytest.mark.parametrize("word", DONE_WORDS)
def test_review_done_words_both(word: str) -> None:
    assert pending.is_done(word)
    assert pending.check(pending.REVIEW, word, {"key": "brief"}) is None
    assert rp.reply_text(word) == ""


def test_review_not_done_word() -> None:
    assert not pending.is_done("nextish")


def _confirm_rec(verb: str) -> dict:
    return {"options": [verb, "оставить"]}


@pytest.mark.parametrize(
    ("verb", "agree", "keep"),
    [
        ("удалить", ["1", "да", "удалить", "yes", "remove", "Remove"], ["2", "keep"]),
        ("снять", ["1", "да", "снять", "yes", "drop"], ["2", "нет", "keep"]),
        ("принять", ["1", "да", "принять", "yes", "accept"], ["оставить", "keep"]),
    ],
)
def test_confirm_words_both(verb: str, agree: list[str], keep: list[str]) -> None:
    rec = _confirm_rec(verb)
    for word in [*agree, *keep]:
        assert pending.check(pending.REMARKS_CONFIRM, word, rec) is None, word
    assert pending.check(pending.REMARKS_CONFIRM, "maybe", rec) is not None


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_change_confirm_option_two_text_accepted(lang: str) -> None:
    """Вариант 2 вопроса вида `change` — ровно напечатанная фраза — принимается
    и `dfl reply`, и терминалом (ревью #809)."""
    with i18n.language(lang):
        text = rp.confirm_question("design", "D-01", rp.CHANGE, "x").text
    phrase = text.rsplit("2. ", 1)[1].strip()
    assert phrase in {"оставить как было", "keep as it was"}
    assert (
        pending.check(pending.REMARKS_CONFIRM, phrase, _confirm_rec("принять")) is None
    )
    assert rp.confirm_answer(phrase, rp.CHANGE) is False


@pytest.mark.parametrize(
    ("kind", "agree"),
    [(rp.REMOVE, "remove"), (rp.DOWNGRADE, "drop"), (rp.CHANGE, "accept")],
)
def test_confirm_answer_both(kind: str, agree: str) -> None:
    assert rp.confirm_answer(agree, kind) is True
    assert rp.confirm_answer("yes", kind) is True
    assert rp.confirm_answer("keep", kind) is False
    others = {"remove", "drop", "accept"} - {agree}
    assert all(rp.confirm_answer(w, kind) is None for w in others)


@pytest.mark.parametrize(
    "text",
    [
        "своё: x",
        "own: x",
        "OWN: x",
        "Своё: x",
        "свое: x",
        "не согласен: x",
        "disagree: x",
        "Disagree: x",
    ],
)
def test_edge_prefixes_both(text: str) -> None:
    rec = {"options": ["a", "b"]}
    assert pending.check(pending.EDGE, text, rec) is None
    assert pending.check(pending.EDGE, text.split(":")[0] + ":  ", rec) is not None


def _edge_item() -> Item:
    finding = ej.Finding("n", "e", "R1", "major", "s", "f.md", "1", "d", ("h",))
    return Item(finding, "decision", "q", [Option("a", True, [], [])])


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("own: x", "own"),
        ("своё: x", "own"),
        ("свое: x", "own"),
        ("Disagree: why", "disagree"),
        ("не согласен: why", "disagree"),
    ],
)
def test_edge_parse_both(text: str, kind: str) -> None:
    decision = er._parse(_edge_item(), "q", text, None, 1)
    assert decision is not None and decision.kind == kind
    assert er._parse(_edge_item(), "q", "own:", None, 1) is None


def test_edge_skip_and_noreason_both() -> None:
    assert er._starts_any("Disagree: x", er.DISAGREE_PREFIXES)
    assert er._starts_any("не согласен:", er.DISAGREE_PREFIXES)
    assert not er._starts_any("own: x", er.DISAGREE_PREFIXES)
    assert interview_chat.is_skip("skip") and interview_chat.is_skip("Skip ")


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("1", mc.DONE),
        ("passed", mc.DONE),
        ("Passed", mc.DONE),
        ("выполнено", mc.DONE),
        ("failed: broken", mc.NOT_DONE),
        ("не выполнено: сломано", mc.NOT_DONE),
        ("passed with caveat: slow", mc.CAVEAT),
        ("3: slow", mc.CAVEAT),
        ("с оговоркой: slow", mc.CAVEAT),
        ("принять с оговоркой: slow", mc.CAVEAT),
    ],
)
def test_manual_verdicts_both(text: str, verdict: str) -> None:
    assert pending.parse_manual(text)["verdict"] == verdict
    assert pending.check(pending.MANUAL, text) is None


def test_manual_caveat_needs_note_both() -> None:
    assert pending.check(pending.MANUAL, "passed with caveat") is not None
    assert pending.check(pending.MANUAL, "с оговоркой") is not None


def test_manual_ask_one_accepts_english() -> None:
    answers = iter(["passed"])
    result = manual_ask.ask_one(
        mc.ManualCheck("BEH-01", "check it", "T-1", None),
        lambda _prompt: next(answers),
    )
    assert result == {"verdict": mc.DONE, "note": ""}


def test_interview_yes_skip_fix_both(tmp_path: Path) -> None:
    assert {"да", "верно", "ок", "yes"} <= interviewer.YES_WORDS
    assert "ok" in interviewer.YES_WORDS
    assert {"пропустить", "skip"} == interviewer.SKIP_WORDS
    for text in (
        "fix: shorter",
        "Fix: shorter",
        "поправить: shorter",
        "2. fix: shorter",
    ):
        assert interviewer._correction(text, lambda _p: None) == "shorter"


def test_append_correction_writes_run_language(tmp_path: Path) -> None:
    interviewer.append_correction(tmp_path, "x", lang="en")
    interviewer.append_correction(tmp_path, "y")
    lines = [
        json.loads(line)["text"]
        for line in (tmp_path / interviewer.TRANSCRIPT).read_text("utf-8").splitlines()
    ]
    assert lines == ["fix: x", "поправить: y"]


def test_stop_word_both() -> None:
    for word in ("/стоп", "/stop"):
        assert interview_chat.read_answer(lambda _p, w=word: w) is None


def test_is_wait_both_languages() -> None:
    for lang in ("ru", "en"):
        with i18n.language(lang):
            assert manual_ask.is_wait(i18n.t(manual_ask.PAUSED))
            assert manual_ask.is_wait(i18n.t(manual_ask.WAIT_PREFIX) + "x")
            assert manual_ask.is_wait(i18n.t(manual_ask.NO_TTY).format(n=2))
    assert not manual_ask.is_wait("boom")


def test_hints_name_english_words_in_en() -> None:
    i18n.reset_misses()
    with i18n.language("en"):
        rec = {"options": ["a", "b"], "key": "brief"}
        hints = [
            pending.check(pending.MANUAL, "?"),
            pending.check(pending.EDGE, "?", rec),
            pending.check(pending.REVIEW, "x", rec),
            pending.check(pending.REVIEW, " ", {"key": "design"}),
            pending.check(pending.REMARKS_CONFIRM, "?", _confirm_rec("удалить")),
            i18n.t(er.ANSWER_HELP),
            i18n.t(er.REMOTE_HELP),
            i18n.t(er.REASK),
            i18n.t(er.NO_REASON),
            i18n.t(interviewer.CONFIRM),
            i18n.t(rp.REMOTE_ACCEPTS),
        ]
    assert all(h and not cyrillic(h) for h in hints), hints
    assert not i18n.misses()
    assert "own:" in hints[1] and "passed" in hints[0] and "next" in hints[2]


def test_ru_hints_unchanged() -> None:
    assert pending.check(pending.MANUAL, "?") == pending.MANUAL_REFUSAL
    assert i18n.t(er.ANSWER_HELP) == er.ANSWER_HELP


def test_ru_run_accepts_next(env: Env) -> None:  # noqa: F811
    run = ps.new_personal_run("Учёт", REPO, SLUG, "/t", date.today())
    ps.save(run)
    run_dir = ps.run_dir(run.run_id)
    q = rp.question("design", "Дизайн", [Path("/tmp/d.md")])
    pending.asker(run_dir)(q)
    assert env.run("reply", "--id", q.id, "--text", "next") == 0
    replies = json.loads((run_dir / pending.REPLIES_FILE).read_text("utf-8"))
    assert replies == {q.id: "next"}
