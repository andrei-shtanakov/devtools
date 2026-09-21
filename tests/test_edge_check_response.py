from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import response as resp
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _prepared() -> i.PreparedInput:
    text = "BEH-01\nBEH-02\nBEH-03\n"
    f = i.InputFile("subject", "15-behaviour-spec.md", "aa", len(text), text)
    return i.PreparedInput((f,), (), True)


def _envelope(criteria, findings) -> str:
    return json.dumps(
        {"structured_output": {"criteria": criteria, "findings": findings}}
    )


def _all_pass(rs: r.RuleSet) -> list[dict]:
    return [{"id": it.id, "status": "pass", "reason": "ок"} for it in rs.items]


def test_complete_answer_parses() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    out = resp.parse_response(_envelope(_all_pass(rs), []), rs, _prepared())
    assert [c.id for c in out.criteria] == [it.id for it in rs.items]


def test_missing_criterion_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    partial = _all_pass(rs)[:-1]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(partial, []), rs, _prepared())
    assert exc.value.code == "criteria_incomplete"


def test_finding_outside_input_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "major", "path": "20-design.md",
            "lines": [1, 2], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "finding_outside_input"


def test_line_range_beyond_file_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "major", "path": "15-behaviour-spec.md",
            "lines": [1, 99], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_line_range"


def test_unknown_finding_class_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "catastrophic", "path": "15-behaviour-spec.md",
            "lines": [1, 1], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_finding_class"


def test_non_json_answer_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response("не json", rs, _prepared())
    assert exc.value.code == "invalid_response"
