"""Сортировка замечаний edge-check агентом (lite/edge_triage.py).

Агент подменён заготовленными ответами.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lite.edge_journal import Decision, Finding
from lite.edge_triage import triage
from lite.structure import FormError


def _f(rule: str = "R1", node: str = "spec") -> Finding:
    return Finding(node, "spec->brief", rule, "blocker", f"текст {rule}", None, "1")


def _opt(label: str, rec: bool, **extra: Any) -> dict[str, Any]:
    return {"label": label, "recommended": rec, **extra}


def _agent(*answers: Any) -> Any:
    queue = [a if isinstance(a, str) else json.dumps(a) for a in answers]
    calls: list[str] = []

    def agent(prompt: str) -> str:
        calls.append(prompt)
        return queue.pop(0)

    agent.calls = calls  # type: ignore[attr-defined]
    return agent


def _run(findings: list[Finding], agent: Any, **kw: Any) -> Any:
    prior = kw.pop("prior", [])
    return triage(findings, {"spec.md": "S"}, {"brief.md": "B"}, prior, agent, **kw)


def test_fix_and_decision_parsed() -> None:
    answer = {
        "items": [
            {"finding": 1, "kind": "fix", "question": "", "options": []},
            {
                "finding": 2,
                "kind": "decision",
                "question": "Как быть?",
                "options": [_opt("а", True), _opt("б", False)],
            },
        ]
    }
    items = _run([_f("R1"), _f("R2")], _agent(answer))
    assert [i.kind for i in items] == ["fix", "decision"]
    assert items[1].question == "Как быть?"
    assert [o.recommended for o in items[1].options] == [True, False]
    assert items[0].options == []


@pytest.mark.parametrize(
    "options",
    [
        [_opt("а", True)],
        [_opt("а", True), _opt("б", True)],
        [_opt("а", False), _opt("б", False)],
        [_opt(str(i), i == 0) for i in range(4)],
    ],
)
def test_decision_needs_two_to_three_options_one_recommended(options: Any) -> None:
    item = {"finding": 1, "kind": "decision", "question": "?", "options": options}
    with pytest.raises(FormError):
        _run([_f()], _agent({"items": [item]}))


def test_unknown_finding_index_rejected() -> None:
    item = {"finding": 5, "kind": "fix", "question": "", "options": []}
    with pytest.raises(FormError):
        _run([_f()], _agent({"items": [item]}))


def test_missing_and_duplicate_rejected() -> None:
    fix = {"finding": 1, "kind": "fix", "question": "", "options": []}
    with pytest.raises(FormError):
        _run([_f("R1"), _f("R2")], _agent({"items": [fix]}))
    with pytest.raises(FormError):
        _run([_f("R1")], _agent({"items": [fix, fix]}))


def test_finding_with_prior_decision_not_asked() -> None:
    finding = _f()
    prior = [Decision(finding.key, "?", "убрать FR-03", "option", 1)]
    asked = {
        "items": [
            {
                "finding": 1,
                "kind": "decision",
                "question": "снова?",
                "options": [_opt("а", True), _opt("б", False)],
            }
        ]
    }
    agent = _agent(asked)
    items = _run([finding], agent, prior=prior)
    assert items[0].kind == "fix"
    assert items[0].question == "" and items[0].options == []
    assert "убрать FR-03" in agent.calls[0]


def test_option_effects_parsed() -> None:
    item = {
        "finding": 1,
        "kind": "decision",
        "question": "?",
        "options": [
            _opt("убрать", False, removes=["FR-03"], downgrades=["FR-01"]),
            _opt("оставить", True),
        ],
    }
    first, second = _run([_f()], _agent({"items": [item]}))[0].options
    assert first.removes == ["FR-03"] and first.downgrades == ["FR-01"]
    assert second.removes == [] and second.downgrades == []


def test_prompt_has_rules_and_texts() -> None:
    fix = {"finding": 1, "kind": "fix", "question": "", "options": []}
    agent = _agent({"items": [fix]})
    _run([_f()], agent)
    prompt = agent.calls[0]
    assert "removes" in prompt and "downgrades" in prompt
    assert "утверждённ" in prompt
    assert "текст R1" in prompt and "brief.md" in prompt and "spec.md" in prompt


def test_triage_cached_by_input_hash(tmp_path: Path) -> None:
    fix = {"finding": 1, "kind": "fix", "question": "", "options": []}
    agent = _agent({"items": [fix]})
    first = _run([_f()], agent, cache_dir=tmp_path)
    second = _run([_f()], agent, cache_dir=tmp_path)
    assert len(agent.calls) == 1
    assert first == second
    assert len(list((tmp_path / "edge-triage").glob("*.json"))) == 1
    _run([_f("R9")], _agent({"items": [fix]}), cache_dir=tmp_path)
    assert len(list((tmp_path / "edge-triage").glob("*.json"))) == 2


@pytest.mark.parametrize("broken", ["[1, 2]", '"строка"', None])
def test_broken_cache_falls_back_to_agent(tmp_path: Path, broken: str | None) -> None:
    """Кэш не объект JSON или не UTF-8 — без трассировки, спросить агента."""
    fix = {"finding": 1, "kind": "fix", "question": "", "options": []}
    _run([_f()], _agent({"items": [fix]}), cache_dir=tmp_path)
    [cache] = (tmp_path / "edge-triage").glob("*.json")
    cache.unlink()
    if broken is None:
        cache.write_bytes(b"\xff\xfe")  # не UTF-8 — UnicodeDecodeError
    else:
        cache.write_text(broken, encoding="utf-8")
    agent = _agent({"items": [fix]})
    [item] = _run([_f()], agent, cache_dir=tmp_path)
    assert item.kind == "fix" and len(agent.calls) == 1


def test_unreadable_cache_falls_back_to_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = {"finding": 1, "kind": "fix", "question": "", "options": []}
    _run([_f()], _agent({"items": [fix]}), cache_dir=tmp_path)

    def denied(self: Path, *a: Any, **kw: Any) -> str:
        raise PermissionError("нет доступа")

    monkeypatch.setattr(Path, "read_text", denied)
    agent = _agent({"items": [fix]})
    [item] = _run([_f()], agent, cache_dir=tmp_path)
    assert item.kind == "fix" and len(agent.calls) == 1
