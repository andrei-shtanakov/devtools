"""Единый admission режима до выбора читателя (спека §10, R4-4)."""

from __future__ import annotations

from governance.act_admission import Admitted, ModeEvent, Refused, admit
from governance.act_time import Ambiguous, Clock, Unavailable, parse_ts, resolve

T = parse_ts("2026-10-12T10:00:00Z")
EARLY = parse_ts("2026-10-01T00:00:00Z")
LATE = parse_ts("2026-10-20T00:00:00Z")
M_MERGE = ModeEvent(("activation", "m1"), EARLY, "merge")
M_REVIEW = ModeEvent(("activation", "m2"), parse_ts("2026-10-05T00:00:00Z"), "review")


def test_before_first_mode_entry_is_legacy() -> None:
    late_mode = ModeEvent(("activation", "m1"), LATE, "merge")
    assert admit(None, [late_mode], Clock(T)) == Admitted("legacy")


def test_marker_before_first_mode_entry_refused() -> None:
    assert isinstance(admit("review/v1", [], Clock(T)), Refused)


def test_merge_mode_admits_only_unmarked() -> None:
    assert admit(None, [M_MERGE], Clock(T)) == Admitted("merge")
    assert isinstance(admit("review/v1", [M_MERGE], Clock(T)), Refused)


def test_review_mode_admits_only_marked() -> None:
    """Приёмка №24: v1-заявка после активации review — отказ."""
    assert admit("review/v1", [M_MERGE, M_REVIEW], Clock(T)) == Admitted("review")
    assert isinstance(admit(None, [M_MERGE, M_REVIEW], Clock(T)), Refused)


def test_reverse_switch_unavailable() -> None:
    """Приёмка №26: review → merge в v1 не поддерживается — unavailable, не отказ."""
    back = ModeEvent(("activation", "m3"), parse_ts("2026-10-06T00:00:00Z"), "merge")
    result = admit(None, [M_MERGE, M_REVIEW, back], Clock(T))
    assert isinstance(result, Unavailable) and "обратн" in result.reason


def test_removed_mode_entry_refused() -> None:
    removed = ModeEvent(("activation", "m3"), parse_ts("2026-10-06T00:00:00Z"), "")
    result = admit(None, [M_MERGE, removed], Clock(T))
    assert isinstance(result, Refused) and "отсутствует" in result.reason


def test_mode_activation_in_delivery_second_uses_clock() -> None:
    same = ModeEvent(("activation", "m2"), T, "review")
    before = Clock(T, frozenset({same.key}))
    assert admit(None, [M_MERGE, same], Clock(T)) == Admitted("merge")
    assert isinstance(admit(None, [M_MERGE, same], before), Refused)


def test_refusals_with_different_reasons_are_one_verdict() -> None:
    """C3: все допустимые порядки отказывают (причины разные) — это Refused."""
    first = ModeEvent(("activation", "m1"), T, "merge")

    def decide(clock: Clock) -> Admitted | Refused | Unavailable:
        return admit("review/v1", [first], clock)

    assert decide(Clock(T)).reason != decide(Clock(T, frozenset({first.key}))).reason
    result = resolve(T, [first.key], {}, decide)
    assert isinstance(result, Refused)
    assert not isinstance(result, Ambiguous)


def test_refused_vs_admitted_across_orders_is_ambiguous() -> None:
    """Порядки дают Refused (merge) и Admitted (review): неоднозначность."""
    same = ModeEvent(("activation", "m2"), T, "review")

    def decide(clock: Clock) -> Admitted | Refused | Unavailable:
        return admit("review/v1", [M_MERGE, same], clock)

    assert isinstance(resolve(T, [same.key], {}, decide), Ambiguous)
