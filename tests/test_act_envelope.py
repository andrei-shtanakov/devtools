"""Конверт узла и история поколений (спека §9.1 (а), §8, приёмка №17, 23, 25, 31)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
import yaml

from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    Envelope,
    EnvelopeError,
    StopRecord,
    current_event,
    envelope_path,
    envelope_refusal,
    generation_refusal,
    next_generation,
    parse_envelope,
    parse_stop,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_request import NodeEntry, NodeRequest, PolicyPin, manifest_path

PIN = PolicyPin("o/approval-policy", "main", "e" * 40, {})
PINS = {"charter": "a" * 40}


def _req(rid: str, gen: int, digest: str = "h" * 40) -> NodeRequest:
    return NodeRequest(
        rid,
        "ws-7",
        "o/p",
        {"req": NodeEntry("workstreams/ws-7/req.md", digest, gen)},
        {"req": dict(PINS)},
        PIN,
    )


OLD = DeliveredRequest("1" * 40, "a1" * 20, _req("approve-2-1-1", 1))
NEW = DeliveredRequest("2" * 40, "a2" * 20, _req("approve-2-1-2", 2))
STOP2 = DeliveredStop("3" * 40, StopRecord("req", 2, "переоткрыт человеком"))
AGAIN = DeliveredRequest("4" * 40, "a4" * 20, _req("approve-2-1-3", 3))


def _env(delivered: DeliveredRequest, review_id: str = "21") -> Envelope:
    req = delivered.request
    return Envelope(
        node="req",
        request_id=req.request_id,
        generation=req.nodes["req"].generation,
        manifest_path=manifest_path("ws-7", req.request_id),
        head_sha=delivered.head_sha,
        self_hash="h" * 40,
        upstream_pins=dict(PINS),
        merge_commit=delivered.merge_commit,
        review_id=review_id,
        policy=PIN.as_record(),
    )


def test_round_trips_and_paths() -> None:
    assert parse_envelope(render_envelope(_env(NEW))) == _env(NEW)
    assert parse_stop(render_stop(STOP2.stop)) == STOP2.stop
    assert envelope_path("ws-7", "req") == "workstreams/ws-7/approvals/req.yaml"
    assert stop_path("ws-7", "req", 2) == "workstreams/ws-7/approvals/stops/req-g2.yaml"


def test_current_event_is_last_delivered() -> None:
    assert current_event([OLD, NEW], "req") is NEW
    assert current_event([OLD, NEW, STOP2], "req") is STOP2
    assert current_event([OLD, NEW], "other") is None


def test_valid_envelope_accepted() -> None:
    assert envelope_refusal(_env(NEW), [OLD, NEW], "req", "h" * 40, PINS, "21") is None


def test_missing_envelope_not_approved_without_fallback() -> None:
    """Приёмка №23."""
    reason = envelope_refusal(None, [OLD, NEW], "req", "h" * 40, PINS, "21")
    assert reason and "нет конверта" in reason


def test_previous_generation_same_bytes_refused() -> None:
    """Приёмка №31."""
    reason = envelope_refusal(_env(OLD), [OLD, NEW], "req", "h" * 40, PINS, "21")
    assert reason and "поколени" in reason


def test_other_node_envelope_refused() -> None:
    """Приёмка №17."""
    other = dataclasses.replace(_env(NEW), node="design")
    reason = envelope_refusal(other, [OLD, NEW], "req", "h" * 40, PINS, "21")
    assert reason and "подставлен" in reason


def test_reopen_with_unchanged_content_stops_approval() -> None:
    """Приёмка №25: остановка — явное действие; прежний конверт не действует."""
    reason = envelope_refusal(_env(NEW), [OLD, NEW, STOP2], "req", "h" * 40, PINS, "21")
    assert reason and "останов" in reason


def test_new_generation_after_stop_approved() -> None:
    history = [OLD, NEW, STOP2, AGAIN]
    assert envelope_refusal(_env(AGAIN), history, "req", "h" * 40, PINS, "21") is None
    assert next_generation(history, "req") == 4


def test_non_monotonic_generation_refused() -> None:
    stale = DeliveredRequest("5" * 40, "a5" * 20, _req("approve-2-1-4", 2))
    reason = generation_refusal([OLD, NEW, stale], "req")
    assert reason and "не больше" in reason


def test_envelope_with_other_policy_pin_refused() -> None:
    swapped = dataclasses.replace(_env(NEW), policy={**PIN.as_record(), "branch": "x"})
    assert envelope_refusal(swapped, [OLD, NEW], "req", "h" * 40, PINS, "21")


def test_node_bytes_changed_refused() -> None:
    assert envelope_refusal(_env(NEW), [OLD, NEW], "req", "z" * 40, PINS, "21")


def _env_doc() -> dict[str, Any]:
    return yaml.safe_load(render_envelope(_env(NEW)))


def _stop_doc() -> dict[str, Any]:
    return yaml.safe_load(render_stop(STOP2.stop))


def _mut(doc: dict[str, Any], **changes: Any) -> str:
    out = {**doc, **changes}
    return yaml.safe_dump({k: v for k, v in out.items() if v is not _GONE})


_GONE = object()
BAD_SCALARS = [2.0, True, "2", None, [2]]


def _env_cases() -> list[str]:
    doc = _env_doc()
    cases = [
        _mut(doc, schema="other/v1"),
        _mut(doc, schema=_GONE),
        _mut(doc, extra="x"),
        "node: [unclosed",
        "- a\n- b\n",
        "",
        _mut(doc, upstream_pins={"charter": 1}),
        _mut(doc, upstream_pins=["charter"]),
        _mut(doc, policy="x"),
        _mut(doc, policy={**doc["policy"], "extra": "x"}),
        _mut(doc, policy={**doc["policy"], "branch": 1}),
        _mut(doc, policy={**doc["policy"], "files_sha256": {"a": 1}}),
    ]
    cases += [_mut(doc, generation=v) for v in BAD_SCALARS]
    cases += [_mut(doc, node=1), _mut(doc, review_id=21)]
    cases += [_mut(doc, **{k: _GONE}) for k in doc if k != "schema"]
    return cases


def _stop_cases() -> list[str]:
    doc = _stop_doc()
    cases = [
        _mut(doc, schema="other/v1"),
        _mut(doc, schema=_GONE),
        _mut(doc, extra="x"),
        "node: [unclosed",
        "",
        _mut(doc, reason=5),
        _mut(doc, node=["req"]),
    ]
    cases += [_mut(doc, generation=v) for v in BAD_SCALARS]
    cases += [_mut(doc, **{k: _GONE}) for k in doc if k != "schema"]
    return cases


@pytest.mark.parametrize("text", _env_cases())
def test_parse_envelope_rejects_malformed(text: str) -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope(text)


@pytest.mark.parametrize("text", _stop_cases())
def test_parse_stop_rejects_malformed(text: str) -> None:
    with pytest.raises(EnvelopeError):
        parse_stop(text)


@pytest.mark.parametrize(
    "text",
    [
        render_envelope(_env(NEW)) + "1: x\n",
        render_envelope(_env(NEW)).replace("policy:\n", "policy:\n  1: x\n"),
        render_stop(STOP2.stop) + "1: x\n",
    ],
    ids=["envelope", "policy", "stop"],
)
def test_mixed_type_keys_are_envelope_error(text: str) -> None:
    """Ключи разных типов (агентский YAML) — `EnvelopeError`, не `TypeError`."""
    parse = parse_stop if "node-approval-stop" in text else parse_envelope
    with pytest.raises(EnvelopeError):
        parse(text)


def test_no_delivered_request_not_approved() -> None:
    """Узел без доставленной заявки review/v1 — «не одобрен»."""
    for history in ([], [OLD]):
        reason = envelope_refusal(_env(NEW), history, "design", "h" * 40, PINS, "21")
        assert reason and "нет доставленной заявки" in reason


def test_stop_of_other_generation_refused() -> None:
    """Остановка обязана называть действующее поколение."""
    stale = DeliveredStop("3" * 40, StopRecord("req", 1, "не то поколение"))
    reason = envelope_refusal(_env(NEW), [OLD, NEW, stale], "req", "h" * 40, PINS, "21")
    assert reason and "остановка поколения 1, действует 2" in reason
