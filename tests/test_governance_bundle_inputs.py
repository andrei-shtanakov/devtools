"""Direct DAG and supplemental discovery inputs share one resolver."""

from __future__ import annotations

from pathlib import Path

from governance import brief_input, bundle_dag, bundle_inputs
from governance.facts import Outcome
from governance import run_state as rs
from governance.stale_adapter import blob_sha1, blob_sha1_bytes


class ShowOps:
    def __init__(self, values: dict[tuple[str, str], str | None]):
        self.values = values
        self.calls: list[tuple[str, str]] = []

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        self.calls.append((ref, path))
        return self.values.get((ref, path))

    def show_file_bytes(
        self, target_dir: str, ref: str, path: str
    ) -> bytes | None:
        self.calls.append((ref, path))
        value = self.values.get((ref, path))
        return value.encode("utf-8") if value is not None else None


def _state(tmp_path: Path, *, brief=None) -> rs.RunState:
    return rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(tmp_path), bundle_dir="workstreams/WS-1/spec",
        profile="profiles/team-exp.yaml", run_id="r-inputs", brief=brief,
    )


def test_classic_direct_blobs_are_only_dag_upstreams(tmp_path: Path) -> None:
    state = _state(tmp_path)
    charter = "charter bytes"
    rel = f"{state.bundle_dir}/00-charter.md"
    ops = ShowOps({("base", rel): charter})

    fact = bundle_inputs.direct_blobs(
        state, ops, bundle_dag.BUNDLE_DAG, "requirements", "base"
    )

    assert fact.outcome is Outcome.FOUND
    assert fact.value == {"charter": blob_sha1(charter)}


def test_known_texts_avoid_duplicate_ref_read(tmp_path: Path) -> None:
    state = _state(tmp_path)
    ops = ShowOps({})

    fact = bundle_inputs.direct_blobs(
        state, ops, bundle_dag.BUNDLE_DAG, "requirements", "base",
        known_texts={"00-charter.md": "cached charter"},
    )

    assert fact.outcome is Outcome.FOUND
    assert fact.value == {"charter": blob_sha1("cached charter")}
    assert ops.calls == []


def test_charter_reads_customer_source_from_same_ref(tmp_path: Path) -> None:
    text = "source bytes"
    descriptor = {
        "frame": "customer",
        "primary": brief_input.PRIMARY_REL,
        "requirements_source": brief_input.PRIMARY_REL,
        "source_paths": [brief_input.PRIMARY_REL],
        "source_blobs": {"discovery-brief": blob_sha1(text)},
    }
    state = _state(tmp_path, brief=descriptor)
    rel = f"{state.bundle_dir}/{brief_input.PRIMARY_REL}"
    ops = ShowOps({("head-sha", rel): text})

    fact = bundle_inputs.direct_blobs(
        state, ops, bundle_dag.BUNDLE_DAG, "charter", "head-sha"
    )

    assert fact.outcome is Outcome.FOUND
    assert fact.value == {"discovery-brief": blob_sha1(text)}
    assert ops.calls == [("head-sha", rel)]


def test_engineer_source_is_complete_and_ordered(tmp_path: Path) -> None:
    engineer, customer = "engineer", "customer"
    descriptor = {
        "frame": "engineer",
        "primary": brief_input.PRIMARY_REL,
        "requirements_source": "00-discovery/customer.md",
        "source_paths": [brief_input.PRIMARY_REL, "00-discovery/customer.md"],
        "source_blobs": {
            "discovery-brief": blob_sha1(engineer),
            "discovery-customer": blob_sha1(customer),
        },
    }
    state = _state(tmp_path, brief=descriptor)
    base = Path(state.target_dir) / state.bundle_dir
    (base / brief_input.PRIMARY_REL).parent.mkdir(parents=True)
    (base / brief_input.PRIMARY_REL).write_text(engineer, encoding="utf-8")
    (base / "00-discovery/customer.md").write_text(customer, encoding="utf-8")

    fact = bundle_inputs.direct_blobs(
        state, ShowOps({}), bundle_dag.BUNDLE_DAG, "charter", None
    )

    assert fact.outcome is Outcome.FOUND
    assert fact.value == descriptor["source_blobs"]


def test_changed_source_is_forbidden_not_replaced_by_descriptor(
    tmp_path: Path,
) -> None:
    descriptor = {
        "frame": "customer",
        "primary": brief_input.PRIMARY_REL,
        "requirements_source": brief_input.PRIMARY_REL,
        "source_paths": [brief_input.PRIMARY_REL],
        "source_blobs": {"discovery-brief": blob_sha1("expected")},
    }
    state = _state(tmp_path, brief=descriptor)
    rel = f"{state.bundle_dir}/{brief_input.PRIMARY_REL}"

    fact = bundle_inputs.direct_blobs(
        state, ShowOps({("base", rel): "changed"}),
        bundle_dag.BUNDLE_DAG, "charter", "base",
    )

    assert fact.outcome is Outcome.FORBIDDEN
    assert fact.value is None
    assert "descriptor" in fact.detail


def test_source_blob_preserves_crlf_bytes(tmp_path: Path) -> None:
    data = b"source\r\nbytes\r\n"
    descriptor = {
        "frame": "customer",
        "primary": brief_input.PRIMARY_REL,
        "requirements_source": brief_input.PRIMARY_REL,
        "source_paths": [brief_input.PRIMARY_REL],
        "source_blobs": {"discovery-brief": blob_sha1_bytes(data)},
    }
    state = _state(tmp_path, brief=descriptor)
    rel = f"{state.bundle_dir}/{brief_input.PRIMARY_REL}"

    fact = bundle_inputs.direct_blobs(
        state,
        ShowOps({("base", rel): data.decode("utf-8")}),
        bundle_dag.BUNDLE_DAG,
        "charter",
        "base",
    )

    assert fact.outcome is Outcome.FOUND
    assert fact.value == descriptor["source_blobs"]


def test_missing_source_is_unavailable(tmp_path: Path) -> None:
    descriptor = {
        "frame": "customer",
        "primary": brief_input.PRIMARY_REL,
        "requirements_source": brief_input.PRIMARY_REL,
        "source_paths": [brief_input.PRIMARY_REL],
        "source_blobs": {"discovery-brief": "a" * 40},
    }
    fact = bundle_inputs.direct_blobs(
        _state(tmp_path, brief=descriptor), ShowOps({}),
        bundle_dag.BUNDLE_DAG, "charter", "base",
    )

    assert fact.outcome is Outcome.UNAVAILABLE


def test_corrupt_descriptor_cannot_escape_bundle(tmp_path: Path) -> None:
    descriptor = {
        "frame": "customer",
        "primary": "../../outside.md",
        "requirements_source": "../../outside.md",
        "source_paths": ["../../outside.md"],
        "source_blobs": {"discovery-brief": "a" * 40},
    }

    fact = bundle_inputs.direct_blobs(
        _state(tmp_path, brief=descriptor), ShowOps({}),
        bundle_dag.BUNDLE_DAG, "charter", None,
    )

    assert fact.outcome is Outcome.UNAVAILABLE
    assert "primary path" in fact.detail
