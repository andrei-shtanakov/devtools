"""Разбор замечаний edge-check разговором (lite/edge_resolve.py).

Человек (`read`), агент сортировки и переписывание (`revise`) подменены;
каталог прогона и чекаут цели — в `tmp_path`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from governance import run_state as rs
from governance.author_launch import AuthorCall, LaunchOutcome
from lite import edge_journal as ej
from lite import edge_resolve as er
from lite.structure import AgentUnavailable

NODE = "requirements"
EDGE = "requirements-vs-charter"
FILE = "10-requirements.md"
BUNDLE = "spec"

BEFORE = """# Req

#### FR-01: вход
**Priority**: Must
text

#### FR-02: выход
**Priority**: Should
text
"""
FIXED = BEFORE + "\nG-03: явно отклонена — вне объёма\n"
WITHOUT_FR02 = BEFORE.replace("#### FR-02: выход\n**Priority**: Should\ntext\n", "")
CHARTER = "# Charter\nG-01 G-03\n"


def _finding(rule: str = "R1", path: str | None = FILE, cls: str = "major") -> dict:
    loc: dict[str, Any] = {"lines": [3, 5]}
    if path is not None:
        loc["path"] = path
    return {"rule_id": rule, "class": cls, "statement": f"{rule}: цель не покрыта",
            "location": loc}  # fmt: skip


@dataclass
class World:
    """Прогон в `stopped_review` и записанные вызовы подмен."""

    tmp: Path
    state: rs.RunState
    prompts: list[str] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    said: list[tuple[str, int]] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        return rs.run_dir(self.state.run_id)

    @property
    def node(self) -> Path:
        return self.tmp / "target" / BUNDLE / FILE

    def findings(self, *findings: dict, node: str = NODE, edge: str = EDGE) -> None:
        out = self.run_dir / "edge-check" / f"w{self.state.wave}"
        out.mkdir(parents=True, exist_ok=True)
        record = {"node": node, "edge": edge, "verdict": "FAIL",
                  "findings": list(findings)}  # fmt: skip
        (out / f"{node}--{edge}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )

    def agent(self, items: list[dict]) -> Any:
        def call(prompt: str) -> str:
            self.prompts.append(prompt)
            return json.dumps({"items": items}, ensure_ascii=False)

        return call

    def revise(self, new_text: str = FIXED, code: int = 0) -> Any:
        def call(
            author: AuthorCall,
            kind: str,
            subject: str,
            current: str,
            findings: str,
            decisions: str,
        ) -> LaunchOutcome:
            self.revisions.append(
                {
                    "call": author,
                    "kind": kind,
                    "current": current,
                    "findings": findings,
                    "decisions": decisions,
                }  # fmt: skip
            )
            if code == 0:
                self.node.write_text(new_text, encoding="utf-8")
            else:
                self.node.write_text("мусор", encoding="utf-8")
            return LaunchOutcome(code, "граница отказала" if code else "")

        return call

    def resolve(
        self, answers: list[str | None], agent: Any, revise: Any, round_no: int = 1
    ) -> er.Outcome:
        feed = iter(answers)
        return er.resolve(
            self.state,
            read=lambda prompt: next(feed, None),
            agent=agent,
            revise=revise,
            round_no=round_no,
        )

    def out(self) -> str:
        return "\n".join(text for text, _ in self.said)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    bundle = tmp_path / "target" / BUNDLE
    bundle.mkdir(parents=True)
    (bundle / FILE).write_text(BEFORE, encoding="utf-8")
    (bundle / "00-charter.md").write_text(CHARTER, encoding="utf-8")
    state = rs.new_run(
        mode="test",
        subject="тема",
        repo="alpha",
        repo_slug="o/alpha",
        ws_id="WS-1",
        target_dir=str(tmp_path / "target"),
        bundle_dir=BUNDLE,
        profile="profiles/team-exp.yaml",
        run_id="WS-1-a",
    )
    state.status, state.wave = "stopped_review", 2
    rs.save(state)
    w = World(tmp_path, state)
    monkeypatch.setattr(er, "say", lambda text, level=0: w.said.append((text, level)))
    return w


FIX = [{"finding": 1, "kind": "fix", "question": "", "options": []}]


def _decision_item(removes: list[str] | None = None) -> list[dict]:
    return [
        {
            "finding": 1,
            "kind": "decision",
            "question": "Цель G-03 — покрыть или отклонить?",
            "options": [
                {
                    "label": "покрыть требованием",
                    "recommended": True,
                    "removes": [],
                    "downgrades": [],
                },  # fmt: skip
                {
                    "label": "убрать FR-02",
                    "recommended": False,
                    "removes": removes or [],
                    "downgrades": [],
                },  # fmt: skip
            ],
        }
    ]


def test_fix_only_no_questions_revised(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve([], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == FIXED
    assert er.FIXING.format(n=1, m=0) in world.out()
    rev = world.revisions[0]
    assert rev["kind"] == NODE and rev["current"] == BEFORE
    assert "R1: цель не покрыта" in rev["findings"]
    assert rev["call"].filename == FILE and rev["call"].bundle_dir == BUNDLE
    assert CHARTER in world.prompts[0]  # основание дошло до сортировки
    backup = world.run_dir / "edge-revise" / "1" / FILE
    assert backup.read_text(encoding="utf-8") == BEFORE
    assert any(lv == 1 and "исправлено" in t for t, lv in world.said)


def test_decision_option_recorded_and_passed_to_revise(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve(["1", ""], world.agent(_decision_item()), world.revise())
    assert outcome is er.Outcome.REVISED
    [d] = ej.load(world.run_dir, 2)
    assert d.key == (NODE, EDGE, "R1") and d.kind == "option"
    assert d.answer == "покрыть требованием" and d.round == 1
    decisions = world.revisions[0]["decisions"]
    assert "Цель G-03 — покрыть или отклонить? → покрыть требованием" in decisions
    out = world.out()
    assert er.FIXING.format(n=0, m=1) in out
    assert "1. покрыть требованием (рекомендую)" in out
    assert er.OVERRIDE_OFFER not in out  # круг 1, несогласия не было


def test_disagree_recorded(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve(
        ["не согласен: цель вне объёма", ""],
        world.agent(_decision_item()),
        world.revise(),
    )
    assert outcome is er.Outcome.REVISED
    [d] = ej.load(world.run_dir, 2)
    assert d.kind == "disagree" and d.answer == "цель вне объёма"
    assert "не согласен: цель вне объёма" in world.revisions[0]["decisions"]


def test_disagree_without_reason_reasks(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve(
        ["не согласен:", "", "своё: покрыть FR-03", ""],
        world.agent(_decision_item()),
        world.revise(),
    )
    assert outcome is er.Outcome.REVISED
    [d] = ej.load(world.run_dir, 2)
    assert d.kind == "own" and d.answer == "покрыть FR-03"


def test_interrupted_answers_kept(world: World) -> None:
    world.findings(_finding("R1"), _finding("R2"))
    items = _decision_item() + [{**_decision_item()[0], "finding": 2}]
    outcome = world.resolve(["2", "", None], world.agent(items), world.revise())
    assert outcome is er.Outcome.STOPPED
    [d] = ej.load(world.run_dir, 2)
    assert d.key[2] == "R1" and d.answer == "убрать FR-02"
    assert world.revisions == []
    assert world.node.read_text(encoding="utf-8") == BEFORE


def test_skip_word_stops(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve(
        ["пропустить", ""], world.agent(_decision_item()), world.revise()
    )
    assert outcome is er.Outcome.STOPPED
    assert ej.load(world.run_dir, 2) == [] and world.revisions == []


def test_triage_failure_falls_back_to_stop(world: World) -> None:
    world.findings(_finding())

    def down(prompt: str) -> str:
        raise AgentUnavailable("claude не найден")

    assert world.resolve([], down, world.revise()) is er.Outcome.STOPPED
    assert "claude не найден" in world.out()
    assert world.revisions == []
    bad = world.resolve([], lambda p: '{"items": 1}', world.revise())
    assert bad is er.Outcome.STOPPED and world.revisions == []


def test_weakened_rewrite_restored(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve([], world.agent(FIX), world.revise(WITHOUT_FR02))
    assert outcome is er.Outcome.STOPPED
    assert world.node.read_text(encoding="utf-8") == BEFORE
    assert "FR-02" in world.out()


def test_revise_refused_restored(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve([], world.agent(FIX), world.revise(code=3))
    assert outcome is er.Outcome.STOPPED
    assert world.node.read_text(encoding="utf-8") == BEFORE
    assert "граница отказала" in world.out()


def test_chosen_removal_allowed(world: World) -> None:
    world.findings(_finding())
    items = _decision_item(removes=["FR-02"])
    outcome = world.resolve(["2", ""], world.agent(items), world.revise(WITHOUT_FR02))
    assert outcome is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == WITHOUT_FR02
    [d] = ej.load(world.run_dir, 2)
    assert d.allows == {"removes": ["FR-02"], "downgrades": []}


def test_repeat_after_disagree_offers_override(world: World) -> None:
    world.findings(_finding())
    ej.append(
        world.run_dir,
        2,
        ej.Decision((NODE, EDGE, "R1"), "Цель G-03?", "вне объёма", "disagree", 1),
    )
    # прошлое решение есть: сортировка даёт fix, но после несогласия
    # человека спрашивают снова — с принятием вопреки замечанию
    outcome = world.resolve(["1", ""], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.OVERRIDDEN
    assert er.OVERRIDE_OFFER in world.out()
    assert ej.overridden(world.run_dir, 2) == {(NODE, EDGE, "R1")}
    assert world.revisions == []


def test_override_on_last_round(world: World) -> None:
    world.findings(_finding())
    outcome = world.resolve(
        ["3", ""], world.agent(_decision_item()), world.revise(), er.MAX_ROUNDS
    )
    assert outcome is er.Outcome.OVERRIDDEN
    assert world.revisions == []


def test_override_offer_absent_in_strict(world: World) -> None:
    world.state.mode = "strict"
    world.findings(_finding())
    outcome = world.resolve(
        ["3", "", "3", ""],
        world.agent(_decision_item()),
        world.revise(),
        er.MAX_ROUNDS,
    )
    assert er.OVERRIDE_OFFER not in world.out()
    assert outcome is er.Outcome.STOPPED  # «3» — нет такого варианта, дважды
    assert ej.overridden(world.run_dir, 2) == set()


def test_unresolvable_file_no_resolution(world: World) -> None:
    world.findings(_finding(path="20-design.md"))
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    world.findings(_finding(path=None))
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == [] and world.revisions == []


def test_instrument_failure_not_applicable(world: World) -> None:
    world.findings(_finding(cls="minor"))  # FAIL без блокирующих
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    world.state.status = "stopped_gate"
    world.findings(_finding())
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == []
