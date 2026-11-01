"""Решение по доставке поверх доверенного контекста (общий вход Task 13/14)."""

from __future__ import annotations

import pytest

pytest.importorskip("steward")

from governance import act_context, act_facts, act_trust  # noqa: E402
from governance.act_check import LegacyAccepted  # noqa: E402
from governance.act_decision import (  # noqa: E402
    Accepted,
    DeliveryView,
    WaitingDelivery,
)
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from tests.act_cases_facts import (  # noqa: E402
    HEAD,
    PATH,
    act,
    context,
    policy_files,
    sha,
    ts,
)

NOT_MERGED = DeliveryView(False, None, None, None, None, None, False, {})


class NoEvents:
    """Журнал стенда: чисто; запись события здесь — ошибка теста."""

    def status(self, snap: object) -> str:
        return "clear"

    def record(self, snap: object, source: str, detail: str) -> None:
        pytest.fail(f"событие периметра: {source}: {detail}")


def _trust(ctx) -> act_trust.Trust:
    snap = act_trust.TrustSnapshot(
        act_trust.TrustRoot("github.com", 11, "main", "1" * 40),
        "owner/df-trust",
        ctx.source,
        ctx.epochs,
    )
    return act_trust.Trust(snap, ())


def _wire(monkeypatch, ctx, *, merged: bool = True) -> None:
    delivery = ctx.delivery if merged else NOT_MERGED
    monkeypatch.setattr(
        act_facts,
        "read",
        lambda t, repo, pr, paths: (delivery, ctx.facts if merged else None),
    )
    monkeypatch.setattr(
        act_trust, "load_anchor_times", lambda anon, trust, t: dict(ctx.anchor_at)
    )
    monkeypatch.setattr(
        act_trust,
        "load_ranges",
        lambda anon, trust, anchors, t, journal: (
            dict(ctx.ranges),
            dict(ctx.policy_sets),
        ),
    )


def _verdict(ctx, **kw):
    readers = act_trust.Readers(object(), lambda c, p: None)
    args: dict[str, object] = {
        "product": ctx.product,
        "pr": 7,
        "paths": [PATH],
        "request": ctx.request,
        "request_act": ctx.request_act,
        "head_sha": HEAD,
        "facts_transport": object(),
        "self_hashes": lambda: dict(ctx.head_self_hashes),
        "legacy": None,
    }
    args.update(kw)
    return act_context.delivery_verdict(readers, _trust(ctx), NoEvents(), **args)


def test_happy_path(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx)
    outcome, delivery = _verdict(ctx)
    assert isinstance(outcome, Accepted) and delivery is ctx.delivery


def test_not_merged_waits_without_reading_ranges(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx, merged=False)
    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: pytest.fail("ranges"))
    assert _verdict(ctx)[0] == WaitingDelivery()


def test_legacy_computed_only_for_unmarked(monkeypatch) -> None:
    ctx = context(modes={sha("p1"): "merge"}, request_act=None)
    _wire(monkeypatch, ctx)
    calls: list[int] = []
    outcome, _ = _verdict(
        ctx,
        request=None,
        request_act=None,
        legacy=lambda: calls.append(1) or LegacyAccepted("bob"),
    )
    assert outcome == LegacyAccepted("bob") and calls == [1]


def test_unavailable_self_hash_is_unavailable(monkeypatch) -> None:
    ctx = context()
    _wire(monkeypatch, ctx)
    got = _verdict(ctx, self_hashes=lambda: Unavailable("хеш не вычислен"))[0]
    assert isinstance(got, Unavailable) and got.reason == "хеш не вычислен"


def test_ranges_unavailable_is_unavailable(monkeypatch) -> None:
    ctx = context(ranges={1: (act("p1", "08:00:00"),)})
    _wire(monkeypatch, ctx)
    monkeypatch.setattr(act_trust, "load_ranges", lambda *a: Unavailable("history"))
    got = _verdict(ctx)[0]
    assert isinstance(got, Unavailable) and got.reason == "history"


def test_loader_supplies_prior_epochs_for_merge_admission(monkeypatch) -> None:
    """B1-10: при `merge` на T admission требует историю эпохи 1 — настоящий
    загрузчик (`load_anchor_times` + `load_ranges`) её даёт; раньше — Unavailable."""
    p0, p1 = sha("p0"), sha("p1")
    epochs = (Epoch(1, p0, p0, ts("07:00:00"), "разрыв"), Epoch(2, p1))
    times = {p0: parse_ts(ts("06:00:00")), p1: parse_ts(ts("08:00:00"))}
    monkeypatch.setattr(
        act_facts,
        "activation",
        lambda t, s, c: act_facts.ActivationFact(1, c, times[c], {}),
    )
    monkeypatch.setattr(
        act_facts,
        "history",
        lambda t, s, a, u: (act_facts.ActivationFact(1, a, times[a], {}),),
    )
    monkeypatch.setattr(
        act_trust,
        "policy_set",
        lambda anon, s, a: parse_policy_set(
            a.merge_commit, policy_files("merge", ("alice",))
        ),
    )
    ctx = context(request_act=None)
    monkeypatch.setattr(
        act_facts, "read", lambda t, repo, pr, paths: (ctx.delivery, ctx.facts)
    )
    snap = act_trust.TrustSnapshot(
        act_trust.TrustRoot("github.com", 11, "main", "1" * 40),
        "owner/df-trust",
        ctx.source,
        epochs,
    )
    outcome, _ = act_context.delivery_verdict(
        act_trust.Readers(object(), lambda c, p: None),
        act_trust.Trust(snap, ()),
        NoEvents(),
        product=ctx.product,
        pr=7,
        paths=[],
        request=None,
        request_act=None,
        head_sha=HEAD,
        facts_transport=object(),
        self_hashes=lambda: {},
        legacy=lambda: LegacyAccepted("bob"),
    )
    assert outcome == LegacyAccepted("bob")
