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


# === Critical 1: сырые исключения наружу (4 теста) ===


def test_criteria_not_list_raw_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = _envelope({"id": "R1"}, [])  # criteria — словарь, не список
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(bad, rs, _prepared())
    assert exc.value.code == "invalid_response"


def test_findings_not_list_raw_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = _envelope(_all_pass(rs), {"rule_id": "R1"})  # findings — словарь
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(bad, rs, _prepared())
    assert exc.value.code == "invalid_response"


def test_criterion_not_dict_raw_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = _envelope(["not a dict"], [])  # элемент критерия — строка
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(bad, rs, _prepared())
    assert exc.value.code == "invalid_response"


def test_lines_not_int_raw_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "major", "path": "15-behaviour-spec.md",
            "lines": [1, "a"], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_response"


# === Critical 2: офф-бай-уан в подсчёте строк (2 теста) ===


def test_final_newline_line_counting() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    # text с финальным \n содержит ровно 3 строки, [1, 4] — вне файла
    text = "BEH-01\nBEH-02\nBEH-03\n"
    f = i.InputFile("subject", "15-behaviour-spec.md", "aa", len(text), text)
    prep = i.PreparedInput((f,), (), True)
    bad = [{"rule_id": "R1", "class": "major", "path": "15-behaviour-spec.md",
            "lines": [1, 4], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, prep)
    assert exc.value.code == "invalid_line_range"


def test_no_final_newline_line_counting() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    # text БЕЗ финального \n: [1, 3] должен пройти
    text = "BEH-01\nBEH-02\nBEH-03"
    f = i.InputFile("subject", "15-behaviour-spec.md", "aa", len(text), text)
    prep = i.PreparedInput((f,), (), True)
    good = [{"rule_id": "R1", "class": "major", "path": "15-behaviour-spec.md",
             "lines": [1, 3], "statement": "x"}]
    out = resp.parse_response(
        _envelope(_all_pass(rs), good), rs, prep
    )
    assert len(out.findings) == 1


# === Important: лишние и дубли пунктов (2 теста) ===


def test_unknown_criterion_id_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = _all_pass(rs) + [{"id": "ZZZ-NOTREAL", "status": "pass",
                            "reason": "ок"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(bad, []), rs, _prepared())
    assert exc.value.code == "criteria_malformed"


def test_duplicate_criterion_id_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    dup = _all_pass(rs)
    dup.append(dup[0])  # добавляем дубль первого
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(dup, []), rs, _prepared())
    assert exc.value.code == "criteria_malformed"


# === Minor: rule_id находки не сверяется (1 тест) ===


def test_invalid_finding_rule_id() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "UNKNOWN-RULE", "class": "major",
            "path": "15-behaviour-spec.md", "lines": [1, 1],
            "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_finding_rule"
