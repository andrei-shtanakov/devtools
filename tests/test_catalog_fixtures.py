"""catalog-conformance-fixtures v1 — owner-side QA of the shipped set.

Proves both directions: the shipped fixtures satisfy their expectations
(check-catalog-fixtures.py --check is green), and the checker is not
vacuously satisfiable — a wrong expectation, a tampered copy or a stale
manifest each turn it red. The negative direction is readiness condition 3
of devtools#43 on the owner side: every negative case has a check that
would fail if the case were accepted.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "check-catalog-fixtures.py"
_CONTRACT = _REPO / "contracts" / "catalog-conformance-fixtures" / "v1"


@pytest.fixture(scope="session")
def checker():
    spec = importlib.util.spec_from_file_location("catalog_fixtures_checker", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить модуль чекера из {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["catalog_fixtures_checker"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def contract_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "v1"
    shutil.copytree(_CONTRACT, copy)
    return copy


def _cases() -> list[dict]:
    doc = tomllib.loads((_CONTRACT / "expectations.toml").read_text())
    return doc["case"]


def test_shipped_set_is_green(checker) -> None:
    assert checker.check_all(_CONTRACT) == []


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["file"])
def test_each_case_holds_individually(checker, case: dict) -> None:
    assert checker.check_case(_CONTRACT, case) is None


@pytest.mark.parametrize(
    "case",
    [c for c in _cases() if c["expect"] in ("error", "flag", "parse-error")],
    ids=lambda c: c["file"],
)
def test_negative_fixtures_are_not_acceptable_as_valid(checker, case: dict) -> None:
    """A loader that accepted this fixture as healthy would diverge."""
    flipped = {"file": case["file"], "expect": "valid"}
    assert checker.check_case(_CONTRACT, flipped) is not None


def test_valid_fixture_fails_error_expectation(checker) -> None:
    wrong = {
        "file": "fixtures/valid/three-planes.toml",
        "expect": "error",
        "code": "V1",
    }
    assert checker.check_case(_CONTRACT, wrong) is not None


def test_validate_catalog_collects_all_rules(checker) -> None:
    catalog = {
        "models": {
            "m-active": {"vendor": "acme", "status": "active"},
            "m-retired": {"vendor": "acme", "status": "retired"},
            "m-deprecated": {"vendor": "acme", "status": "deprecated"},
            "m-weird": {"vendor": "acme", "status": "preview"},
        },
        "harnesses": {
            "h_ok": {"kind": "cli", "routable": True},
            "h_pinned": {"kind": "cli", "routable": False},
            "h_weird": {"kind": "container", "routable": False},
        },
        "agents": [
            {"harness": "ghost", "model": "m-active"},  # V1
            {"harness": "h_ok", "model": "ghost"},  # V2
            {"harness": "h_ok", "model": "m-retired"},  # V3
            {"harness": "h_ok", "model": "m-active"},
            {"harness": "h_ok", "model": "m-active"},  # V4 (duplicate)
            {"harness": "h_pinned", "model": "m-active", "routable": True},  # V5
            {"harness": "h_ok", "model": "m-deprecated"},  # V6
        ],
    }
    issues = checker.validate_catalog(catalog)
    errors = sorted(i.code for i in issues if i.severity == "error")
    warnings = sorted(i.code for i in issues if i.severity == "warning")
    assert errors == ["V1", "V2", "V3", "V4", "V5"]
    assert warnings == ["V6", "V7", "V7"]  # unknown status + unknown kind


def test_misshapen_catalog_is_a_case_problem_not_a_crash(
    checker, contract_copy: Path
) -> None:
    """Parseable TOML with a non-contract shape fails the case, no traceback."""
    fixture = contract_copy / "fixtures" / "valid" / "misshapen.toml"
    fixture.write_text("models = 3\n[[agents]]\nrouted = true\n")
    case = {"file": "fixtures/valid/misshapen.toml", "expect": "valid"}
    problem = checker.check_case(contract_copy, case)
    assert problem is not None and "shape not validatable" in problem


def test_check_and_write_manifest_are_mutually_exclusive(
    checker, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["check-catalog-fixtures.py", "--check", "--write-manifest"]
    )
    with pytest.raises(SystemExit) as excinfo:
        checker.main()
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_stale_manifest_is_detected(checker, contract_copy: Path) -> None:
    fixture = contract_copy / "fixtures" / "valid" / "three-planes.toml"
    fixture.write_text(fixture.read_text() + "\n# tampered\n")
    problems = checker.check_all(contract_copy)
    assert any("manifest.json stale" in p for p in problems)


def test_uncovered_fixture_is_detected(checker, contract_copy: Path) -> None:
    stray = contract_copy / "fixtures" / "invalid" / "stray.toml"
    stray.write_text('[models."x"]\nvendor = "acme"\n')
    problems = checker.check_all(contract_copy)
    assert any("stray.toml" in p and "no [[case]]" in p for p in problems)


def test_missing_fixture_is_detected(checker, contract_copy: Path) -> None:
    (contract_copy / "fixtures" / "warn" / "v6-deprecated-ref.toml").unlink()
    problems = checker.check_all(contract_copy)
    assert any("non-existent fixture" in p for p in problems)


def test_tampered_pathres_is_detected(checker, contract_copy: Path) -> None:
    exp = contract_copy / "expectations.toml"
    text = exp.read_text().replace('expect = "missing-file-error"', 'expect = "loaded"')
    exp.write_text(text)
    problems = checker.check_all(contract_copy)
    assert any("must expect 'missing-file-error'" in p for p in problems)


def test_manifest_roundtrip_is_deterministic(checker, contract_copy: Path) -> None:
    first = checker.compute_manifest(contract_copy)
    second = checker.compute_manifest(contract_copy)
    assert first == second
    assert first["tree_sha256"]
    assert all(len(e["sha256"]) == 64 for e in first["files"])
    paths = [e["path"] for e in first["files"]]
    assert "manifest.json" not in paths
    assert paths == sorted(paths)
