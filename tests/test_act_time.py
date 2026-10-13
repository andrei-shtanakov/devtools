"""§8.2: события равной секунды не упорядочены; перебор совместных порядков."""

from __future__ import annotations

import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from governance.act_time import (
    MAX_GROUP,
    Ambiguous,
    Clock,
    EventKey,
    Unavailable,
    before_sets,
    parse_ts,
    resolve,
)

T = "2026-10-12T10:00:00Z"
APPROVE = ("review", "1")
DISMISS = ("dismissal", "1")


def test_parse_ts_normalises_forms() -> None:
    assert parse_ts("2026-10-12T10:00:00Z") == parse_ts("2026-10-12T10:00:00.700+00:00")


def test_parse_ts_one_event_in_all_forms_compares_equal() -> None:
    forms = [
        "2026-10-12T10:00:00Z",
        "2026-10-12T10:00:00+00:00",
        "2026-10-12T10:00:00.123Z",
        "2026-10-12T10:00:00.999999+00:00",
        "2026-10-12T13:00:00+03:00",
    ]
    assert len({parse_ts(form) for form in forms}) == 1


def test_parse_ts_next_second_differs() -> None:
    assert parse_ts("2026-10-12T10:00:01Z") > parse_ts("2026-10-12T10:00:00.999Z")


def test_parse_ts_rejects_naive() -> None:
    with pytest.raises(ValueError):
        parse_ts("2026-10-12T10:00:00")


def test_unavailable_carries_reason() -> None:
    assert Unavailable("x").reason == "x"
    assert Ambiguous().reason == "ambiguous_event_order"


def test_clock_orders_by_stamp_then_membership() -> None:
    clock = Clock(parse_ts(T), frozenset({APPROVE}))
    assert clock.precedes(("x", "1"), parse_ts("2026-10-12T09:59:59Z"))
    assert not clock.precedes(("x", "1"), parse_ts("2026-10-12T10:00:01Z"))
    assert clock.precedes(APPROVE, parse_ts(T))
    assert not clock.precedes(DISMISS, parse_ts(T))


def test_before_sets_respect_dependencies() -> None:
    sets = before_sets([APPROVE, DISMISS], {DISMISS: frozenset({APPROVE})})
    assert frozenset({DISMISS}) not in sets
    assert len(sets) == 3


def test_dependency_outside_group_is_satisfied() -> None:
    """Approve раньше T (вне группы), отзыв в T — «отзыв до доставки» допустим."""
    sets = before_sets([DISMISS], {DISMISS: frozenset({APPROVE})})
    assert frozenset({DISMISS}) in sets


@dataclass(frozen=True)
class _Verdict:
    verdict: str
    witness: str = field(compare=False, default="")


def test_same_verdict_different_witness_not_ambiguous() -> None:
    """Approve Alice и Bob заранее; отзыв Alice в T — акт есть при любом порядке."""
    d_alice = ("dismissal", "alice")

    def decide(clock: Clock) -> _Verdict:
        return _Verdict("accepted", "bob" if d_alice in clock.before else "alice")

    result = resolve(parse_ts(T), [d_alice], {}, decide)
    assert result == _Verdict("accepted")
    assert isinstance(result, _Verdict) and result.witness == "bob"


def _brute(
    group: Sequence[EventKey], requires: Mapping[EventKey, frozenset[EventKey]]
) -> set[frozenset[EventKey]]:
    seen: set[frozenset[EventKey]] = set()
    for order in itertools.permutations([*group, ("D", "D")]):
        pos = {e: i for i, e in enumerate(order)}
        if all(pos[p] < pos[e] for e, ps in requires.items() for p in ps):
            seen.add(frozenset(e for e in group if pos[e] < pos[("D", "D")]))
    return seen


def test_matches_brute_force_linear_orders() -> None:
    rng = random.Random(7)
    outside: EventKey = ("e", "outside")
    for _ in range(40):
        group: list[EventKey] = [("e", str(i)) for i in range(rng.randint(0, 5))]
        requires = {
            e: frozenset(p for p in [outside, *group[:i]] if rng.random() < 0.3)
            for i, e in enumerate(group)
        }
        inside = {e: ps - {outside} for e, ps in requires.items()}
        assert set(before_sets(group, requires)) == _brute(group, inside)


def test_single_outcome_accepted() -> None:
    assert resolve(parse_ts(T), [APPROVE], {}, lambda clock: "ok") == "ok"


def test_differing_outcomes_ambiguous() -> None:
    result = resolve(parse_ts(T), [APPROVE], {}, lambda clock: APPROVE in clock.before)
    assert result == Ambiguous()


def test_two_dismissals_same_second_ambiguous() -> None:
    """Приёмка №49: оба отзыва в T — есть порядок «оба до T» (акта нет)."""
    d_alice, d_bob = ("dismissal", "alice"), ("dismissal", "bob")

    def decide(clock: Clock) -> bool:
        return not ({d_alice, d_bob} <= clock.before)

    assert resolve(parse_ts(T), [d_alice, d_bob], {}, decide) == Ambiguous()


def test_group_over_cap_ambiguous_without_deciding() -> None:
    group: list[EventKey] = [("e", str(i)) for i in range(MAX_GROUP)]  # + доставка

    def explode(clock: Clock) -> str:
        raise AssertionError("перебор не должен выполняться")

    assert resolve(parse_ts(T), group, {}, explode) == Ambiguous()


def test_group_at_cap_is_enumerated() -> None:
    group: list[EventKey] = [("e", str(i)) for i in range(MAX_GROUP - 1)]
    assert resolve(parse_ts(T), group, {}, lambda clock: "same") == "same"
