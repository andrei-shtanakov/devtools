"""Discovery brief intake and portable source-layer layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import brief_input


def customer_brief(*, status: str = "draft", validation: str = "pass") -> str:
    return f"""\
---
spec_stage: discovery
status: {status}
version: 1
generated_by: discovery-agent@test
generated_at: 2026-09-13
validation: {validation}
owner_role: product
schema: discovery-brief
schema_version: 1
feeds: [charter, requirements]
interview:
  frame: customer
  sessions:
    - participant_role: product-owner
coverage:
  goals: covered
  personas: covered
  jobs: covered
  functions: covered
  nfr: covered
  constraints: covered
  success_metrics: covered
  out_of_scope: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: []
---

# Brief

- **G-01** Goal
- **P-01** Persona
- **J-01** `traces: [G-01]` Job

#### FR-01: Feature `traces: [G-01, J-01]`
**Priority**: Must
**Acceptance**: works

#### NFR-01: Safety `traces: [CON-01]`
**Target**: zero writes

- **CON-01** Constraint
- **M-01** `traces: [G-01]` Metric
- **OUT-01** Not in scope
"""


def engineer_brief(ref: str = "customer.md") -> str:
    return f"""\
---
spec_stage: discovery
status: draft
version: 1
generated_by: discovery-agent@test
generated_at: 2026-09-13
validation: pass
owner_role: architect
schema: discovery-brief
schema_version: 1
feeds: [system-assessment, tech-selection]
interview:
  frame: engineer
  sessions:
    - participant_role: platform-engineer
coverage:
  systems: covered
  interfaces: covered
  constraints: covered
  arch_preferences: covered
  risks: covered
  feasibility_review: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: [{ref}]
---

# Engineer brief

- **S-01** System
- **IF-01** `traces: [S-01]` Interface
- **CON-01** Constraint
- **AP-01** `traces: [S-01, CON-01]` Preference
- **RK-01** Risk

## Feasibility

- FR-01 is feasible.
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_customer_pass_and_warning_only_are_accepted(tmp_path: Path) -> None:
    path = _write(tmp_path / "brief.md", customer_brief())

    source = brief_input.inspect_brief(path)

    assert source.frame == "customer"
    assert source.primary_rel == "00-discovery/brief.md"
    assert source.requirements_rel == source.primary_rel
    assert source.source_paths == (source.primary_rel,)


def test_invalid_customer_is_rejected_with_gate_id(tmp_path: Path) -> None:
    path = _write(tmp_path / "brief.md", customer_brief(validation="pending"))

    with pytest.raises(brief_input.BriefInputError, match="GC-15"):
        brief_input.inspect_brief(path)


def test_engineer_resolves_one_approved_customer(tmp_path: Path) -> None:
    customer = _write(
        tmp_path / "sources/customer.md", customer_brief(status="approved")
    )
    engineer = _write(
        tmp_path / "engineer.md", engineer_brief("sources/customer.md")
    )

    source = brief_input.inspect_brief(engineer)

    assert source.frame == "engineer"
    assert source.requirements_input == customer
    assert source.requirements_rel == "00-discovery/sources/customer.md"
    assert source.source_paths == (
        "00-discovery/brief.md",
        "00-discovery/sources/customer.md",
    )
    assert dict(source.source_blobs) == {
        "discovery-brief": brief_input.blob_sha1(engineer.read_text()),
        "discovery-customer": brief_input.blob_sha1(customer.read_text()),
    }


@pytest.mark.parametrize("ref", ["", "/tmp/customer.md", "../customer.md"])
def test_engineer_rejects_nonportable_upstream(tmp_path: Path, ref: str) -> None:
    _write(tmp_path / "customer.md", customer_brief(status="approved"))
    path = _write(tmp_path / "engineer.md", engineer_brief(ref))

    with pytest.raises(brief_input.BriefInputError):
        brief_input.inspect_brief(path)


def test_engineer_rejects_draft_or_non_customer_upstream(tmp_path: Path) -> None:
    _write(tmp_path / "customer.md", customer_brief(status="draft"))
    path = _write(tmp_path / "engineer.md", engineer_brief())

    with pytest.raises(brief_input.BriefInputError, match="approved"):
        brief_input.inspect_brief(path)


def test_engineer_rejects_multiple_path_refs(tmp_path: Path) -> None:
    _write(tmp_path / "customer.md", customer_brief(status="approved"))
    _write(tmp_path / "other.md", customer_brief(status="approved"))
    text = engineer_brief().replace(
        "traces_to: [customer.md]", "traces_to: [customer.md, other.md]"
    )
    path = _write(tmp_path / "engineer.md", text)

    with pytest.raises(brief_input.BriefInputError, match="ровно один"):
        brief_input.inspect_brief(path)


def test_materialize_preserves_bytes_and_is_reinspectable(tmp_path: Path) -> None:
    source_root = tmp_path / "input"
    customer = _write(
        source_root / "nested/customer.md", customer_brief(status="approved")
    )
    engineer = _write(
        source_root / "engineer.md", engineer_brief("nested/customer.md")
    )
    source = brief_input.inspect_brief(engineer)
    target = tmp_path / "target"

    brief_input.materialize(source, target, "workstreams/ws/spec")

    primary_out = target / "workstreams/ws/spec/00-discovery/brief.md"
    customer_out = target / "workstreams/ws/spec" / source.requirements_rel
    assert primary_out.read_bytes() == engineer.read_bytes()
    assert customer_out.read_bytes() == customer.read_bytes()
    restored = brief_input.inspect_materialized(
        target, "workstreams/ws/spec"
    )
    assert restored.as_state() == source.as_state()


def test_materialized_tamper_is_detected(tmp_path: Path) -> None:
    path = _write(tmp_path / "brief.md", customer_brief())
    source = brief_input.inspect_brief(path)
    target = tmp_path / "target"
    brief_input.materialize(source, target, "workstreams/ws/spec")
    (target / "workstreams/ws/spec/00-discovery/brief.md").write_text(
        customer_brief().replace("Goal", "Changed"), encoding="utf-8"
    )

    assert brief_input.inspect_materialized(
        target, "workstreams/ws/spec"
    ).as_state() != source.as_state()
