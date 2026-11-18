"""Разбор замечаний edge-check без терминала (#766 срез 2, Task 4, место 5).

Ядро (`lite.edge_resolve`): вопрос уходит в `pending.json` (`WAITING`),
продолжение после `dfl reply` берёт ответ, сортировка — из кэша, партия
ответов круга та же. Терминал: записанный ответ берётся до живого ввода.
Фикстура `world` — из `tests/test_lite_edge_resolve.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from lite import edge_journal as ej
from lite import edge_resolve as er
from lite import pending
from lite.personal import flow, state
from tests.test_lite_edge_resolve import (  # noqa: F401
    BOTH_APPLIED,
    FIXED,
    TWO_CHOICES,
    World,
    _decision_item,
    _finding,
    _goal,
    world,
)
from tests.test_lite_personal_flow import (  # noqa: F401
    NODE_TEXT,
    FakeOps,
    _bundle,
    _reviser,
    checker,
    personal_root,
    run,
)


def _remote(
    world: World,  # noqa: F811
    items: list[dict],
    revise: object,
    waiting: er.Waiting,
    round_no: int = 1,
    calls: er.PaidCalls | None = None,
) -> er.Outcome:
    """Круг без терминала: `read` не зовётся, ответы — через `dfl reply`."""
    return er.resolve(
        world.state,
        read=lambda prompt: pytest.fail("без терминала read не зовётся"),
        agent=world.agent(items),
        revise=revise,
        round_no=round_no,
        calls=calls,
        ask=pending.asker(world.run_dir),
        waiting=waiting,
    )


def _reply(run_dir: Path, text: str) -> dict:
    rec = pending.load(run_dir)
    assert rec is not None
    pending.record(run_dir, str(rec["id"]), text)
    return rec


def test_edge_question_written_without_tty(world: World) -> None:  # noqa: F811
    world.findings(_finding())
    calls = er.PaidCalls()
    waiting = er.Waiting()
    outcome = _remote(world, _decision_item(), world.revise(), waiting, calls=calls)
    assert outcome is er.Outcome.WAITING
    rec = pending.load(world.run_dir)
    assert rec is not None and rec["kind"] == pending.EDGE
    assert rec["key"] == "requirements/requirements-vs-charter/R1"
    assert rec["options"] == ["покрыть требованием (рекомендую)", "убрать FR-02"]
    assert "Цель G-03 — покрыть или отклонить?" in rec["text"]
    assert rec["accepts"] == er.ANSWER_HELP
    assert waiting.question is not None and waiting.question.id == rec["id"]
    assert waiting.batch
    assert (calls.triage, calls.revise) == (1, 0)
    assert world.revisions == [] and ej.load(world.run_dir, 2) == []
    assert "разбор остановлен" not in world.out()


def test_edge_resume_no_repaid_triage(world: World) -> None:  # noqa: F811
    world.findings(_finding())
    calls = er.PaidCalls()
    first = er.Waiting()
    _remote(world, _decision_item(), world.revise(), first, calls=calls)
    _reply(world.run_dir, "1")
    again = er.Waiting(batch=first.batch)
    outcome = _remote(world, _decision_item(), world.revise(), again, calls=calls)
    assert outcome is er.Outcome.REVISED
    assert (calls.triage, calls.revise) == (1, 1)  # сортировка из кэша
    assert len(world.prompts) == 1
    [d] = ej.load(world.run_dir, 2)
    assert d.answer == "покрыть требованием" and d.batch == first.batch
    assert pending.load(world.run_dir) is None


def test_group_answers_across_runs_one_batch(world: World) -> None:  # noqa: F811
    """Два вопроса одной группы, по одному на `dfl run`: партия та же —
    разрешения обоих ответов доходят до сверки, сортировка одна."""
    world.findings(_goal("G-03"), _goal("G-05"))
    calls = er.PaidCalls()
    waiting = er.Waiting()
    revise = world.revise(BOTH_APPLIED)
    for _ in range(2):
        assert (
            _remote(world, TWO_CHOICES, revise, waiting, er.MAX_ROUNDS, calls)
            is er.Outcome.WAITING
        )
        _reply(world.run_dir, "2")
        waiting = er.Waiting(batch=waiting.batch)
    outcome = _remote(world, TWO_CHOICES, revise, waiting, er.MAX_ROUNDS, calls)
    assert outcome is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == BOTH_APPLIED
    assert [d.answer for d in ej.load(world.run_dir, 2)] == [
        "снять FR-01",
        "снять FR-02",
    ]
    assert (calls.triage, calls.revise) == (1, 1)


def test_override_offer_is_last_option(world: World) -> None:  # noqa: F811
    """«Принять вопреки» — последний вариант записанного вопроса: `dfl reply`
    проверяет диапазон по `options`, разбор — тем же `_parse`."""
    world.findings(_finding())
    waiting = er.Waiting()
    revise = world.revise()
    _remote(world, _decision_item(), revise, waiting, er.MAX_ROUNDS)
    rec = pending.load(world.run_dir)
    assert rec is not None and rec["options"][-1] == er.OVERRIDE_OFFER
    assert pending.check(pending.EDGE, "3", rec) is None
    assert pending.check(pending.EDGE, "4", rec) is not None
    _reply(world.run_dir, "3")
    again = er.Waiting(batch=waiting.batch)
    outcome = _remote(world, _decision_item(), revise, again, er.MAX_ROUNDS)
    assert outcome is er.Outcome.OVERRIDDEN and world.revisions == []


def test_terminal_takes_recorded_reply(world: World) -> None:  # noqa: F811
    """Терминал: ответ `dfl reply` на записанный вопрос — ответ человека,
    живой ввод не нужен; вопрос снят."""
    world.findings(_finding())
    waiting = er.Waiting()
    _remote(world, _decision_item(), world.revise(), waiting)
    _reply(world.run_dir, "1")
    outcome = er.resolve(
        world.state,
        read=lambda prompt: pytest.fail("ответ уже записан"),
        agent=world.agent(_decision_item()),
        revise=world.revise(),
        round_no=1,
        waiting=er.Waiting(batch=waiting.batch),
    )
    assert outcome is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == FIXED
    assert pending.load(world.run_dir) is None


def test_terminal_live_answer_discards_pending(world: World) -> None:  # noqa: F811
    world.findings(_finding())
    _remote(world, _decision_item(), world.revise(), er.Waiting())
    outcome = world.resolve(["2", ""], world.agent(_decision_item()), world.revise())
    assert outcome is er.Outcome.REVISED
    assert pending.load(world.run_dir) is None
    assert not (world.run_dir / pending.REPLIES_FILE).exists()


def test_terminal_creates_no_wait_files(world: World) -> None:  # noqa: F811
    world.findings(_finding())
    outcome = world.resolve(["1", ""], world.agent(_decision_item()), world.revise())
    assert outcome is er.Outcome.REVISED
    assert not (world.run_dir / pending.PENDING_FILE).exists()
    assert not (world.run_dir / pending.REPLIES_FILE).exists()
    assert not (world.run_dir / er.WAIT_FILE).exists()


def test_wait_slot_roundtrip(tmp_path: Path) -> None:
    finding = ej.Finding("n", "e", "R1", "major", "s", "f.md", "1", "d", ("h",))
    assert er.wait_load(tmp_path, "wave-2") is None
    er.wait_save(tmp_path, "wave-2", {"round": 2, "node": None, "findings": []})
    er.wait_save(
        tmp_path, "node-x", {"round": 1, "findings": er.findings_json([finding])}
    )
    saved = er.wait_load(tmp_path, "node-x")
    assert saved is not None
    assert er.findings_from_json(saved["findings"]) == [finding]
    assert er.wait_load(tmp_path, "wave-2") == {
        "round": 2,
        "node": None,
        "findings": [],
    }
    er.wait_clear(tmp_path, "wave-2")
    assert er.wait_load(tmp_path, "wave-2") is None
    er.wait_clear(tmp_path, "node-x")
    assert not (tmp_path / er.WAIT_FILE).exists()
    er.wait_clear(tmp_path, "node-x")  # нет файла — не создаётся


def test_broken_wait_file_is_no_slot(tmp_path: Path) -> None:
    (tmp_path / er.WAIT_FILE).write_text("[1]", encoding="utf-8")
    assert er.wait_load(tmp_path, "wave-2") is None
    (tmp_path / er.WAIT_FILE).write_text(json.dumps({"wave-2": "x"}), encoding="utf-8")
    assert er.wait_load(tmp_path, "wave-2") is None


# --- личный путь: `lite.personal.flow._node` ---------------------------------

DESIGN = NODE_TEXT["20-design.md"]
DESIGN_DECISION = [
    {
        "finding": 1,
        "kind": "decision",
        "question": "Как быть с поиском?",
        "options": [
            {"label": "уточнить D-02", "recommended": True, "removes": [],
             "downgrades": []},
            {"label": "оставить", "recommended": False, "removes": [],
             "downgrades": []},
        ],
    }
]  # fmt: skip
DESIGN_SLOT = "node-personal-design"


@dataclass
class Personal:
    """Личный прогон с подменами; `calls` — счётчики каждого `advance`."""

    run: state.PersonalRun
    check: Any
    author: Any = field(default_factory=lambda: _reviser(DESIGN + "\nУточнено.\n"))
    prompts: list[str] = field(default_factory=list)
    calls: list[er.PaidCalls] = field(default_factory=list)

    @property
    def rd(self) -> Path:
        return state.run_dir(self.run.run_id)

    def advance(self, *, tty: bool, lines: tuple[str, ...] = ()) -> int:
        feed = iter(lines)

        def agent(prompt: str) -> str:
            self.prompts.append(prompt)
            return json.dumps({"items": DESIGN_DECISION}, ensure_ascii=False)

        def read(prompt: str) -> str | None:
            if not tty:
                pytest.fail("без терминала read не зовётся")
            return next(feed, None)

        return flow.advance(
            self.run,
            FakeOps(self.run),
            read=read,
            agent=agent,
            launch=self.author,
            check=self.check,
            tty=lambda: tty,
        )

    def revisions(self) -> int:
        return len([p for p in self.author.prompts if "## Режим правки" in p])

    def total(self) -> tuple[int, int, int, int]:
        c = self.calls
        return (
            sum(x.author for x in c),
            sum(x.triage for x in c),
            sum(x.revise for x in c),
            sum(x.check for x in c),
        )


@pytest.fixture
def personal(
    run: state.PersonalRun,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Personal:
    p = Personal(run, checker("FAIL", "PASS"))
    original = flow._Ctx.__init__

    def spy(self: Any, *a: Any, **k: Any) -> None:
        original(self, *a, **k)
        p.calls.append(self.calls)

    monkeypatch.setattr(flow._Ctx, "__init__", spy)
    return p


def test_personal_edge_question_written_without_tty(personal: Personal) -> None:
    assert personal.advance(tty=False) == 0
    rec = pending.load(personal.rd)
    assert rec is not None and rec["kind"] == pending.EDGE
    assert rec["options"] == ["уточнить D-02 (рекомендую)", "оставить"]
    assert personal.run.stop_reason.startswith("ждёт ответа: замечание R1 major")
    assert personal.run.step == "design"
    saved = er.wait_load(personal.rd, DESIGN_SLOT)
    assert saved is not None and saved["round"] == 1
    assert len(saved["findings"]) == 1
    assert personal.total() == (1, 1, 0, 1)


def test_personal_edge_resume_no_repaid_triage_or_check(personal: Personal) -> None:
    assert personal.advance(tty=False) == 0
    pending.record(personal.rd, pending.load(personal.rd)["id"], "1")  # type: ignore[index]
    assert personal.advance(tty=False) == 0
    assert personal.run.step == "code-pr"
    # проверка дизайна до переписывания не звалась: FAIL, перепроверка, декомпозиция
    assert personal.check.calls == [
        "personal-design-vs-brief",
        "personal-design-vs-brief",
        "personal-decomposition-vs-design-brief",
    ]
    assert len(personal.prompts) == 1 and personal.revisions() == 1
    assert pending.load(personal.rd) is None
    assert er.wait_load(personal.rd, DESIGN_SLOT) is None
    [_, second] = personal.calls
    assert (second.triage, second.revise, second.check) == (0, 1, 2)


def test_personal_edited_node_drops_saved_findings(personal: Personal) -> None:
    assert personal.advance(tty=False) == 0
    node = _bundle(personal.run) / "20-design.md"
    node.write_text(DESIGN + "\nПравка руками.\n", encoding="utf-8")
    assert personal.advance(tty=False) == 0
    assert personal.check.calls == [
        "personal-design-vs-brief",
        "personal-design-vs-brief",  # проверка заново: узел другой
        "personal-decomposition-vs-design-brief",
    ]
    assert pending.load(personal.rd) is None
    assert er.wait_load(personal.rd, DESIGN_SLOT) is None
    assert personal.revisions() == 0


def test_personal_terminal_creates_no_wait_files(personal: Personal) -> None:
    assert personal.advance(tty=True, lines=("1",)) == 0
    assert personal.run.step == "code-pr"
    assert not (personal.rd / pending.PENDING_FILE).exists()
    assert not (personal.rd / er.WAIT_FILE).exists()


def test_personal_edge_remote_paid_calls_equal_terminal(
    personal: Personal,
    tmp_path: Path,
) -> None:
    """§5.6 п.5: цикл `dfl run` → `dfl reply` платит столько же, сколько
    терминальный проход с тем же ответом."""
    assert personal.advance(tty=False) == 0
    pending.record(personal.rd, pending.load(personal.rd)["id"], "1")  # type: ignore[index]
    assert personal.advance(tty=False) == 0
    remote = (personal.total(), len(personal.prompts), personal.revisions())

    target = tmp_path / "target-2"
    target.mkdir()
    other = state.new_personal_run(
        "Заметки", "repo", "o/repo", str(target), date(2026, 11, 16)
    )
    state.save(other)
    term = Personal(other, checker("FAIL", "PASS"))
    personal.calls.clear()  # счётчики пишет шпион фикстуры
    assert term.advance(tty=True, lines=("1",)) == 0
    term.calls = list(personal.calls)
    assert (term.total(), len(term.prompts), term.revisions()) == remote
    assert remote == ((2, 1, 1, 3), 1, 1)
