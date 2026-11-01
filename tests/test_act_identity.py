"""Идентичность судимых объектов: отрицательные двойники по каждому компоненту (b1)."""

from __future__ import annotations

import pytest

from governance.act_identity import (
    RequestIdentity,
    head_manifest_refusal,
    manifest_refusal,
    paths_refusal,
)
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, render_manifest

ID = RequestIdentity("owner/p", "ws-7", "approve-2-1-1")
PIN = PolicyPin("owner/approval-policy", "main", "a" * 40, {})


def req(**kw: object) -> NodeRequest:
    base: dict[str, object] = {
        "request_id": "approve-2-1-1",
        "ws_id": "ws-7",
        "product": "owner/p",
        "nodes": {"requirements": NodeEntry("spec/10-r.md", "h", 1)},
        "upstream_pins": {"requirements": {}},
        "policy": PIN,
    }
    base.update(kw)
    return NodeRequest(**base)  # type: ignore[arg-type]


def test_matching_manifest_passes() -> None:
    assert manifest_refusal(req(), ID) is None
    assert paths_refusal(req(), {"requirements": "spec/10-r.md"}) is None


@pytest.mark.parametrize("field", ["product", "ws_id", "request_id"])
def test_each_identity_component_is_checked(field: str) -> None:
    got = manifest_refusal(req(**{field: "other"}), ID)
    assert got is not None and f"манифест: {field} other" in got


def test_node_outside_dag_and_wrong_path() -> None:
    outside = paths_refusal(req(), {})
    assert outside is not None and "не из активного DAG" in outside
    moved = paths_refusal(req(), {"requirements": "spec/other.md"})
    assert moved is not None and "путь узла" in moved


def test_manifest_only_in_delivery_or_changed() -> None:
    text = render_manifest(req())
    assert head_manifest_refusal(text, text) is None
    absent = head_manifest_refusal(None, text)
    assert absent is not None and "не в одобренном head" in absent
    changed = head_manifest_refusal(text + "# x\n", text)
    assert changed is not None and "≠" in changed


def test_marker_and_memo_carry_full_identity() -> None:
    other_ws = RequestIdentity("owner/p", "ws-8", "approve-2-1-1")
    assert ID.migration_marker != other_ws.migration_marker
    assert ID.memo_key != other_ws.memo_key
    assert ID.manifest_path == "workstreams/ws-7/approvals/requests/approve-2-1-1.yaml"
