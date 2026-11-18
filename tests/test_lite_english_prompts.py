"""Английский режим личного пути `dfl`: промпты агентов и edge-check `v1-en`.

Всё, что уходит агентам (интервьюер, автор узла, правка, edge-check,
сортировка), при `lang="en"` собирается без кириллицы из английских входов;
при `lang="ru"` — байт в байт как без параметра (#769 срез 1).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from governance.author_launch import LaunchOutcome
from governance.edge_check import prompt as ec_prompt
from governance.edge_check.inputs import prepare_input
from governance.edge_check.rules import load_rules
from lite import edge_triage
from lite import owner_review as orv
from lite import review_pause as rp
from lite.edge_journal import Decision, Finding
from lite.personal import flow, interviewer, nodes, state
from lite.personal.brief import (
    brief_from_json,
    brief_problems,
    demote_uncovered,
    render_brief,
)
from lite.personal.check import contracts_dir, decomposition_problems
from tests.personal_en import (
    EN_BRIEF_TURN,
    EN_DECOMPOSITION,
    EN_DESIGN,
    cyrillic,
)
from tests.test_lite_personal_flow import FakeOps

PERSONAL_EDGES = (
    "personal-design-vs-brief",
    "personal-decomposition-vs-design-brief",
)
SUBJECT = "Notes from the terminal"
BUNDLE = "workstreams/ws/bundle"
OUT = "/tmp/out.md"


def _brief_data() -> dict:
    return json.loads(EN_BRIEF_TURN)["brief"]


def _brief_text() -> str:
    return render_brief(brief_from_json(_brief_data()), lang="en")


def _entries() -> list[dict]:
    return [
        {"role": "agent", "kind": "ask", "text": "Who uses it?", "options": ["me"]},
        {"role": "human", "text": "1. me"},
        {"role": "agent", "kind": "brief", "text": EN_BRIEF_TURN},
        {"role": "check", "text": "FR-02: no acceptance criterion"},
        {"role": "agent", "kind": "summary", "text": _brief_text()},
        {"role": "human", "text": "fix: add search"},
    ]


def _finding() -> Finding:
    return Finding(
        "personal-design",
        "personal-design-vs-brief",
        "R1",
        "major",
        "FR-02 has no named mechanism",
        "20-design.md",
        "1-3",
    )


def _decision() -> Decision:
    return Decision(_finding().key, "Keep search?", "yes, keep it", "option", 1)


def _interviewer(lang: str | None) -> str:
    kw = {} if lang is None else {"lang": lang}
    return interviewer.build_prompt(
        _entries(), SUBJECT, at_cap=True, error="bad shape", **kw
    )


def _author(kind: str, lang: str | None) -> str:
    kw = {} if lang is None else {"lang": lang}
    return nodes.build_prompt(kind, SUBJECT, BUNDLE, OUT, **kw)


def _revise(kind: str, lang: str | None) -> str:
    kw = {} if lang is None else {"lang": lang}
    remarks = [rp.Remark("design", "keep it short", rp.PENDING)]
    return nodes.revise_prompt(
        kind,
        SUBJECT,
        BUNDLE,
        OUT,
        EN_DESIGN,
        "- R1 major (20-design.md:1-3): FR-02 has no named mechanism",
        orv.decisions(remarks, **kw),
        **kw,
    )


def _edge(tmp_path: Path, edge: str, lang: str | None) -> str:
    kw = {} if lang is None else {"lang": lang}
    files = {
        "00-brief.md": _brief_text(),
        "20-design.md": EN_DESIGN,
        "30-decomposition.md": EN_DECOMPOSITION,
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    subject = "20-design.md" if "design-vs" in edge else "30-decomposition.md"
    ruleset = load_rules(edge, contracts_dir(lang or "ru"))
    bases = [(r, tmp_path / nodes.FILENAMES[r]) for r in ruleset.basis_roles]
    prepared = prepare_input(ruleset, tmp_path, [tmp_path / subject], bases)
    text = ec_prompt.build_prompt(ruleset, prepared, **kw).text
    # одноразовый токен маркера — свой на каждый вызов
    return "\n".join(ln for ln in text.splitlines() if "EDGE-CHECK-DATA:" not in ln)


def _triage(lang: str | None) -> str:
    kw = {} if lang is None else {"lang": lang}
    return edge_triage.build_prompt(
        [_finding()],
        {"20-design.md": EN_DESIGN},
        {"00-brief.md": _brief_text()},
        [_decision()],
        **kw,
    )


@pytest.mark.parametrize("kind", sorted(nodes.EDGES))
def test_en_prompts_have_no_cyrillic(kind: str, tmp_path: Path) -> None:
    assert cyrillic(_interviewer("en")) == []
    assert cyrillic(_author(kind, "en")) == []
    assert cyrillic(_revise(kind, "en")) == []
    assert cyrillic(_triage("en")) == []
    edge = nodes.EDGES[kind][0]
    assert cyrillic(_edge(tmp_path, edge, "en")) == []


@pytest.mark.parametrize("kind", sorted(nodes.EDGES))
def test_ru_prompts_unchanged(kind: str, tmp_path: Path) -> None:
    assert _interviewer("ru") == _interviewer(None)
    assert _author(kind, "ru") == _author(kind, None)
    assert _revise(kind, "ru") == _revise(kind, None)
    assert _triage("ru") == _triage(None)
    edge = nodes.EDGES[kind][0]
    assert _edge(tmp_path, edge, "ru") == _edge(tmp_path, edge, None)


def test_en_prompt_files_ask_english_labels() -> None:
    design = _author("personal-design", "en")
    decomposition = _author("personal-decomposition", "en")
    assert "## Open questions" in design and "**Functions:**" in design
    assert "**Criteria:**" in decomposition and "## Checks" in decomposition
    assert "Write in English." in design and "Write in English." in decomposition
    assert "Write in English." in _interviewer("en")


def test_v1_en_schema_identical() -> None:
    v1, en = contracts_dir("ru"), contracts_dir("en")
    assert en.name == "v1-en" and v1.name == "v1"
    assert (en / "response-schema.json").read_bytes() == (
        v1 / "response-schema.json"
    ).read_bytes()


def _structure(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["items"] = [{k: v for k, v in it.items() if k != "text"} for it in doc["items"]]
    return doc


@pytest.mark.parametrize("edge", PERSONAL_EDGES)
def test_v1_en_rules_structure_equal(edge: str) -> None:
    v1 = contracts_dir("ru") / "rules" / f"{edge}.yaml"
    en = contracts_dir("en") / "rules" / f"{edge}.yaml"
    assert _structure(en) == _structure(v1)
    assert cyrillic(en.read_text(encoding="utf-8")) == []


def test_v1_en_has_only_personal_rules() -> None:
    names = sorted(p.stem for p in (contracts_dir("en") / "rules").glob("*.yaml"))
    assert names == sorted(PERSONAL_EDGES)
    for name in ("instruction.md", "README.md"):
        text = (contracts_dir("en") / name).read_text(encoding="utf-8")
        assert cyrillic(text) == []


def test_en_decomposition_passes_problems() -> None:
    brief = brief_from_json(_brief_data())
    assert decomposition_problems(EN_DECOMPOSITION, brief) == []


def test_en_decomposition_missing_criterion_is_reported() -> None:
    brief = brief_from_json(_brief_data())
    text = EN_DECOMPOSITION.replace("**Criteria:** FR-02/1", "**Criteria:** FR-01/1")
    problems = decomposition_problems(text, brief, lang="en")
    assert problems == ["criterion FR-02/1 is not covered by any BEH check"]


def test_ru_labels_still_parsed() -> None:
    brief = brief_from_json(_brief_data())
    ru = EN_DECOMPOSITION.replace("## Checks", "## Проверки").replace(
        "**Criteria:**", "**Критерии:**"
    )
    assert decomposition_problems(ru, brief) == []


def test_en_brief_render_no_cyrillic() -> None:
    assert cyrillic(_brief_text()) == []
    assert "**Acceptance criteria:**" in _brief_text()


def test_en_brief_problems_and_demotion_no_cyrillic() -> None:
    data = _brief_data()
    data["functions"][1]["criteria"] = []
    data["context"]["auth"] = ""
    brief = brief_from_json(data)
    problems = brief_problems(brief, lang="en")
    assert problems and cyrillic("\n".join(problems)) == []
    assert brief_problems(brief, lang="ru") == brief_problems(brief)
    demoted = demote_uncovered(brief, lang="en")
    assert cyrillic(render_brief(demoted, lang="en")) == []


def test_en_run_threads_language_to_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прогон `en`: интервьюер, автор и edge-check получают английский язык и
    каталог `v1-en`; бриф и промпты автора — без кириллицы."""
    monkeypatch.setattr(state, "PERSONAL_ROOT", tmp_path / "personal-runs")
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    langs: list[str] = []

    def fake_interview(run_dir, subject, *, read, agent, lang="ru"):
        langs.append(lang)
        return brief_from_json(_brief_data())

    monkeypatch.setattr(flow, "interview", fake_interview)
    texts = {"20-design.md": EN_DESIGN, "30-decomposition.md": EN_DECOMPOSITION}
    prompts: list[str] = []

    def launch(call, prompt) -> LaunchOutcome:
        prompts.append(prompt(OUT))
        path = Path(call.target_dir) / call.bundle_dir / call.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texts[call.filename], encoding="utf-8")
        return LaunchOutcome(0)

    checks: list[dict] = []

    def check(edge_id, bundle, subject, bases, **kw):
        checks.append(kw)
        return {"edge": edge_id, "verdict": "PASS", "findings": [], "subject": []}

    target = tmp_path / "target"
    target.mkdir()
    run = state.new_personal_run(
        SUBJECT, "repo", "o/repo", str(target), date(2026, 11, 18), language="en"
    )
    state.save(run)
    flow.advance(
        run,
        FakeOps(run),
        read=lambda prompt: None,
        agent=lambda p: "",
        launch=launch,
        check=check,
        tty=lambda: False,
    )
    assert langs == ["en"]
    brief = (target / run.bundle_dir / "00-brief.md").read_text(encoding="utf-8")
    assert cyrillic(brief) == []
    assert len(prompts) == 2 and cyrillic("\n".join(prompts)) == []
    assert [c["lang"] for c in checks] == ["en", "en"]
    assert {c["contracts_dir"] for c in checks} == {contracts_dir("en")}
