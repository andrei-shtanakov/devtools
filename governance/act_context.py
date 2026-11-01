"""Решение по доставке поверх доверенного контекста (общий вход Task 13, 14).

Порядок §5.5: доставка первой — не смёржен → `WaitingDelivery`, ничего
больше не читается; затем время якорей и диапазоны на `T = merged_at`,
вердикт старого кода (только у заявки без маркера), `check_delivery`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from governance import act_facts, act_trust
from governance.act_check import (
    DeliveryContext,
    LegacyAccepted,
    Outcome,
    check_delivery,
)
from governance.act_decision import DeliveryView, Rejected, WaitingDelivery
from governance.act_request import NodeRequest
from governance.act_time import Unavailable, parse_ts


def delivery_verdict(
    readers: act_trust.Readers,
    trust: act_trust.Trust,
    journal: act_trust.Journal,
    *,
    product: str,
    pr: int,
    paths: Sequence[str],
    request: NodeRequest | None,
    request_act: str | None,
    head_sha: str,
    facts_transport: Any,
    self_hashes: Callable[[], Mapping[str, str] | Unavailable],
    legacy: Callable[[], LegacyAccepted | Rejected | Unavailable] | None,
) -> tuple[Outcome, DeliveryView | None]:
    """Исход доставки PR `product#pr` и прочитанная доставка.

    `journal` обязателен (ревизия b1): нарушение формы истории в диапазоне
    любой эпохи регистрируется событием периметра (`load_ranges`)."""
    got = act_facts.read(facts_transport, product, pr, paths)
    if isinstance(got, Unavailable):
        return got, None
    delivery, facts = got
    if facts is None or delivery.merged_at is None:
        return WaitingDelivery(), delivery
    t = parse_ts(delivery.merged_at)
    anchors = act_trust.load_anchor_times(readers.anon, trust, t)
    loaded = act_trust.load_ranges(readers.anon, trust, anchors, t, journal)
    if isinstance(loaded, Unavailable):
        return loaded, delivery
    ranges, sets = loaded
    hashes = self_hashes() if request is not None else {}
    if isinstance(hashes, Unavailable):
        return hashes, delivery
    ctx = DeliveryContext(
        source=trust.source,
        product=product,
        request=request,
        request_act=request_act,
        head_sha=head_sha,
        delivery=delivery,
        facts=facts,
        head_self_hashes=hashes,
        epochs=trust.epochs,
        anchor_at=anchors,
        ranges=ranges,
        policy_sets=sets,
        # Лениво и один раз: старый код — только после `OLD_READER`, и не по
        # разу на каждый порядок группы равных меток (§8.2, I-1 ревью Task 14).
        legacy=functools.cache(legacy)
        if request is None and legacy is not None
        else None,
    )
    return check_delivery(ctx), delivery
