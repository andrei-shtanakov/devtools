"""Адаптер act_facts: перевод исходов steward и граница исключений."""

from __future__ import annotations

import json
from datetime import UTC, timedelta, timezone
from pathlib import Path

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


VECTORS = Path(__file__).resolve().parent.parent / "contracts/approval-facts/v3/vectors"
POLICY = SourceV2(
    "andrei-shtanakov/approval-policy", "main", "0" * 40, frozenset({"x"})
)
BRANCH_PATH = "repos/andrei-shtanakov/approval-policy/branches/main"
ANCHOR = "6dcd4ce23d88e2ee9568ba546c007c63d9131c1b"


class _GitHub:
    """Fake GitHub over history vectors: answers by path, any order, any number of
    times; the branch head is movable. Unknown paths are 404."""

    def __init__(self, *vectors: str, branch_head: str | None = None) -> None:
        self.answers: dict[str, v3.Response] = {}
        self.sent: list[str] = []
        for name in vectors:
            spec = json.loads((VECTORS / "history" / f"{name}.json").read_text())
            for ex in spec["transport"]["exchanges"]:
                req, resp = ex["request"], ex["response"]
                self.answers.setdefault(
                    req["path"],
                    v3.Response(
                        resp["status"], resp.get("body"), resp.get("link"), None
                    ),
                )
        if branch_head is not None:
            self.answers[BRANCH_PATH] = v3.Response(
                200, {"name": "main", "commit": {"sha": branch_head}}
            )

    def send(self, request: v3.Request) -> v3.Response:
        self.sent.append(request.path)
        return self.answers.get(request.path, v3.Response(404, {"message": "Nope"}))


def test_policy_history_until_pinned_p() -> None:
    """The open epoch is read up to the pinned P, not to the moved branch head."""
    p_sha = "50c9e8d5fc98727b4bbc93cf5d64a68db647f04f"
    pinned = act_facts.history(_GitHub("merge_squash_neutral"), POLICY, ANCHOR, None)
    assert isinstance(pinned, tuple)
    assert pinned[-1].merge_commit == p_sha
    moved = _GitHub("merge_squash_neutral", branch_head="f" * 40)
    got = act_facts.history(moved, POLICY, ANCHOR, None, head=p_sha)
    assert got == pinned
    assert BRANCH_PATH not in moved.sent  # the current head is never consulted
    # without `head` the 2b behaviour stays: the current branch head is read
    assert isinstance(act_facts.history(moved, POLICY, ANCHOR, None), Unavailable)


def test_policy_rollback_after_p_same_break() -> None:
    """A break before P survives a later rollback of the branch to the anchor."""
    p_sha = "ae4f281df5a5d0ff3cad6371f76d5c29b6d953ec"
    before = act_facts.history(_GitHub("direct_push"), POLICY, ANCHOR, None)
    assert isinstance(before, act_facts.HistoryBreak)
    rolled = _GitHub("direct_push", "merge_squash_neutral", branch_head=ANCHOR)
    hidden = act_facts.history(rolled, POLICY, ANCHOR, None)
    assert isinstance(hidden, tuple)  # read to the current head the break is gone
    assert act_facts.history(rolled, POLICY, ANCHOR, None, head=p_sha) == before


def test_closed_epoch_until_wins_over_head() -> None:
    """A closed epoch is read to its `until`; `head` only pins the open epoch."""
    until = "ae4f281df5a5d0ff3cad6371f76d5c29b6d953ec"
    hub = _GitHub("merge_squash_neutral")
    closed = act_facts.history(hub, POLICY, ANCHOR, until)
    assert act_facts.history(hub, POLICY, ANCHOR, until, head="f" * 40) == closed
