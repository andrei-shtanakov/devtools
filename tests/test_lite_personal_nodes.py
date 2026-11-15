"""Edge rules and node authoring (design, decomposition) of the personal path."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from governance.author_launch import AuthorCall, LaunchOutcome
from governance.decomposition_guard import graph_findings, parse_dt_tasks
from governance.edge_check.author_rules import DEFAULT_CONTRACTS, rules_for
from governance.edge_check.rules import load_rules
from governance.task_bridge import parse_behaviour
from lite.personal import nodes, state

FIXTURES = Path(__file__).parent / "fixtures" / "personal"
_CRITERIA = re.compile(r"\*\*Критерии:\*\*\s*(.+)$", re.MULTILINE)


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    return root


def _run() -> state.PersonalRun:
    run = state.new_personal_run(
        "Заметки", "git@github.com:o/r.git", "o/r", "/tmp/t", date(2026, 11, 15)
    )
    state.save(run)
    return run


class FakeLaunch:
    """Fake `launch_author`: records the call and the rendered prompt."""

    def __init__(self) -> None:
        self.calls: list[AuthorCall] = []
        self.prompts: list[str] = []

    def __call__(
        self, call: AuthorCall, make_prompt: Callable[[str], str], **_: object
    ) -> LaunchOutcome:
        self.calls.append(call)
        self.prompts.append(make_prompt("/calls/x/stage/" + call.filename))
        return LaunchOutcome(0)


def _section(text: str, title: str) -> str:
    """Body of the `## <title>` section up to the next `## ` heading."""
    m = re.search(rf"^## {title}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert m is not None, title
    return m.group(1)


def test_rules_load() -> None:
    for kind, (edge, bases) in nodes.EDGES.items():
        rules = load_rules(edge, DEFAULT_CONTRACTS)
        assert rules.subject_role == kind
        assert list(rules.basis_roles) == [role for role, _ in bases]
        assert rules.severity.blocking == {"blocker", "major"}
        assert all(nodes.FILENAMES[role] == name for role, name in bases)
    assert nodes.FILENAMES["personal-design"] == "20-design.md"
    assert nodes.FILENAMES["personal-decomposition"] == "30-decomposition.md"


def test_enterprise_author_rules_unchanged() -> None:
    for kind in ("design", "decomposition"):
        text = rules_for(kind)
        assert text
        assert "personal-" not in text


@pytest.mark.parametrize(
    ("kind", "filename", "bases"),
    [
        ("personal-design", "20-design.md", ["00-brief.md"]),
        ("personal-decomposition", "30-decomposition.md", ["20-design.md"]),
    ],
)
def test_author_node_call_and_prompt(kind: str, filename: str, bases: list) -> None:
    run = _run()
    fake = FakeLaunch()
    outcome = nodes.author_node(run, kind, launch=fake)
    assert outcome.ok
    (call,) = fake.calls
    assert call == AuthorCall(
        run_id=run.run_id,
        run_dir=os.path.realpath(state.run_dir(run.run_id)),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        filename=filename,
    )
    (prompt,) = fake.prompts
    assert f"/calls/x/stage/{filename}" in prompt
    for base in [*bases, "00-brief.md"]:
        assert f"{run.bundle_dir}/{base}" in prompt
    rules = load_rules(nodes.EDGES[kind][0], DEFAULT_CONTRACTS)
    for item in rules.items:
        assert item.text in prompt
    assert run.subject in prompt


def test_decomposition_prompt_teaches_grammar() -> None:
    fake = FakeLaunch()
    nodes.author_node(_run(), "personal-decomposition", launch=fake)
    prompt = fake.prompts[0]
    for marker in (
        "## Проверки",
        "## Задачи",
        "#### BEH-01:",
        "**checked_by**",
        "`kind:",
        "`target:",
        "**Критерии:** FR-01/1",
        "#### DT-01:",
        "· type: implement · owner:",
        "scenarios: [BEH-01]",
        "depends_on: []",
        "parallel_group:",
    ):
        assert marker in prompt, marker


def test_unknown_kind_refused() -> None:
    with pytest.raises(ValueError, match="design"):
        nodes.author_node(_run(), "design", launch=FakeLaunch())


def test_revise_node_prompt_has_findings_and_decisions() -> None:
    run = _run()
    call = AuthorCall(
        run_id=run.run_id,
        run_dir=os.path.realpath(state.run_dir(run.run_id)),
        target_dir=run.target_dir,
        bundle_dir=run.bundle_dir,
        filename="20-design.md",
    )
    fake = FakeLaunch()
    outcome = nodes.revise_node(
        call,
        "personal-design",
        "Заметки",
        "ТЕКУЩИЙ ДИЗАЙН",
        "- R1: FR-02 без механизма",
        "- Хранить где? → в файле",
        launch=fake,
    )
    assert outcome.ok
    assert fake.calls == [call]
    prompt = fake.prompts[0]
    for text in (
        "ТЕКУЩИЙ ДИЗАЙН",
        "- R1: FR-02 без механизма",
        "- Хранить где? → в файле",
        "/calls/x/stage/20-design.md",
        f"{run.bundle_dir}/00-brief.md",
    ):
        assert text in prompt
    rules = load_rules("personal-design-vs-brief", DEFAULT_CONTRACTS)
    assert rules.items[0].text in prompt


def test_fixture_decomposition_parses() -> None:
    text = (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8")
    checks = _section(text, "Проверки")
    scenarios = parse_behaviour(checks)
    assert [s.beh_id for s in scenarios] == ["BEH-01", "BEH-02"]
    assert all(s.checked_kind and s.checked_target for s in scenarios)
    tasks, form = parse_dt_tasks(_section(text, "Задачи"))
    assert form == []
    assert [t.dt_id for t in tasks] == ["DT-01", "DT-02"]
    assert graph_findings(checks, text) == []
    cited = {c.strip() for m in _CRITERIA.finditer(checks) for c in m[1].split(",")}
    assert cited == {"FR-01/1", "FR-02/1"}
    brief = (FIXTURES / "00-brief.md").read_text(encoding="utf-8")
    assert "### FR-01:" in brief and "### FR-02:" in brief
    assert (FIXTURES / "20-design.md").read_text(encoding="utf-8").strip()


def test_rules_forbid_declining_fr_and_define_criterion_ref() -> None:
    """Регрессия ревью: дизайн не может отказаться от FR или сузить бриф
    (иначе ребро декомпозиции невыполнимо); FR-NN/k объяснён ревьюеру."""
    design = load_rules("personal-design-vs-brief", DEFAULT_CONTRACTS)
    text = " ".join(it.text for it in design.items)
    assert "с причиной" not in text
    assert "без объявления" not in text
    prompt = (nodes.PROMPTS / "design.md").read_text(encoding="utf-8")
    assert "сознательно не делаешь" not in prompt
    decomposition = load_rules(
        "personal-decomposition-vs-design-brief", DEFAULT_CONTRACTS
    )
    texts = [it.text for it in decomposition.items]
    assert "k — номер пункта" in texts[0]
    assert any("только на существующие FR-NN/k" in t for t in texts)
    assert any("не вводит обязательств" in t for t in texts)


def test_design_decisions_visible_to_node_guard() -> None:
    """Регрессия ревью #774: id решений дизайна — `#### D-NN`, их видит
    `node_guard` — удалённое решение переписыванием = ослабление."""
    from lite import node_guard

    text = (FIXTURES / "20-design.md").read_text(encoding="utf-8")
    assert list(node_guard.node_items(text)) == ["D-01", "D-02"]
    cut = text.split("#### D-02")[0]
    assert node_guard.weakened(text, cut, set(), set()) == ["D-02"]
    assert node_guard.weakened(text, cut, {"D-02"}, set()) == []
    prompt = (nodes.PROMPTS / "design.md").read_text(encoding="utf-8")
    assert "`#### D-NN: <решение>`" in prompt
    assert "### D-NN" not in prompt.replace("#### D-NN", "")
