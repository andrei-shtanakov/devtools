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

import pytest

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
    calls = _gh(
        monkeypatch,
        {
            "data": {
                "repository": {
                    "ref": {"target": {"history": {"nodes": [{"oid": SHA}]}}}
                }
            }
        },
    )
    fact = RealOps().policy_version_fact(REPO, BRANCH, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == SHA
    assert "-F" in calls[0] and f"p={PATH}" in calls[0], "история — ПО ПУТИ"
    assert "q=refs/heads/main" in calls[0]


def test_version_absent_when_branch_missing_or_history_empty(monkeypatch) -> None:
    _gh(monkeypatch, {"data": {"repository": {"ref": None}}})
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT
    _gh(
        monkeypatch,
        {"data": {"repository": {"ref": {"target": {"history": {"nodes": []}}}}}},
    )
    assert RealOps().policy_version_fact(REPO, BRANCH, PATH).outcome is Outcome.ABSENT


def test_version_unavailable_on_rc_or_odd_shape(monkeypatch) -> None:
    _gh(monkeypatch, None, rc=1)
    version = RealOps().policy_version_fact
    assert version(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, {"data": {"repository": None}})
    assert version(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, {"data": {"repository": {"ref": {"target": {}}}}})
    assert version(REPO, BRANCH, PATH).outcome is Outcome.UNAVAILABLE


def test_file_found_absent_unavailable(monkeypatch) -> None:
    calls = _gh(
        monkeypatch,
        {
            "data": {
                "repository": {
                    "object": {
                        "file": {
                            "object": {
                                "text": "K=v\n",
                                "isBinary": False,
                                "isTruncated": False,
                            }
                        }
                    }
                }
            }
        },
    )
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    assert fact.outcome is Outcome.FOUND and fact.value == "K=v\n"
    assert f"s={SHA}" in calls[0]
    _gh(monkeypatch, {"data": {"repository": {"object": None}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(monkeypatch, {"data": {"repository": {"object": {"file": None}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.ABSENT
    _gh(
        monkeypatch,
        {
            "data": {
                "repository": {
                    "object": {
                        "file": {
                            "object": {
                                "text": None,
                                "isBinary": True,
                                "isTruncated": False,
                            }
                        }
                    }
                }
            }
        },
    )
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
    _gh(monkeypatch, None, rc=1)
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE


def test_file_field_missing_is_unavailable_not_absent(monkeypatch) -> None:
    """Неполный ответ (`object: {}`) — UNAVAILABLE; ABSENT — только явный null."""
    _gh(monkeypatch, {"data": {"repository": {"object": {}}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE


_MISSING = object()
_FORMS = {
    "text": [_MISSING, None, 1, ["x"], "K=v\n"],
    "isBinary": [_MISSING, None, 0, "false", True, False],
    "isTruncated": [_MISSING, None, 0, "false", True, False],
}


def _blob(text, is_binary, is_truncated) -> dict:
    blob = {}
    for key, value in (
        ("text", text),
        ("isBinary", is_binary),
        ("isTruncated", is_truncated),
    ):
        if value is not _MISSING:
            blob[key] = value
    return {"data": {"repository": {"object": {"file": {"object": blob}}}}}


@pytest.mark.parametrize("text", _FORMS["text"])
@pytest.mark.parametrize("is_binary", _FORMS["isBinary"])
@pytest.mark.parametrize("is_truncated", _FORMS["isTruncated"])
def test_file_fact_found_only_for_complete_text(
    monkeypatch, text, is_binary, is_truncated
) -> None:
    """Перебор форм (ревью плана A8→A10): FOUND — только строковый text при
    явных `isBinary: false` и `isTruncated: false`; всё прочее — UNAVAILABLE."""
    _gh(monkeypatch, _blob(text, is_binary, is_truncated))
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    complete = text == "K=v\n" and is_binary is False and is_truncated is False
    assert fact.outcome is (Outcome.FOUND if complete else Outcome.UNAVAILABLE)


@pytest.mark.parametrize(
    "obj",
    [
        {"file": {}},
        {"file": {"object": None}},
        {"file": "x"},
        {"file": {"object": "x"}},
    ],
)
def test_file_fact_malformed_entry_is_unavailable(monkeypatch, obj) -> None:
    _gh(monkeypatch, {"data": {"repository": {"object": obj}}})
    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE


#: Живой ответ GitHub на отсутствующий путь (замер 2026-10-10, приёмка E2):
#: `data` с `file: null` И `errors` NOT_FOUND по этому месту; `gh` — код 1.
_LIVE_NOT_FOUND = {
    "data": {"repository": {"object": {"file": None}}},
    "errors": [
        {
            "type": "NOT_FOUND",
            "path": ["repository", "object", "file"],
            "locations": [{"line": 1, "column": 115}],
            "message": "Could not resolve file for path 'x/brief.md'.",
        }
    ],
}


def test_live_not_found_answer_is_absent(monkeypatch) -> None:
    """Живая приёмка E2: без этого «файла нет» становилось «повторите», и
    brief-propose не мог проверить базу (адаптер отбрасывал ответ с кодом 1)."""
    _gh(monkeypatch, _LIVE_NOT_FOUND, rc=1)
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    assert fact.outcome is Outcome.ABSENT, fact


def _variant(**changes):
    import copy

    payload = copy.deepcopy(_LIVE_NOT_FOUND)
    if "error" in changes:
        payload["errors"][0].update(changes.pop("error"))
    if changes.pop("extra_error", False):
        payload["errors"].append({"type": "FORBIDDEN", "path": ["repository"]})
    if "file" in changes:
        payload["data"]["repository"]["object"]["file"] = changes.pop("file")
    if changes.pop("no_data", False):
        del payload["data"]
    if changes.pop("no_errors", False):
        del payload["errors"]
    assert not changes
    return payload


@pytest.mark.parametrize(
    ("payload", "rc"),
    [
        (_variant(error={"type": "FORBIDDEN"}), 1),  # не NOT_FOUND
        (_variant(error={"path": ["repository", "object"]}), 1),  # не то место
        (_variant(extra_error=True), 1),  # NOT_FOUND + иная ошибка
        (_variant(file={"object": {"text": "x"}}), 1),  # значение там не null
        (_variant(no_data=True), 1),  # нет data
        (_variant(no_errors=True), 1),  # код 1 без объяснения
        (_variant(file={"object": {"text": "x"}}), 0),  # код 0, но errors
    ],
    ids=[
        "other-type",
        "other-path",
        "mixed-errors",
        "not-null",
        "no-data",
        "rc1-no-errors",
        "rc0-with-errors",
    ],
)
def test_not_found_twins_are_unavailable(monkeypatch, payload, rc) -> None:
    """Принимается ТОЛЬКО форма живого NOT_FOUND; всякая иная — UNAVAILABLE."""
    _gh(monkeypatch, payload, rc=rc)
    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
    assert fact.outcome is Outcome.UNAVAILABLE, fact
