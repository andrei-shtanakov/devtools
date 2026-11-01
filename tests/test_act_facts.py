"""Адаптер act_facts: перевод исходов steward и граница исключений."""

from __future__ import annotations

from datetime import UTC, timedelta, timezone

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_facts  # noqa: E402
from governance.act_source import SourceV2  # noqa: E402
from governance.act_time import Clock, Unavailable, parse_ts  # noqa: E402

HEAD = "1" * 40
SOURCE = SourceV2("owner/approval-policy", "main", "a" * 40, frozenset({"owner"}))


def _facts(
    *reviews: v3.Review, dismissals: tuple[v3.Dismissal, ...] = ()
) -> v3.ReviewFacts:
    return v3.ReviewFacts("owner/p", 7, HEAD, "main", "MERGED", reviews, dismissals)


class _Raising:
    def send(self, request: v3.Request) -> v3.Response:
        raise OSError("socket closed")


def test_library_exception_is_unavailable() -> None:
    """Инструкция steward: исключение — «факт не установлен», не разрешение."""
    got = act_facts.read(_Raising(), "owner/p", 7, ["a.md"])
    assert isinstance(got, Unavailable)
    assert "OSError" in got.reason


def test_caller_error_is_unavailable_too() -> None:
    """`repo` не owner/name — ValueError у steward; граница переводит в Unavailable."""
    got = act_facts.history(
        _Raising(), SourceV2("bad", "main", "a" * 40, frozenset({"o"})), "a" * 40, None
    )
    assert isinstance(got, Unavailable)


def test_effective_keys_by_review_login() -> None:
    facts = _facts(
        v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z")
    )
    eff = act_facts.effective(facts, Clock(parse_ts("2026-10-12T10:00:00Z")))
    assert list(eff) == ["alice"] and eff["alice"].login == "alice"
    assert eff["alice"].commit_id == HEAD


def test_effective_rekeys_by_login_not_by_library_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ключ результата — `review.login`, даже если библиотека ключует иначе."""
    review = v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z")
    monkeypatch.setattr(
        v3, "effective_reviews", lambda facts, t, before: {"not-alice": review}
    )
    eff = act_facts.effective(_facts(review), Clock(parse_ts("2026-10-12T10:00:00Z")))
    assert list(eff) == ["alice"]


def test_effective_converts_non_utc_clock_to_utc() -> None:
    """Aware-время с локальным офсетом: в steward уходит UTC, а не стенные часы с `Z`."""
    seen: list[str] = []
    facts = _facts(
        v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z")
    )
    real = v3.effective_reviews

    def spy(f, t, before):
        seen.append(t)
        return real(f, t, before=before)

    plus3 = timezone(timedelta(hours=3))
    t = parse_ts("2026-10-12T10:00:00Z").astimezone(plus3)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(v3, "effective_reviews", spy)
        act_facts.effective(facts, Clock(t))
    assert seen == ["2026-10-12T10:00:00Z"]


def test_call_turns_exception_into_unavailable() -> None:
    """b5/B3-11 (`_call`): исключение библиотеки — свой `Unavailable` с именем исключения."""

    def boom():
        raise OSError("socket")

    got = act_facts._call("read_act_facts", boom)
    assert isinstance(got, Unavailable)
    assert got.reason == "steward read_act_facts: OSError: socket"


def test_own_translates_steward_unavailable() -> None:
    """b5/B3-11 (`_own`): `v3.Unavailable` переводится в свой тип с той же причиной."""
    got = act_facts._own(v3.Unavailable("transport"))
    assert isinstance(got, Unavailable) and got.reason == "transport"
    assert act_facts._own("значение") == "значение"


def test_steward_unavailable_reason_kept_verbatim() -> None:
    """Отзыв ревью, не находящегося в DISMISSED — `malformed` (О13), code сохраняется."""
    facts = _facts(
        v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T09:00:00Z"),
        dismissals=(v3.Dismissal("11", "bob", "2026-10-12T09:30:00Z", "APPROVED"),),
    )
    got = act_facts.effective(facts, Clock(parse_ts("2026-10-12T10:00:00Z")))
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("malformed")


def test_event_stamps_are_normalised_for_clock() -> None:
    """Заметки части A, п. 2: Clock принимает каждую метку адаптера."""
    facts = _facts(
        v3.Review("11", "alice", "User", "DISMISSED", HEAD, "2026-10-12T09:00:00Z"),
        dismissals=(v3.Dismissal("11", "bob", "2026-10-12T10:00:00Z", "APPROVED"),),
    )
    stamps = act_facts.event_stamps(facts)
    clock = Clock(parse_ts("2026-10-12T10:00:00Z"))
    for key, stamp in stamps.items():
        assert stamp.tzinfo is UTC and stamp.microsecond == 0
        clock.precedes(key, stamp)  # не бросает ValueError
    assert act_facts.review_requires(facts) == {
        ("dismissal", "11"): frozenset({("review", "11")})
    }


def test_before_carries_only_review_keys() -> None:
    """Ключи активаций и границ эпох в Clock.before steward не передаются."""
    facts = _facts(
        v3.Review("11", "alice", "User", "APPROVED", HEAD, "2026-10-12T10:00:00Z")
    )
    clock = Clock(
        parse_ts("2026-10-12T10:00:00Z"),
        frozenset({("review", "11"), ("activation", "p" * 40)}),
    )
    eff = act_facts.effective(facts, clock)
    assert eff["alice"].state == "APPROVED"
