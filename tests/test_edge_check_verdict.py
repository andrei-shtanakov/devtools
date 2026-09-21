from __future__ import annotations

import json
from pathlib import Path

from governance.edge_check import check as c
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01 traces FR-01\n", encoding="utf-8")
    return b


def _answer(rs: r.RuleSet, findings: list[dict]) -> str:
    return json.dumps(
        {
            "structured_output": {
                "criteria": [
                    {"id": it.id, "status": "pass", "reason": "ок"} for it in rs.items
                ],
                "findings": findings,
            }
        }
    )


def _run(tmp_path: Path, findings: list[dict]) -> dict:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    return c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=lambda prompt: _answer(rs, findings),
    )


def test_blocking_finding_gives_fail(tmp_path: Path) -> None:
    out = _run(tmp_path, [{"rule_id": "R2", "class": "major",
                           "path": "15-behaviour-spec.md", "lines": [1, 1],
                           "statement": "вводит обязательство сверх требований"}])
    assert out["verdict"] == "FAIL"


def test_advisory_only_gives_pass_and_keeps_findings(tmp_path: Path) -> None:
    out = _run(tmp_path, [{"rule_id": "R4", "class": "minor",
                           "path": "15-behaviour-spec.md", "lines": [1, 1],
                           "statement": "формулировка двусмысленна"}])
    assert out["verdict"] == "PASS"
    assert len(out["findings"]) == 1


def test_result_carries_computed_hashes_and_both_identities(tmp_path: Path) -> None:
    out = _run(tmp_path, [])
    assert out["schema_version"] == 1
    assert {f["role"] for f in out["subject"]} == {"subject"}
    assert out["bases"][0]["role"] == "requirements"
    assert len(out["subject"][0]["sha256"]) == 64
    assert len(out["check_identity"]) == 64
    assert out["context"]["method"] == "utf8-bytes/4"
    assert out["reviewer"]["model"] == "claude-opus-5"
    assert out["attempt_id"] and out["started_at"] and out["finished_at"]


def test_reviewer_failure_becomes_error_with_code(tmp_path: Path) -> None:
    b = _bundle(tmp_path)

    def boom(prompt: str) -> str:
        raise r.EdgeCheckError("timeout", "ревьюер не ответил за 600 с")

    out = c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=boom,
    )
    assert out["verdict"] == "ERROR"
    assert out["error_code"] == "timeout"
    assert out["reason"]


def test_absent_optional_basis_gives_na_without_calling_the_model(
    tmp_path: Path,
) -> None:
    b = _bundle(tmp_path)
    disco = b / "00-discovery"
    disco.mkdir()
    (disco / "brief.md").write_text("IF-01 интерфейс\n", encoding="utf-8")
    called: list[str] = []

    out = c.run_check(
        "engineer-brief-vs-customer-brief",
        b,
        [disco / "brief.md"],
        [("customer-brief", disco / "discovery-brief-customer.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=lambda prompt: called.append(prompt) or "{}",
    )
    assert out["verdict"] == "N/A"
    assert out["absence"][0]["rule_id"] == "A1"
    assert called == [], "при N/A модель не зовётся"


def test_call_exception_other_than_edge_check_error_becomes_reviewer_failed(
    tmp_path: Path,
) -> None:
    """Инъекция `call` — публичный параметр; контракт `run_check` не должен
    зависеть от того, что именно она бросает."""
    b = _bundle(tmp_path)

    def boom(prompt: str) -> str:
        raise RuntimeError("сеть недоступна")

    out = c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=boom,
    )
    assert out["verdict"] == "ERROR"
    assert out["error_code"] == "reviewer_failed"
    assert "RuntimeError" in out["reason"]


def test_parse_failure_keeps_specific_edge_check_error_code(tmp_path: Path) -> None:
    """Задача 5 закрыла пути к «дикому» исключению из `parse_response`: любой
    отказ там уже приходит как `EdgeCheckError` со своим кодом. Проверяем,
    что широкая ловушка в `run_check` не подменяет этот код на общий."""
    b = _bundle(tmp_path)
    out = c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=lambda prompt: "не json",
    )
    assert out["verdict"] == "ERROR"
    assert out["error_code"] == "invalid_response"
