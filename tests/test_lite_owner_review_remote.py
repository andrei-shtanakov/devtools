"""Кандидат правки и вопрос «Удалить …?» без терминала (#766 срез 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lite import owner_review as orv
from lite import pending
from lite import review_pause as rp

BEFORE = (
    "#### FR-01: вход\n**Priority**: Must\n\n#### FR-02: выход\n**Priority**: Must\n"
)
WITHOUT_BOTH = "# пусто\n"
WITHOUT_FR01 = "#### FR-02: выход\n**Priority**: Must\n"
STAGE = "requirements"
REMARK = "- замечание владельца: убери вход и выход"


class FakeRevise:
    """Автор: пишет заранее заданные тексты по очереди, считает вызовы."""

    def __init__(self, path: Path, *outputs: str | None) -> None:
        self.path = path
        self.outputs = list(outputs)
        self.calls: list[str] = []

    def __call__(self, current: str, text: str) -> str | None:
        self.calls.append(text)
        output = self.outputs[len(self.calls) - 1]
        if output is None:
            return "сбой автора"
        self.path.write_text(output, encoding="utf-8")
        return None


def no_read(_prompt: str) -> str | None:
    raise AssertionError("ввод не должен читаться")


def make_stage(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "10-requirements.md"
    path.write_text(BEFORE, encoding="utf-8")
    store = tmp_path / "run"
    store.mkdir()
    return path, store


def remote(path: Path, store: Path, revise: FakeRevise) -> tuple[str, str]:
    return orv.revise_by_owner(
        path,
        REMARK,
        revise=revise,
        read=no_read,
        ask=pending.asker(store),
        store=store,
        stage=STAGE,
    )


def test_confirm_question_shape() -> None:
    q = rp.confirm_question(STAGE, "FR-01", rp.REMOVE, "вход")
    assert q.kind == pending.REMARKS_CONFIRM
    assert q.key == "requirements:FR-01"
    assert q.text == "Удалить FR-01 — «вход»? 1. удалить · 2. оставить"
    assert q.options == ("удалить", "оставить")


def test_remote_confirm_waits_and_keeps_candidate(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_BOTH)
    outcome, detail = remote(path, store, revise)
    assert outcome == orv.WAITING
    assert len(revise.calls) == 1
    assert path.read_text(encoding="utf-8") == BEFORE
    rec = pending.load(store)
    assert rec is not None
    assert rec["kind"] == pending.REMARKS_CONFIRM
    assert rec["key"] == "requirements:FR-01"
    q = rp.confirm_question(STAGE, "FR-01", rp.REMOVE, "вход")
    assert detail == pending.wait_line(q)
    assert (store / orv.CANDIDATE_FILE).exists()


def test_resume_takes_candidate_without_second_revise(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_BOTH, WITHOUT_FR01)
    assert remote(path, store, revise)[0] == orv.WAITING
    pending.record(store, pending.load(store)["id"], "1")

    outcome, _ = remote(path, store, revise)
    assert outcome == orv.WAITING
    assert len(revise.calls) == 1
    assert pending.load(store)["key"] == "requirements:FR-02"
    assert path.read_text(encoding="utf-8") == BEFORE
    pending.record(store, pending.load(store)["id"], "2")

    outcome, _ = remote(path, store, revise)
    assert outcome == orv.ACCEPTED
    assert len(revise.calls) == 2
    assert "- FR-02 не удалять" in revise.calls[1]
    assert path.read_text(encoding="utf-8") == WITHOUT_FR01
    assert not (store / orv.CANDIDATE_FILE).exists()
    assert pending.load(store) is None


def test_candidate_dropped_when_file_edited(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_BOTH, WITHOUT_BOTH)
    assert remote(path, store, revise)[0] == orv.WAITING
    pending.record(store, pending.load(store)["id"], "1")
    path.write_text(BEFORE + "\nправка руками\n", encoding="utf-8")

    outcome, _ = remote(path, store, revise)
    assert outcome == orv.WAITING
    assert len(revise.calls) == 2
    # ответ на вопрос о старой правке не взят: снова спрашивается FR-01
    assert pending.load(store)["key"] == "requirements:FR-01"
    assert not pending.answered(store)


def test_terminal_takes_recorded_confirm(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01)
    assert remote(path, store, revise)[0] == orv.WAITING
    pending.record(store, pending.load(store)["id"], "1")

    outcome, _ = orv.revise_by_owner(
        path, REMARK, revise=revise, read=no_read, store=store, stage=STAGE
    )
    assert outcome == orv.ACCEPTED
    assert len(revise.calls) == 1
    assert path.read_text(encoding="utf-8") == WITHOUT_FR01
    assert not (store / orv.CANDIDATE_FILE).exists()


def test_terminal_live_answer_discards_pending(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01)
    assert remote(path, store, revise)[0] == orv.WAITING
    answers = iter(["удалить"])

    outcome, _ = orv.revise_by_owner(
        path,
        REMARK,
        revise=revise,
        read=lambda _p: next(answers, None),
        store=store,
        stage=STAGE,
    )
    assert outcome == orv.ACCEPTED
    assert len(revise.calls) == 1
    assert pending.load(store) is None
    assert not (store / orv.CANDIDATE_FILE).exists()


def test_terminal_without_candidate_never_writes_one(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01)
    answers = iter([None])
    outcome, _ = orv.revise_by_owner(
        path,
        REMARK,
        revise=revise,
        read=lambda _p: next(answers, None),
        store=store,
        stage=STAGE,
    )
    assert outcome == orv.PAUSED
    assert not (store / orv.CANDIDATE_FILE).exists()


def test_without_store_behaviour_unchanged(tmp_path: Path) -> None:
    path, _store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01)
    answers = iter(["1"])
    outcome, _ = orv.revise_by_owner(
        path, REMARK, revise=revise, read=lambda _p: next(answers, None)
    )
    assert outcome == orv.ACCEPTED
    assert not list(tmp_path.rglob(orv.CANDIDATE_FILE))


def test_ask_without_store_is_error(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01)
    with pytest.raises(ValueError, match="ask без store"):
        orv.revise_by_owner(
            path, REMARK, revise=revise, read=no_read, ask=pending.asker(store)
        )
    assert revise.calls == []


def keep_first(path: Path, store: Path, revise: FakeRevise) -> tuple[str, str]:
    """Первый вызов ждёт ответа по FR-01; ответ «оставить»; второй вызов."""
    assert remote(path, store, revise)[0] == orv.WAITING
    pending.record(store, pending.load(store)["id"], "2")
    return remote(path, store, revise)


def test_candidate_deleted_on_failed(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01, None)
    outcome, detail = keep_first(path, store, revise)
    assert (outcome, detail) == (orv.FAILED, "сбой автора")
    assert len(revise.calls) == 2
    assert path.read_text(encoding="utf-8") == BEFORE
    assert not (store / orv.CANDIDATE_FILE).exists()


def test_candidate_deleted_on_unclear(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_FR01, WITHOUT_FR01)
    outcome, detail = keep_first(path, store, revise)
    assert (outcome, detail) == (orv.UNCLEAR, "FR-01")
    assert path.read_text(encoding="utf-8") == BEFORE
    assert not (store / orv.CANDIDATE_FILE).exists()


def test_corrupt_candidate_means_fresh_revise(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    (store / orv.CANDIDATE_FILE).write_text("{не json", encoding="utf-8")
    revise = FakeRevise(path, WITHOUT_FR01)
    outcome, _ = remote(path, store, revise)
    assert outcome == orv.WAITING
    assert len(revise.calls) == 1
    data = json.loads((store / orv.CANDIDATE_FILE).read_text(encoding="utf-8"))
    assert data["after"] == WITHOUT_FR01


def test_terminal_stop_during_resume_keeps_answers(tmp_path: Path) -> None:
    path, store = make_stage(tmp_path)
    revise = FakeRevise(path, WITHOUT_BOTH)
    assert remote(path, store, revise)[0] == orv.WAITING
    pending.record(store, pending.load(store)["id"], "1")

    outcome, _ = orv.revise_by_owner(
        path, REMARK, revise=revise, read=lambda _p: None, store=store, stage=STAGE
    )
    assert outcome == orv.PAUSED
    assert len(revise.calls) == 1
    assert path.read_text(encoding="utf-8") == BEFORE
    data = json.loads((store / orv.CANDIDATE_FILE).read_text(encoding="utf-8"))
    assert data["answers"] == {"FR-01": True}
