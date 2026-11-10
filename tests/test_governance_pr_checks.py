"""Предикат «проверок нет» (`pr_checks.no_checks`)."""

from __future__ import annotations

from governance import pr_checks


def test_no_checks_predicate() -> None:
    assert pr_checks.no_checks({}, []) is True
    assert pr_checks.no_checks({"statusCheckRollup": []}, []) is True
    assert pr_checks.no_checks({}, None) is False
    assert pr_checks.no_checks({}, ["ci.yml"]) is False
    queued = {"statusCheckRollup": [{"name": "x", "status": "QUEUED"}]}
    assert pr_checks.no_checks(queued, []) is False
