"""GraphQL-факты политики подписи: отсутствие отличимо от недоступности.

Спека approval-policy §4.1: версия — последний коммит ветки по пути файла
(REST не даёт истории по пути с отличимым «ветки нет»), содержимое — по
`object(oid:)` + `file(path:)`, где `object: null` и `file: null` — два
разных положительных отсутствия, а `text: null`/бинарный/усечённый —
неустановленный факт.
"""

from __future__ import annotations

import json
import subprocess

from governance.facts import Outcome
from governance.ops import RealOps

REPO, BRANCH, PATH, SHA = "o/policy", "main", "policy/approvers.env", "a" * 40


def _gh(monkeypatch, payload: dict | None, rc: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        out = json.dumps(payload) if payload is not None else ""
        return subprocess.CompletedProcess(
            argv, rc, stdout=out, stderr="boom" if rc else ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_version_found_is_last_commit_touching_the_path(monkeypatch) -> None:
    calls = _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {
        "history": {"nodes": [{"oid": SHA}]}}}}}})
    fact = RealOps().policy_version_fact(REPO, BRANCH, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == SHA
    assert "-F" in calls[0] and f"p={PATH}" in calls[0], "история — ПО ПУТИ"
    assert "q=refs/heads/main" in calls[0]


def test_version_absent_when_branch_missing_or_history_empty(monkeypatch) -> None:
    _gh(monkeypatch, {"data": {"repository": {"ref": None}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {
        "history": {"nodes": []}}}}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT


def test_version_unavailable_on_rc_or_odd_shape(monkeypatch) -> None:
    _gh(monkeypatch, None, rc=1)
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, {"data": {"repository": None}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {}}}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE


def test_file_found_absent_unavailable(monkeypatch) -> None:
    calls = _gh(monkeypatch, {"data": {"repository": {"object": {"file": {"object": {
        "text": "K=v\n", "isBinary": False, "isTruncated": False}}}}}})
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == "K=v\n"
    assert f"s={SHA}" in calls[0]
    _gh(monkeypatch, {"data": {"repository": {"object": None}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": None}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": {"object": {
        "text": None, "isBinary": True, "isTruncated": False}}}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, None, rc=1)
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
