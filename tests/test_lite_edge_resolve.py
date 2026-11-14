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
from governance import runner
from governance.author_launch import AuthorCall, LaunchOutcome
from governance.edge_check import override
from lite import edge_journal as ej
from lite import edge_memory as em
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
SUBJECT = [{"path": FILE, "sha256": "s-node"}]


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
    stop_lines: list[str] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        return rs.run_dir(self.state.run_id)

    @property
    def node(self) -> Path:
        return self.tmp / "target" / BUNDLE / FILE

    def findings(
        self,
        *findings: dict,
        node: str = NODE,
        edge: str = EDGE,
        subject: list[dict] | None = SUBJECT,
        criteria: list[dict] | None = None,
    ) -> None:
        """Запись ребра на диске и те же находки в файле стопа раннера."""
        out = self.run_dir / "edge-check" / f"w{self.state.wave}"
        out.mkdir(parents=True, exist_ok=True)
        record = {"node": node, "edge": edge, "verdict": "FAIL",
                  "findings": list(findings), "subject": subject}  # fmt: skip
        if criteria is not None:
            record["criteria"] = criteria
        (out / f"{node}--{edge}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        self.stop_lines += [
            f"error EDGE-CHECK({node}/{edge}): {f['rule_id']} {f['class']}: "
            f"{f['statement']} @ {f['location'].get('path')}:[3, 5]"
            for f in findings
        ]
        self.stop_file("\n".join(self.stop_lines))

    def stop_file(self, text: str) -> None:
        (self.run_dir / "edge-findings.txt").write_text(text + "\n", "utf-8")

    def agent(self, items: list[dict]) -> Any:
        def call(prompt: str) -> str:
            self.prompts.append(prompt)
            return json.dumps({"items": items}, ensure_ascii=False)

        return call

    def revise(
        self,
        new_text: str = FIXED,
        code: int = 0,
        per_file: dict[str, tuple[str, int]] | None = None,
        interrupt: bool = False,
    ) -> Any:
        """Подмена `RealOps.revise`: пишет файл узла; `per_file` — имя файла →
        (текст, код); `interrupt` — Ctrl-C после записи мусора."""

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
                }
            )
            text, rc = (per_file or {}).get(author.filename, (new_text, code))
            path = self.tmp / "target" / BUNDLE / author.filename
            path.write_text(text if rc == 0 else "мусор", encoding="utf-8")
            if interrupt:
                raise KeyboardInterrupt
            return LaunchOutcome(rc, "граница отказала" if rc else "")

        return call

    def resolve(
        self,
        answers: list[str | None],
        agent: Any,
        revise: Any,
        round_no: int = 1,
        calls: er.PaidCalls | None = None,
    ) -> er.Outcome:
        feed = iter(answers)
        return er.resolve(
            self.state,
            read=lambda prompt: next(feed, None),
            agent=agent,
            revise=revise,
            round_no=round_no,
            calls=calls,
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


def _disagree(digest: str = "", rule: str = "R1") -> ej.Decision:
    """Несогласие в журнале; `digest` — находка на момент ответа."""
    key = (NODE, EDGE, rule)
    return ej.Decision(key, "Цель G-03?", "вне объёма", "disagree", 1, digest=digest)


def test_repeat_after_disagree_offers_override(world: World) -> None:
    world.findings(_finding())
    # несогласие дано на прежнем тексте узла (digest другой): узел переписан,
    # замечание вернулось — спросить снова, с принятием вопреки замечанию
    ej.append(world.run_dir, 2, _disagree(digest="old-text"))
    outcome = world.resolve(["1", ""], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.OVERRIDDEN
    assert er.OVERRIDE_OFFER in world.out()
    assert em.RETURNED in world.out()
    # решение привязано к самой находке — тот же digest, что считает раннер
    digest = override.finding_digest(NODE, EDGE, _finding(), ["s-node"])
    assert ej.overridden(world.run_dir, 2) == {digest}
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


# --- круг исправлений 1 ---


def test_stale_records_after_instrument_failure(world: World) -> None:
    """Запись FAIL прошлой попытки на диске, а стоп — отказ прибора."""
    world.findings(_finding())
    world.stop_file("error EDGE-CHECK(no_edges): у волны нет рёбер")
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == [] and world.revisions == []


def test_failure_next_to_findings_not_applicable(world: World) -> None:
    world.findings(_finding())
    world.stop_lines.append("error EDGE-CHECK(design/x): отказ ребра")
    world.stop_file("\n".join(world.stop_lines))
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE


def test_record_findings_filtered_by_stop_file(world: World) -> None:
    world.findings(_finding("R1"), _finding("R2"))
    world.stop_lines = world.stop_lines[:1]  # в стопе только R1
    world.stop_file("\n".join(world.stop_lines))
    assert world.resolve([], world.agent(FIX), world.revise()) is er.Outcome.REVISED
    findings = world.revisions[0]["findings"]
    assert "R1: цель" in findings and "R2" not in findings


def test_paid_calls_counted(world: World) -> None:
    world.findings(_finding())
    calls = er.PaidCalls()
    world.resolve([], world.agent(FIX), world.revise(code=3), calls=calls)
    # файл возвращён — вход тот же, сортировка из кэша и не платится
    world.resolve([], world.agent(FIX), world.revise(code=3), calls=calls)
    assert (calls.triage, calls.revise, calls.check) == (1, 2, 0)
    assert calls.line() == "платных вызовов: сортировка 1, переписывание 2, проверка 0"


def test_option_effects_shown_and_bounded(world: World) -> None:
    world.findings(_finding())
    items = _decision_item(removes=["FR-02", "FR-01"])
    items[0]["options"][1]["downgrades"] = ["FR-01"]
    outcome = world.resolve(["2", ""], world.agent(items), world.revise())
    assert outcome is er.Outcome.REVISED
    assert "  2. убрать FR-02, удалит: FR-02" in world.out()
    assert "FR-01" not in world.out()  # человек его не видел — не разрешён
    [d] = ej.load(world.run_dir, 2)
    assert d.allows == {"removes": ["FR-02"], "downgrades": []}


DESIGN = "#### D-1: модуль\ntext\n#### D-2: вход\ntext\n"
ACCEPT = "#### AC-01: вход\ntext\n#### AC-02: выход\ntext\n"


def _two_files(world: World) -> None:
    """Волна W4: design и acceptance, по находке на файл."""
    world.state.wave = 4
    rs.save(world.state)
    bundle = world.tmp / "target" / BUNDLE
    (bundle / "20-design.md").write_text(DESIGN, encoding="utf-8")
    (bundle / "25-acceptance.md").write_text(ACCEPT, encoding="utf-8")
    world.findings(
        _finding("R1", "20-design.md"),
        node="design",
        edge="design-vs-requirements-behaviour",
    )
    world.findings(
        _finding("R2", "25-acceptance.md"),
        node="acceptance",
        edge="acceptance-vs-requirements-behaviour",
    )


def _fixes(n: int) -> list[dict]:
    return [
        {"finding": i, "kind": "fix", "question": "", "options": []}
        for i in range(1, n + 1)
    ]


def test_allowances_scoped_per_file(world: World) -> None:
    _two_files(world)
    # находки по порядку записей рёбер: 1 — acceptance, 2 — design
    items = [
        _fixes(1)[0],
        {
            "finding": 2,
            "kind": "decision",
            "question": "Убрать AC-02?",
            "options": [
                {
                    "label": "да, убрать AC-02",
                    "recommended": True,
                    "removes": ["AC-02"],
                    "downgrades": [],
                },  # fmt: skip
                {"label": "нет", "recommended": False},
            ],
        },
    ]
    # решение по design разрешает удалить AC-02, но AC-02 — в acceptance
    per_file = {
        "20-design.md": (DESIGN + "\nправка\n", 0),
        "25-acceptance.md": ("#### AC-01: вход\ntext\n", 0),
    }
    revise = world.revise(per_file=per_file)
    outcome = world.resolve(["1", ""], world.agent(items), revise)
    assert outcome is er.Outcome.STOPPED
    assert "AC-02" in world.out()
    bundle = world.tmp / "target" / BUNDLE
    assert (bundle / "20-design.md").read_text(encoding="utf-8") == DESIGN
    assert (bundle / "25-acceptance.md").read_text(encoding="utf-8") == ACCEPT


def test_multi_file_restore(world: World) -> None:
    _two_files(world)
    # acceptance переписан первым, design отказал — acceptance возвращён
    per_file = {
        "25-acceptance.md": (ACCEPT + "\nправка\n", 0),
        "20-design.md": (DESIGN, 3),
    }
    revise = world.revise(per_file=per_file)
    outcome = world.resolve([], world.agent(_fixes(2)), revise)
    assert outcome is er.Outcome.STOPPED
    assert len(world.revisions) == 2
    bundle = world.tmp / "target" / BUNDLE
    assert (bundle / "20-design.md").read_text(encoding="utf-8") == DESIGN
    assert (bundle / "25-acceptance.md").read_text(encoding="utf-8") == ACCEPT


def test_symlinked_node_not_applicable(world: World) -> None:
    world.findings(_finding())
    real = world.tmp / "elsewhere.md"
    real.write_text(BEFORE, encoding="utf-8")
    world.node.unlink()
    world.node.symlink_to(real)
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == [] and FILE in world.out()


def test_symlinked_basis_not_applicable(world: World) -> None:
    world.findings(_finding())
    charter = world.tmp / "target" / BUNDLE / "00-charter.md"
    real = world.tmp / "charter-real.md"
    real.write_text(CHARTER, encoding="utf-8")
    charter.unlink()
    charter.symlink_to(real)
    agent = world.agent(FIX)
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == []


def test_restore_failure_lists_files(world: World, monkeypatch) -> None:
    world.findings(_finding())
    real_publish = er.safe_io.publish

    def publish(root: Path, rel: str, data: bytes) -> None:
        if str(rel).startswith(BUNDLE + "/"):
            raise er.safe_io.SafeIOError("запись запрещена")
        real_publish(root, rel, data)

    monkeypatch.setattr(er.safe_io, "publish", publish)
    outcome = world.resolve([], world.agent(FIX), world.revise(code=3))
    assert outcome is er.Outcome.STOPPED
    assert f"не возвращены: {FILE}" in world.out()


def test_yo_spelling_accepted(world: World) -> None:
    world.findings(_finding())
    item = _decision_item()
    world.resolve(["Своё: покрыть FR-03", ""], world.agent(item), world.revise())
    first = [(d.kind, d.answer) for d in ej.load(world.run_dir, 2)]
    assert first == [("own", "покрыть FR-03")]
    (world.run_dir / ej.JOURNAL).unlink()  # иначе вопрос не повторится
    world.resolve(["свое: другое", ""], world.agent(item), world.revise())
    got = [(d.kind, d.answer) for d in ej.load(world.run_dir, 2)]
    assert got == [("own", "другое")]


def test_interrupt_restores_round_files(world: World) -> None:
    world.findings(_finding())
    with pytest.raises(KeyboardInterrupt):
        world.resolve([], world.agent(FIX), world.revise(interrupt=True))
    assert world.node.read_text(encoding="utf-8") == BEFORE


def test_unrated_stop_line_not_applicable(world: World) -> None:
    """Строка раннера «FAIL без находок» в стопе — разбирать нечего, даже
    рядом с записью FAIL прошлой попытки или с блокирующей находкой."""
    world.findings(_finding())
    unrated = f"error EDGE-CHECK(design/design-vs-x): {runner.EDGE_UNRATED}"
    agent = world.agent(FIX)
    world.stop_file(unrated)  # только строка раннера, запись — прошлая
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    world.stop_file("\n".join([*world.stop_lines, unrated]))
    assert world.resolve([], agent, world.revise()) is er.Outcome.NOT_APPLICABLE
    assert world.prompts == [] and world.revisions == []


def test_override_not_offered_without_subject_hashes(world: World) -> None:
    """Запись без хэшей subject: принятию не к чему привязаться — вопрос
    после несогласия задаётся без варианта «принять вопреки», строки нет."""
    world.findings(_finding(), subject=None)
    ej.append(world.run_dir, 2, _disagree(digest="old-text"))
    outcome = world.resolve(["2", "", "2", ""], world.agent(FIX), world.revise())
    assert er.OVERRIDE_OFFER not in world.out()
    assert outcome is er.Outcome.STOPPED
    assert ej.overridden(world.run_dir, 2) == set()
    assert all(d.kind != "override" for d in ej.load(world.run_dir, 2))


# --- память решений между кругами и вызовами (финальное ревью, I1–I3) ---

DIGEST = override.finding_digest(NODE, EDGE, _finding(), ["s-node"])


def test_disagree_same_text_not_reasked(world: World) -> None:
    """Несогласие на том же тексте узла (переписывания ещё не было) — не
    спрашивать снова и не писать «вернулось»."""
    world.findings(_finding())
    ej.append(world.run_dir, 2, _disagree(digest=DIGEST))
    outcome = world.resolve([], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.REVISED
    assert er.FIXING.format(n=1, m=0) in world.out()
    assert em.RETURNED not in world.out() and er.OVERRIDE_OFFER not in world.out()
    assert "не согласен: вне объёма" in world.revisions[0]["decisions"]


def test_disagree_without_digest_reasked_neutrally(world: World) -> None:
    """Старая строка без digest — неизвестно, менялся ли текст: спросить, но
    без «вернулось» и без принятия вопреки (не последний круг)."""
    world.findings(_finding())
    ej.append(world.run_dir, 2, _disagree())
    outcome = world.resolve(["своё: покрыть", ""], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.REVISED
    out = world.out()
    assert em.AGAIN in out
    assert em.RETURNED not in out and er.OVERRIDE_OFFER not in out
    last = world.resolve(
        ["1", ""], world.agent(FIX), world.revise(), round_no=er.MAX_ROUNDS
    )
    assert last is er.Outcome.REVISED  # «своё» — последнее решение, не спросят


def test_disagree_then_stop_next_run_does_not_reask(world: World) -> None:
    """Несогласие с K1, остановка на K2; следующий `dfl run` не спрашивает K1."""
    world.findings(_finding("R1"), _finding("R2"))
    items = _decision_item() + [{**_decision_item()[0], "finding": 2}]
    first = world.resolve(
        ["не согласен: вне объёма", "", None], world.agent(items), world.revise()
    )
    assert first is er.Outcome.STOPPED
    world.said.clear()
    second = world.resolve(["1", ""], world.agent(items), world.revise())
    assert second is er.Outcome.REVISED
    out = world.out()
    assert "замечание R1" not in out and em.RETURNED not in out
    assert er.FIXING.format(n=1, m=1) in out
    kinds = [(d.key[2], d.kind) for d in ej.load(world.run_dir, 2)]
    assert kinds == [("R1", "disagree"), ("R2", "option")]


def test_current_override_not_asked(world: World) -> None:
    world.findings(_finding())
    ej.append(world.run_dir, 2, _override(DIGEST))
    outcome = world.resolve([], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.OVERRIDDEN
    assert er.FIXING.format(n=1, m=0) in world.out() and world.revisions == []


def _override(digest: str) -> ej.Decision:
    return ej.Decision(
        (NODE, EDGE, "R1"), "Цель G-03?", er.OVERRIDE_OFFER, "override", 2,
        digest=digest, subject=["s-node"],
    )  # fmt: skip


def test_override_then_text_changed_asked_again(world: World) -> None:
    """Принятие вопреки, затем текст узла изменился: принятие не действует —
    человека спрашивают снова (с причиной) и снова предлагают принять."""
    world.findings(_finding())
    first = world.resolve(
        ["3", ""], world.agent(_decision_item()), world.revise(), er.MAX_ROUNDS
    )
    assert first is er.Outcome.OVERRIDDEN
    world.said.clear()
    world.stop_lines = []
    world.findings(_finding(), subject=[{"path": FILE, "sha256": "s-edited"}])
    second = world.resolve(["1", ""], world.agent(FIX), world.revise())
    out = world.out()
    assert "текст узла изменился после вашего принятия" in out
    assert er.OVERRIDE_OFFER in out
    assert second is er.Outcome.OVERRIDDEN and world.revisions == []
    edited = override.finding_digest(NODE, EDGE, _finding(), ["s-edited"])
    assert edited in ej.overridden(world.run_dir, 2)


def test_chosen_removal_then_stop_allowed_next_run(world: World) -> None:
    """Выбранное удаление FR-02 (K1), стоп на K2; следующий вызов разрешает
    переписыванию удалить FR-02 — решение берётся из журнала."""
    world.findings(_finding("R1"), _finding("R2"))
    items = _decision_item(removes=["FR-02"]) + [{**_decision_item()[0], "finding": 2}]
    first = world.resolve(["2", "", None], world.agent(items), world.revise())
    assert first is er.Outcome.STOPPED
    second = world.resolve(["1", ""], world.agent(items), world.revise(WITHOUT_FR02))
    assert second is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == WITHOUT_FR02


def test_journal_allowances_scoped_per_file(world: World) -> None:
    """Разрешение из журнала по находке design не снимает AC-02 в acceptance."""
    _two_files(world)
    key = ("design", "design-vs-requirements-behaviour", "R1")
    ej.append(
        world.run_dir,
        4,
        ej.Decision(key, "Убрать AC-02?", "да", "option", 1,
                    {"removes": ["AC-02"], "downgrades": []}),
    )  # fmt: skip
    per_file = {
        "20-design.md": (DESIGN + "\nправка\n", 0),
        "25-acceptance.md": ("#### AC-01: вход\ntext\n", 0),
    }
    outcome = world.resolve([], world.agent(_fixes(2)), world.revise(per_file=per_file))
    assert outcome is er.Outcome.STOPPED
    assert "AC-02" in world.out()


def test_removal_then_own_answer_not_allowed(world: World) -> None:
    """Удаление FR-02 выбрано, затем по той же находке ответ «своё: …» —
    разрешение берётся только из последнего решения: FR-02 удалять нельзя."""
    world.findings(_finding())
    removal = ej.Decision(
        (NODE, EDGE, "R1"), "Цель G-03?", "убрать FR-02", "option", 1,
        {"removes": ["FR-02"], "downgrades": []}, digest=DIGEST,
    )  # fmt: skip
    own = ej.Decision(
        (NODE, EDGE, "R1"), "Цель G-03?", "покрыть", "own", 1, digest=DIGEST
    )
    ej.append(world.run_dir, 2, removal)
    ej.append(world.run_dir, 2, own)
    outcome = world.resolve([], world.agent(FIX), world.revise(WITHOUT_FR02))
    assert outcome is er.Outcome.STOPPED
    assert "FR-02" in world.out()
    assert world.node.read_text(encoding="utf-8") == BEFORE


def test_removal_spent_after_text_changed(world: World) -> None:
    """Удаление выбрано на прежнем тексте; узел переписан (digest другой),
    находка того же ключа вернулась — разрешение не переиспользуется."""
    world.findings(_finding(), subject=[{"path": FILE, "sha256": "s-edited"}])
    ej.append(
        world.run_dir,
        2,
        ej.Decision(
            (NODE, EDGE, "R1"), "Цель G-03?", "убрать FR-02", "option", 1,
            {"removes": ["FR-02"], "downgrades": []}, digest=DIGEST,
        ),
    )  # fmt: skip
    outcome = world.resolve([], world.agent(FIX), world.revise(WITHOUT_FR02))
    assert outcome is er.Outcome.STOPPED
    assert world.node.read_text(encoding="utf-8") == BEFORE


# --- принятие вопреки: группа правила целиком (ревью #762, находка A) ---


def _goal(goal: str, rule: str = "R1") -> dict:
    """Находка правила `rule` про цель `goal` (тот же класс и текст узла)."""
    return {**_finding(rule), "statement": f"цель {goal} не покрыта"}


def _group_items(kinds: list[str]) -> list[dict]:
    """Пункты сортировки: `decision` — вопрос с двумя вариантами, `fix`."""
    return [
        {**_decision_item()[0], "finding": no}
        if kind == "decision"
        else {"finding": no, "kind": "fix", "question": "", "options": []}
        for no, kind in enumerate(kinds, 1)
    ]


def test_same_rule_findings_one_question_listing_all(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"))
    items = _group_items(["decision", "fix"])
    outcome = world.resolve(
        ["3", ""], world.agent(items), world.revise(), er.MAX_ROUNDS
    )
    out = world.out()
    assert er.FIXING.format(n=1, m=1) in out  # fix виден в вопросе группы
    assert "цель G-03 не покрыта" in out and "цель G-05 не покрыта" in out
    assert er.override_offer(2, "R1") in out
    assert outcome is er.Outcome.OVERRIDDEN and world.revisions == []
    [d] = ej.load(world.run_dir, 2)
    assert d.kind == "override"


def test_same_rule_option_keeps_both_in_rewrite(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"))
    items = _group_items(["decision", "fix"])
    outcome = world.resolve(
        ["1", ""], world.agent(items), world.revise(), er.MAX_ROUNDS
    )
    assert outcome is er.Outcome.REVISED
    findings = world.revisions[0]["findings"]
    assert "G-03" in findings and "G-05" in findings


def test_mixed_rules_only_overridden_group_dropped(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"), _goal("G-07", rule="R2"))
    items = _group_items(["decision", "decision", "decision"])
    outcome = world.resolve(
        ["3", "", "1", ""], world.agent(items), world.revise(), er.MAX_ROUNDS
    )
    assert outcome is er.Outcome.REVISED
    assert er.FIXING.format(n=0, m=3) in world.out()  # R1-2 снят принятием
    assert "цель G-05 не покрыта" not in world.out()
    findings = world.revisions[0]["findings"]
    assert "G-07" in findings
    assert "G-03" not in findings and "G-05" not in findings


def test_latest_option_after_override_keeps_group(world: World) -> None:
    """Журнал: принятие, затем выбор варианта по той же группе — действует
    последнее решение, переписывание получает всю группу."""
    world.findings(_goal("G-03"), _goal("G-05"))
    ej.append(world.run_dir, 2, _override(DIGEST))
    later = ej.Decision(
        (NODE, EDGE, "R1"), "Цель?", "покрыть", "option", 2, digest=DIGEST
    )
    ej.append(world.run_dir, 2, later)
    outcome = world.resolve(
        [], world.agent(_group_items(["fix", "fix"])), world.revise()
    )
    assert outcome is er.Outcome.REVISED
    findings = world.revisions[0]["findings"]
    assert "G-03" in findings and "G-05" in findings


# --- принятие вопреки: общий предикат с раннером (ревью #762, находка B) ---

INSUFFICIENT = [
    {"id": "R1", "status": "fail", "reason": "цель не покрыта"},
    {"id": "R3", "status": "insufficient", "reason": ""},
]
EXPLAINED = [{"id": "R1", "status": "fail", "reason": "цель не покрыта"}]
UNRATED = f"принять вопреки нельзя: пункт правил ребра {EDGE} не оценён"


def test_unrated_criterion_no_override_offered(world: World) -> None:
    world.findings(_finding(), criteria=INSUFFICIENT)
    outcome = world.resolve(
        ["3", "", "3", ""],
        world.agent(_decision_item()),
        world.revise(),
        er.MAX_ROUNDS,
    )
    out = world.out()
    assert er.OVERRIDE_OFFER not in out
    assert out.count(UNRATED) == 1
    assert outcome is er.Outcome.STOPPED
    assert ej.overridden(world.run_dir, 2) == set()


def test_unrated_criterion_recorded_override_not_overridden(world: World) -> None:
    """Принятие в журнале, но раннер его не примет (`insufficient`): не
    OVERRIDDEN и не пустое «переписывание» — остановка с причиной."""
    world.findings(_finding(), criteria=INSUFFICIENT)
    ej.append(world.run_dir, 2, _override(DIGEST))
    outcome = world.resolve([], world.agent(FIX), world.revise())
    assert outcome is er.Outcome.STOPPED
    assert world.out().count(UNRATED) == 1
    assert world.revisions == []


def test_explained_fail_override_offered(world: World) -> None:
    world.findings(_finding(), criteria=EXPLAINED)
    outcome = world.resolve(
        ["3", ""], world.agent(_decision_item()), world.revise(), er.MAX_ROUNDS
    )
    assert er.OVERRIDE_OFFER in world.out()
    assert outcome is er.Outcome.OVERRIDDEN


# --- recheck #762: вопрос по каждому решению группы, причины отказа ---

BOTH_APPLIED = "# Req\n\n#### FR-01: вход\n**Priority**: Should\ntext\n"


def _choice(no: int, question: str, kind: str, target: str) -> dict:
    """Пункт `decision` со своим вопросом; второй вариант — `kind`
    (`removes`/`downgrades`) пункта `target`."""
    effects = {"removes": [], "downgrades": [], kind: [target]}
    return {
        "finding": no,
        "kind": "decision",
        "question": question,
        "options": [
            {"label": "переписать", "recommended": True, "removes": [],
             "downgrades": []},
            {"label": f"снять {target}", "recommended": False, **effects},
        ],
    }  # fmt: skip


TWO_CHOICES = [
    _choice(1, "Цель G-03 — понизить FR-01?", "downgrades", "FR-01"),
    _choice(2, "Цель G-05 — убрать FR-02?", "removes", "FR-02"),
]


def test_group_decisions_each_asked_permissions_united(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"))
    outcome = world.resolve(
        ["2", "", "2", ""],
        world.agent(TWO_CHOICES),
        world.revise(BOTH_APPLIED),
        er.MAX_ROUNDS,
    )
    out = world.out()
    assert "Цель G-03 — понизить FR-01?" in out and "Цель G-05 — убрать FR-02?" in out
    assert out.count(er.override_offer(2, "R1")) == 2
    assert outcome is er.Outcome.REVISED
    assert world.node.read_text(encoding="utf-8") == BOTH_APPLIED
    answers = [d.answer for d in ej.load(world.run_dir, 2)]
    assert answers == ["снять FR-01", "снять FR-02"]


def test_group_override_on_second_question_supersedes_first(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"))
    outcome = world.resolve(
        ["1", "", "3", ""], world.agent(TWO_CHOICES), world.revise(), er.MAX_ROUNDS
    )
    assert outcome is er.Outcome.OVERRIDDEN and world.revisions == []
    assert ej.overridden(world.run_dir, 2) == {DIGEST}
    assert [d.kind for d in ej.load(world.run_dir, 2)] == ["option", "override"]


def test_group_override_on_first_question_skips_rest(world: World) -> None:
    world.findings(_goal("G-03"), _goal("G-05"))
    outcome = world.resolve(
        ["3", ""], world.agent(TWO_CHOICES), world.revise(), er.MAX_ROUNDS
    )
    assert outcome is er.Outcome.OVERRIDDEN
    assert "Цель G-05 — убрать FR-02?" not in world.out()


def test_neighbour_not_applicable_reason_named(world: World) -> None:
    """Соседнее ребро N/A: принять нельзя, и повтор проверки не советуется."""
    world.findings(_finding())
    out_dir = world.run_dir / "edge-check" / "w2"
    other = "requirements-vs-engineer-brief"
    (out_dir / f"{NODE}--{other}.json").write_text(
        json.dumps(
            {
                "node": NODE,
                "edge": other,
                "verdict": "N/A",
                "findings": [],
                "subject": SUBJECT,
            }
        ),  # fmt: skip
        encoding="utf-8",
    )
    outcome = world.resolve(
        ["3", "", "3", ""],
        world.agent(_decision_item()),
        world.revise(),
        er.MAX_ROUNDS,
    )
    out = world.out()
    assert f"ребро {other} неприменимо — поправьте узел по замечаниям" in out
    assert "повторите проверку" not in out and er.OVERRIDE_OFFER not in out
    assert outcome is er.Outcome.STOPPED
