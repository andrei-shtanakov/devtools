"""Паритет владения токеном с spec-runner: общие фикстуры criteria-closure/v1.

Вендоренная копия `tests/fixtures/criteria-closure/v1/ownership/` spec-runner
(PIN, manifest.json; дизайн spec-runner#603 §6.3, devtools#491). Наш парсер на
каждом `<case>.py` обязан дать `owned` из `<case>.expected.json`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from governance import criteria_contract as cc
from governance import criteria_tokens as ct

ROOT = cc.CONTRACT_DIR / "fixtures" / "ownership"
CASES = sorted(ROOT.glob("*.py"))


def test_the_agreed_case_set_is_present():
    assert len(CASES) == 21
    assert [c.stem[:2] for c in CASES] == [f"{n:02d}" for n in range(1, 22)]


def test_vendored_copy_is_intact():
    assert (ROOT / "PIN").read_text().startswith("SOURCE: spec-runner @ ")
    assert cc.integrity_findings(ROOT) == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.stem)
def test_owned_matches_expected(case: Path):
    expected = json.loads(
        case.with_suffix(".expected.json").read_text(encoding="utf-8")
    )
    assert expected["case"] == case.stem
    source = case.read_bytes().decode("utf-8")
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


@pytest.mark.parametrize(
    ("stem", "check"),
    [
        ("01_form_feed", lambda b: b"\x0c" in b),
        ("05_bom", lambda b: b.startswith(b"\xef\xbb\xbf")),
        ("14_crlf", lambda b: b.count(b"\r\n") == 6 and b.count(b"\n") == 6),
        ("15_lone_cr", lambda b: b"\n" not in b and b.count(b"\r") == 6),
        ("17_nul", lambda b: b"\x00" in b),
    ],
)
def test_special_bytes_survive(stem, check):
    assert check((ROOT / f"{stem}.py").read_bytes()), stem


def test_git_does_not_normalise_them():
    out = subprocess.run(
        ["git", "check-attr", "text", "--", str(ROOT / "14_crlf.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    assert out.strip().endswith(": text: unset"), out
