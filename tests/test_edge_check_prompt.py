from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import prompt as p
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _prepared(text: str = "BEH-01\n") -> i.PreparedInput:
    f = i.InputFile("subject", "15-behaviour-spec.md", "deadbeef", len(text), text)
    g = i.InputFile("requirements", "10-requirements.md", "cafe", 4, "FR-01\n")
    return i.PreparedInput((f, g), (), True)


def test_prompt_carries_every_rule_and_every_input() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    out = p.build_prompt(rs, _prepared())
    for item in rs.items:
        assert item.id in out.text
    assert "15-behaviour-spec.md" in out.text
    assert "BEH-01" in out.text
    assert "FR-01" in out.text


def test_measure_covers_instruction_rules_and_documents() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    small = p.build_prompt(rs, _prepared("BEH-01\n"))
    big = p.build_prompt(rs, _prepared("BEH-01\n" + "x" * 10_000))
    assert big.measure.size > small.measure.size + 9_000
    assert small.measure.method == "utf8-bytes/4"
    assert small.measure.reserve_tokens > 0


def test_oversized_input_is_error_without_truncation() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        p.build_prompt(rs, _prepared("x" * 200_000), limit_tokens=1000)
    assert exc.value.code == "input_too_large"
    assert "1000" in str(exc.value)
