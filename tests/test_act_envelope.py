"""Конверт узла и история поколений (спека §9.1 (а), §8, приёмка №17, 23, 25, 31)."""

from __future__ import annotations

import dataclasses

from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    Envelope,
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
    assert envelope_refusal(other, [OLD, NEW], "req", "h" * 40, PINS, "21")


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
    assert generation_refusal([OLD, NEW, stale], "req")


def test_envelope_with_other_policy_pin_refused() -> None:
    swapped = dataclasses.replace(_env(NEW), policy={**PIN.as_record(), "branch": "x"})
    assert envelope_refusal(swapped, [OLD, NEW], "req", "h" * 40, PINS, "21")


def test_node_bytes_changed_refused() -> None:
    assert envelope_refusal(_env(NEW), [OLD, NEW], "req", "z" * 40, PINS, "21")
