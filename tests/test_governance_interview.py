from __future__ import annotations

import json

import pytest

from governance import interview


def _envelope(**over):
    base = {
        "lifecycle": "awaiting_input", "gate": "unknown", "readiness": "unknown",
        "next_action": {"session_id": "s-1", "question_id": "Q-01",
                        "coverage_key": "goals", "question_text": "?"},
        "findings": [], "readiness_findings": [],
        "operation": {"status": "ok", "reason": ""},
    }
    base.update(over)
    return base


def test_valid_contract_keeps_process_returncode() -> None:
    reply = interview.parse_reply(20, json.dumps(_envelope()), "")
    assert reply.code == 20
    assert reply.envelope["next_action"]["session_id"] == "s-1"


@pytest.mark.parametrize("stdout", ["", "not json", "[1,2]", "42"])
def test_unparsable_or_non_object_stdout_is_synthetic_one(stdout: str) -> None:
    reply = interview.parse_reply(0, stdout, "boom")
    assert reply.code == 1
    assert reply.envelope["operation"]["status"] == "unknown"
    assert reply.envelope["operation"]["reason"].startswith(
        interview.SYNTHETIC_REASON_PREFIX
    )
    assert reply.envelope["lifecycle"] == "unknown"
    assert reply.stderr == "boom"


def test_missing_required_field_is_synthetic_one() -> None:
    env = _envelope()
    del env["readiness_findings"]
    reply = interview.parse_reply(20, json.dumps(env), "")
    assert reply.code == 1
    assert "readiness_findings" in reply.envelope["operation"]["reason"]


def test_unknown_returncode_is_synthetic_one() -> None:
    reply = interview.parse_reply(7, json.dumps(_envelope()), "")
    assert reply.code == 1
    assert "7" in reply.envelope["operation"]["reason"]


def test_code_20_without_session_or_question_is_synthetic_one() -> None:
    env = _envelope(next_action={"question_id": "Q-01"})
    reply = interview.parse_reply(20, json.dumps(env), "")
    assert reply.code == 1
    assert "session_id" in reply.envelope["operation"]["reason"]


def test_synthetic_envelope_has_protocol_shape() -> None:
    reply = interview.parse_reply(0, "", "")
    assert set(reply.envelope) == set(interview.REQUIRED_FIELDS)
