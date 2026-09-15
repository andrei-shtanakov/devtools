from __future__ import annotations

import json
import shlex

import pytest
import yaml

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


def _spec(**over):
    base = dict(frame="customer", stakeholder_role="product owner",
                target="owner/alpha", traces_to=None, upstream_blob=None)
    base.update(over)
    return interview.InterviewSpec(**base)


def _brief(frame="customer", target="owner/alpha", roles=(), traces=()):
    meta = {"interview": {"frame": frame,
                          "sessions": [{"participant_role": r} for r in roles]},
            "traces_to": list(traces), "status": "draft"}
    return (
        "---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n"
        + interview.h1_line(target, frame) + "\n\n## Goals\n"
    )


def test_answer_command_is_shell_safe() -> None:
    cmd = interview.answer_command("s-1", "product owner", "my answer.yaml")
    assert cmd.startswith("discovery answer --session ")
    assert shlex.split(cmd)[shlex.split(cmd).index("--role") + 1] == "product owner"
    assert "--file" in cmd and "my answer.yaml" in shlex.split(cmd)


def test_supersede_template_keeps_question_placeholder() -> None:
    cmd = interview.supersede_template("s-1", "qa")
    assert "--question <QUESTION_ID>" in cmd and "--supersede" in cmd


def test_attach_accepts_empty_or_exact_role_set() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(), spec) == []
    assert interview.attach_findings(_brief(roles=("product owner",)), spec) == []
    # тот же участник дважды в списке недопустим по построению рендера, но
    # множество ролей ⊆ {stakeholder} — критерий по контракту, не по рендеру
    assert interview.attach_findings(
        _brief(roles=("product owner", "product owner")), spec
    ) == []


def test_attach_rejects_foreign_or_extra_role() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(roles=("qa",)), spec)
    assert interview.attach_findings(_brief(roles=("product owner", "qa")), spec)


def test_attach_rejects_h1_frame_and_traces_mismatch() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(target="owner/beta"), spec)
    assert interview.attach_findings(_brief(frame="engineer"), spec)
    # customer: путевой элемент traces_to — расхождение (как у E1)
    assert interview.attach_findings(_brief(traces=("up.md",)), spec)
    # KB-ссылки для customer законны
    assert interview.attach_findings(_brief(traces=("[[kb-note]]",)), spec) == []


def test_brief_coordinates_ignore_roles() -> None:
    spec = _spec()
    assert interview.brief_coordinate_findings(_brief(roles=("qa",)), spec) == []
    assert interview.brief_coordinate_findings(_brief(target="owner/beta"), spec)


def test_engineer_traces_must_equal_recorded_ref() -> None:
    spec = _spec(frame="engineer", traces_to="customer.md")
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=("customer.md",)), spec) == []
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=("other.md",)), spec)
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=()), spec)


def test_as_state_carries_pins_and_no_session() -> None:
    st = _spec().as_state()
    assert st["session_id"] is None and st["completed_at"] is None
    assert st["brief_rel"] == "brief-input/00-discovery/brief.md"
    assert st["stakeholder_role"] == "product owner"
