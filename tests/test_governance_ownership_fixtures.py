"""Паритет владения токеном со spec-runner по общим фикстурам (devtools#491).

Производитель — spec-runner (`tests/fixtures/criteria-closure/v1/ownership/`,
дизайн #603 §6.3); здесь вендоренная копия под своими `PIN` и `manifest.json`.
Обе стороны гоняют свой парсер по каждому `<case>.py` и обязаны получить
`<case>.expected.json`; расхождение — красный CI у разошедшегося.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import criteria_contract as cc
from governance import criteria_tokens as ct

FIXTURES = cc.CONTRACT_DIR / "fixtures" / "ownership"
UPSTREAM_PATH = "tests/fixtures/criteria-closure/v1/ownership"
CASES = sorted(FIXTURES.glob("*.py"))


def test_vendored_copy_is_intact():
    assert (FIXTURES / "PIN").exists()
    assert cc.integrity_findings(FIXTURES) == []


def test_fixtures_have_their_own_pin_independent_of_the_schema_contract():
    """PIN фикстур лежит в своём каталоге, отдельно от корневого PIN схем
    (с B2a схемы v1 вендорены, `vendored()` — True; оракул всё равно
    недоступен — его держит `MIN_SPEC_RUNNER_VERSION=pending`, B2b, проверено
    в test_governance_criteria_contract.py)."""
    assert (FIXTURES / "PIN").exists()
    assert (cc.CONTRACT_DIR / "PIN").exists()
    assert cc.vendored() is True


def test_all_upstream_cases_are_present():
    assert len(CASES) == 21
    for case in CASES:
        assert case.with_suffix(".expected.json").exists(), case.name


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_owned_definitions_match_expected(case: Path):
    # байты — это кейс: CRLF, одиночный CR, BOM, NUL не нормализуются
    source = case.read_bytes().decode("utf-8")
    expected = json.loads(case.with_suffix(".expected.json").read_text())
    if "error" in expected:
        assert expected["error"] == "syntax"
        with pytest.raises((SyntaxError, ValueError)):
            ct.owned_definitions(source)
        return
    got = [
        {"qualname": d.qualname, "line": d.line, "tokens": list(d.tokens)}
        for d in ct.owned_definitions(source)
    ]
    assert got == expected["owned"]


def test_drift_against_upstream_checkout():
    upstream = Path(__file__).resolve().parents[1].parent / "spec-runner"
    errors, notes = cc.drift_findings(
        FIXTURES,
        upstream if upstream.exists() else None,
        ci=False,
        upstream_path=UPSTREAM_PATH,
    )
    assert errors == [], errors
    if notes:
        pytest.skip(notes[0])


def test_fixtures_stay_out_of_the_selfcheck_corpus():
    """Байты фикстур заведомо не парсятся (NUL, синтаксис): в корпусе
    самодиагностики они роняли бы пробы ast-dup, cli-overlap, usage-graph."""
    import tomllib

    from selfcheck.corpus import list_corpus

    root = cc.CONTRACT_DIR.parents[2]
    config = tomllib.loads((root / "selfcheck.toml").read_text(encoding="utf-8"))
    corpus = list_corpus(root, config["corpus"]["exclude"])
    assert not [
        p for p in corpus if p.startswith("contracts/criteria-closure/v1/fixtures/")
    ]
