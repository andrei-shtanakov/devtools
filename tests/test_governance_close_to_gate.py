"""criteria_close → closure_gate, сквозной шов (Minor 5, финальное ревью 2a).

Пер-модульные тесты собирают предложение сами через `ca.proposal_text`, а не
через `criteria_close`, так что дрейф пути/`accepted_pr`/формы коммита в
`criteria_close` не был бы пойман ни одним тестом. Этот тест гоняет
`criteria_close.run` по-настоящему (реальный git, фиктивная форджа) и кормит
результат в `closure_gate.gate_findings` с форджей на тех же PR-фактах —
ровно шов, который проверяет `acceptance_provenance.stamp_findings`. Реальный
`gh` нигде не вызывается — `_ops`/`GateForge` читают локальный git и
фиктивные записи `ops.forge_prs`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from governance import closure_gate as cg
from governance import criteria_close as cc
from governance import policy_rule
from tests.test_governance_criteria_close import (
    SNAP,
    _env,
    _env_human,
    _forge_merge,
    _git,
    _land,
    _ops,
    _oracle_on,
    _proposal_pr,
    _response,
)


class GateForge:
    """Форджа гейта поверх тех же фактов, что уже собрала `_ops`/`_forge_merge`."""

    def __init__(self, ops, origin: Path) -> None:
        self.ops, self.origin = ops, origin

    def pr_facts(self, slug, pr):
        return dict(self.ops.forge_prs[pr]["facts"])

    def pr_files(self, slug, pr):
        facts = self.ops.forge_prs[pr]["facts"]
        merge_oid = facts["mergeCommit"]["oid"]
        out = subprocess.run(
            [
                "git",
                "-C",
                str(self.origin),
                "diff",
                "--name-only",
                f"{merge_oid}^1",
                facts["headRefOid"],
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.split()

    def default_branch(self, slug):
        return "master"

    def policy_file(self, repo, sha, path):
        return "AUTHORIZED_APPROVER_ACCOUNTS=owner-human\n"

    def policy_on_ref(self, repo, sha, ref):
        return True


def _gate(target, ops, monkeypatch):
    monkeypatch.setattr(
        policy_rule, "policy_source", lambda: (SNAP.repo, "main", SNAP.path)
    )
    _land(target)
    (target / "TODO.md").write_text("# T\n\n- [x] done @id:oracle\n")
    origin = Path(_git(target, "remote", "get-url", "origin"))
    return cg.gate_findings(target, slug="owner/alpha", forge=GateForge(ops, origin))


@pytest.mark.parametrize("human", [False, True])
def test_e2e_accepted_is_gate_green(tmp_path, monkeypatch, human):
    """Test-only путь (агент мержит сам) и человеческий путь (человек
    мержит предложение, агент штампует) — оба дают зелёный гейт."""
    _state, target, pin = (_env_human if human else _env)(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    if human:
        assert cc.run("run-1", ops) == 4
        _forge_merge(ops, _proposal_pr(ops), "owner-human")
    assert cc.run("run-1", ops) == 0
    errors, _warns = _gate(target, ops, monkeypatch)
    assert errors == [], errors


def test_e2e_human_criteria_proposal_only_is_red(tmp_path, monkeypatch):
    """Человеческие критерии, предложение ещё не влито в default — гейт
    красный: приёмка не началась, штампа нет."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    errors, _warns = _gate(target, ops, monkeypatch)
    assert errors, "нет закрытия в default -> гейт должен быть красным"
