"""Манифест заявки в одобряемом коммите (спека §2, §7)."""

from __future__ import annotations

import pytest

from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    RequestError,
    ledger_view,
    manifest_path,
    mirror_mismatch,
    parse_manifest,
    render_manifest,
)

PIN = PolicyPin(
    "owner/approval-policy",
    "main",
    "e" * 40,
    {"policy/approvers.env": "1" * 64},
)
REQ = NodeRequest(
    request_id="approve-2-1-1",
    ws_id="ws-7",
    product="owner/product-b",
    nodes={"requirements": NodeEntry("workstreams/ws-7/requirements.md", "f" * 40, 3)},
    upstream_pins={"requirements": {"charter": "a" * 40}},
    policy=PIN,
)


def test_round_trip() -> None:
    assert parse_manifest(render_manifest(REQ)) == REQ


def test_path_is_inside_workstream() -> None:
    assert manifest_path("ws-7", "approve-2-1-1") == (
        "workstreams/ws-7/approvals/requests/approve-2-1-1.yaml"
    )


def test_unknown_act_refused_before_anything_else() -> None:
    text = render_manifest(REQ).replace("act: review/v1", "act: review/v2")
    with pytest.raises(RequestError, match="review/v2"):
        parse_manifest(text)


def test_manifest_without_nodes_refused() -> None:
    """Review Focus 4: пустая заявка не проходит D2 вакуумно."""
    empty = NodeRequest(REQ.request_id, REQ.ws_id, REQ.product, {}, {}, PIN)
    with pytest.raises(RequestError, match="узл"):
        parse_manifest(render_manifest(empty))


def test_node_with_empty_path_refused() -> None:
    """Review Focus 4: узел без одобряемого пути — отказ."""
    bad = NodeRequest(
        REQ.request_id,
        REQ.ws_id,
        REQ.product,
        {"requirements": NodeEntry("", "f" * 40, 3)},
        REQ.upstream_pins,
        PIN,
    )
    with pytest.raises(RequestError, match="path"):
        parse_manifest(render_manifest(bad))


@pytest.mark.parametrize("gen", [True, 0, -1, "3", 1.0])
def test_generation_must_be_real_int_at_least_one(gen: object) -> None:
    """C11: bool — int в Python, `generation: true` обязан быть отказом."""
    bad = NodeRequest(
        REQ.request_id,
        REQ.ws_id,
        REQ.product,
        {"requirements": NodeEntry("workstreams/ws-7/r.md", "f" * 40, gen)},  # type: ignore[arg-type]
        REQ.upstream_pins,
        PIN,
    )
    with pytest.raises(RequestError, match="generation"):
        parse_manifest(render_manifest(bad))


BODY = f"act: review/v1\npolicy-activation: {PIN.activation_commit}\n"


def test_mirror_matches() -> None:
    assert mirror_mismatch(REQ, "approve-2-1-1", ledger_view(REQ), BODY) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy", {**PIN.as_record(), "activation_commit": "0" * 40}),
        ("content_hashes", {"requirements": "0" * 40}),
        ("upstream_pins", {"requirements": {}}),
        ("generations", {"requirements": 9}),
        ("act", None),
    ],
)
def test_mirror_rewritten_field_detected(field: str, value: object) -> None:
    """Приёмка №13: любое переписанное зеркало в run.json — отказ."""
    op = {**ledger_view(REQ), field: value}
    assert field in (mirror_mismatch(REQ, "approve-2-1-1", op, BODY) or "")


@pytest.mark.parametrize(
    "extra",
    ["act: merge\n", f"policy-activation: {'0' * 40}\n", "act: review/v1\n"],
    ids=["contradicting-act", "contradicting-pin", "duplicate-same"],
)
def test_body_duplicates_detected(extra: str) -> None:
    """Зеркало в теле: каждый ключ ровно один раз и равен манифесту."""
    assert mirror_mismatch(REQ, "approve-2-1-1", ledger_view(REQ), BODY + extra)


def test_mirror_other_request_key_detected() -> None:
    assert mirror_mismatch(REQ, "approve-9-9-9", ledger_view(REQ), BODY)


def test_missing_upstream_pins_refused() -> None:
    text = render_manifest(REQ).replace("upstream_pins:", "upstream_pinz:")
    with pytest.raises(RequestError, match="upstream"):
        parse_manifest(text)
