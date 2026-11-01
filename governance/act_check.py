"""Полная проверка доставки на срезе T (спека §6, §6.4, §8, §10).

`_decide(ctx, clock)` проходит: эпоха → диапазон → admission → пин и
набор → эффективные ревью → D1–D3. `check_delivery` перебирает её по всем
допустимым множествам событий группы равных меток (`act_time.resolve`).
Чтений форджи здесь нет. Неустановленное остаётся `Unavailable`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise

from governance import act_facts
from governance.act_admission import Admitted, ModeEvent, Refused, admit
from governance.act_decision import (
    Accepted,
    DeliveryView,
    Rejected,
    WaitingDelivery,
    decide,
)
from governance.act_epochs import Epoch
from governance.act_policy import PolicySet, mode_for
from governance.act_request import NodeRequest, PolicyPin
from governance.act_source import SourceV2
from governance.act_time import (
    Ambiguous,
    Clock,
    EventKey,
    Unavailable,
    parse_ts,
    resolve,
)

OLD_READER = Admitted("old")
EPOCH_GAP = "epoch_gap"
MARKER_BEFORE_MODE = "marker_before_mode"
ADMISSION = "admission"
NO_MANIFEST = "no_manifest"
POLICY_SOURCE = "policy_source"
MIXED_POLICY = "mixed_policy"
#: Манифест называет не тот продукт, что доставил PR (заметки части A, п. 7).
REQUEST_BINDING = "request_binding"
#: Литерал, а не импорт `approval_facts`: тот тянет `governance.ops`, а цепочка
#: импортов act_* обязана оставаться под authority-root (Task 15). Равенство с
#: `approval_facts.INVALIDATION_POLICY_CHANGED` держит тест.
POLICY_CHANGED = "policy_changed"

Span = tuple["ActivationView", ...]


@dataclass(frozen=True)
class ActivationView:
    """Активация источника; ключ совпадает с ключом якоря эпохи."""

    key: EventKey
    merge_commit: str
    at: datetime


@dataclass(frozen=True)
class DeliveryContext:
    """Всё, что решение читает; собирается из форджи (Task 12a, 13)."""

    source: SourceV2
    product: str
    request: NodeRequest | None
    request_act: str | None
    head_sha: str
    delivery: DeliveryView
    facts: object
    head_self_hashes: Mapping[str, str]
    epochs: tuple[Epoch, ...]
    anchor_at: Mapping[str, datetime | Unavailable]
    ranges: Mapping[int, Span | Unavailable]
    policy_sets: Mapping[str, PolicySet | Unavailable]
    legacy: LegacyAccepted | Rejected | Unavailable | None = None


@dataclass(frozen=True)
class LegacyAccepted:
    """Старый путь: старый код потребителя признал доставку подписью.

    `evidence` — то, что старый код запишет (`MergeEvent`, `Authorization`,
    Task 13); в вердикт не входит (§8.2: сравниваются исходы).
    """

    merged_by: str
    evidence: object = field(default=None, compare=False)


Outcome = (
    Accepted
    | LegacyAccepted
    | Rejected
    | WaitingDelivery
    | Admitted
    | Unavailable
    | Ambiguous
)


def check_delivery(ctx: DeliveryContext) -> Outcome:
    """Исход доставки по §8.2: единственный вердикт либо `Ambiguous`."""
    merged_at = ctx.delivery.merged_at
    if not ctx.delivery.merged:
        return WaitingDelivery()
    if merged_at is None:
        return Unavailable("доставка: нет merged_at")
    t = parse_ts(merged_at)
    group = sorted(k for k, at in _stamps(ctx).items() if at == t)
    return resolve(t, group, _requires(ctx), lambda clock: _decide(ctx, clock))


def live_policy_refusal(pin: PolicyPin, current_activation: str) -> str | None:
    """Живая заявка: пин обязан совпадать с текущей активацией (§6)."""
    if pin.activation_commit == current_activation:
        return None
    return (
        f"{POLICY_CHANGED}: заявка закрепила {pin.activation_commit}, "
        f"действует {current_activation}"
    )


def _activations(ctx: DeliveryContext) -> list[ActivationView]:
    return [a for span in ctx.ranges.values() if isinstance(span, tuple) for a in span]


def _stamps(ctx: DeliveryContext) -> dict[EventKey, datetime]:
    stamps = dict(act_facts.event_stamps(ctx.facts))
    stamps.update({a.key: a.at for a in _activations(ctx)})
    for e in ctx.epochs:
        anchor = ctx.anchor_at.get(e.anchor)
        if isinstance(anchor, datetime):
            stamps[("activation", e.anchor)] = anchor
        if e.valid_until is not None:
            stamps[("epoch_valid_until", str(e.epoch))] = parse_ts(e.valid_until)
    return stamps


def _requires(ctx: DeliveryContext) -> dict[EventKey, frozenset[EventKey]]:
    requires = dict(act_facts.review_requires(ctx.facts))
    for span in ctx.ranges.values():
        if not isinstance(span, tuple):
            continue
        for i, a in enumerate(span):
            same = frozenset(b.key for b in span[:i] if b.at == a.at)
            if same:
                requires[a.key] = requires.get(a.key, frozenset()) | same
    return requires


def _epoch_at(
    ctx: DeliveryContext, clock: Clock
) -> Epoch | None | Rejected | Unavailable:
    """Эпоха доставки: обход от последней эпохи назад (e7: старые якоря не нужны,
    если более поздний уже предшествует T). Пустой журнал — не «до bootstrap»:
    корректный журнал содержит хотя бы эпоху 1 (`parse_journal`)."""
    if not ctx.epochs:
        return Unavailable("журнал эпох не прочитан")
    for epoch in reversed(ctx.epochs):
        anchor = ctx.anchor_at.get(
            epoch.anchor, Unavailable("время якоря не загружено")
        )
        if isinstance(anchor, Unavailable):
            return anchor
        if not clock.precedes(("activation", epoch.anchor), anchor):
            continue
        if epoch.valid_until is not None and clock.precedes(
            ("epoch_valid_until", str(epoch.epoch)), parse_ts(epoch.valid_until)
        ):
            return Rejected(EPOCH_GAP, "межэпохальный интервал: политика не определена")
        return epoch
    return None


def _span(ctx: DeliveryContext, epoch: Epoch) -> Span | Unavailable:
    span = ctx.ranges.get(epoch.epoch, Unavailable(f"диапазон эпохи {epoch.epoch} нет"))
    if isinstance(span, Unavailable):
        return span
    if any(later.at < earlier.at for earlier, later in pairwise(span)):
        return Unavailable("merged_at активаций противоречит first-parent")
    return span


def _until_t(span: Span, clock: Clock) -> Span:
    """Активации, способные предшествовать доставке: метка ≤ T (политика после T
    на завершённый акт не влияет и не загружается — спека §6, §8.1 №5)."""
    return tuple(a for a in span if a.at <= clock.t)


def _mode_events(
    ctx: DeliveryContext, activations: Span
) -> list[ModeEvent] | Unavailable:
    events: list[ModeEvent] = []
    previous = ""
    for a in activations:
        policy = ctx.policy_sets.get(a.merge_commit, Unavailable("набор не загружен"))
        if isinstance(policy, Unavailable):
            return policy
        mode = mode_for(policy, ctx.product) or ""
        if mode != previous:
            events.append(ModeEvent(a.key, a.at, mode))
        previous = mode
    return events


def _earlier(ctx: DeliveryContext, epoch: Epoch, clock: Clock) -> Span | Unavailable:
    """Активации всех предыдущих эпох (до их `last_confirmed`), по порядку."""
    collected: list[ActivationView] = []
    for prior in ctx.epochs[: epoch.epoch - 1]:
        span = _span(ctx, prior)
        if isinstance(span, Unavailable):
            return span
        collected.extend(_until_t(span, clock))
    return tuple(collected)


def _mode_history(
    ctx: DeliveryContext, epoch: Epoch, own: Span, clock: Clock
) -> list[ModeEvent] | Unavailable:
    """История режима, от которой зависит admission.

    Если на T в своей эпохе действует `review` — admission зависит только от
    маркера, и старая история не нужна (e7). Иначе (`merge`, нет записи)
    исход зависит от всей предыстории — legacy-граница и запрет обратного
    переключения переходят через границу эпох; недоступная предыстория →
    `Unavailable` (смена эпохи не разрешает начать миграцию заново).
    """
    own_events = _mode_events(ctx, own)
    if isinstance(own_events, Unavailable):
        return own_events
    current = [e for e in own_events if clock.precedes(e.key, e.at)]
    if current and current[-1].mode == "review":
        return own_events
    prior = _earlier(ctx, epoch, clock)
    if isinstance(prior, Unavailable):
        return prior
    return _mode_events(ctx, prior + own)


def _admission(ctx: DeliveryContext, clock: Clock) -> Span | Outcome:
    epoch = _epoch_at(ctx, clock)
    if epoch is None:
        if ctx.request_act is None:
            return OLD_READER
        return Rejected(MARKER_BEFORE_MODE, "заявка review/v1 до bootstrap источника")
    if isinstance(epoch, (Rejected, Unavailable)):
        return epoch
    span = _span(ctx, epoch)
    if isinstance(span, Unavailable):
        return span
    own = _until_t(span, clock)
    modes = _mode_history(ctx, epoch, own, clock)
    if isinstance(modes, Unavailable):
        return modes
    admission = admit(ctx.request_act, modes, clock)
    if isinstance(admission, Refused):
        return Rejected(ADMISSION, admission.reason)
    if isinstance(admission, Unavailable):
        return admission
    if admission.reader != "review":
        return OLD_READER
    return own


def _pinned_policy(
    ctx: DeliveryContext, span: Span, clock: Clock
) -> PolicySet | Outcome:
    if ctx.request is None:
        return Rejected(NO_MANIFEST, "review/v1 без манифеста в одобренном коммите")
    if ctx.request.product != ctx.product:
        return Rejected(
            REQUEST_BINDING,
            f"манифест называет {ctx.request.product}, доставил {ctx.product}",
        )
    pin = ctx.request.policy
    if (pin.repo, pin.branch) != (ctx.source.repo, ctx.source.branch):
        return Rejected(POLICY_SOURCE, f"пин указывает на {pin.repo}@{pin.branch}")
    seen = [a for a in span if clock.precedes(a.key, a.at)]
    if not seen:
        return Unavailable("в диапазоне эпохи нет активации до T")
    if pin.activation_commit != seen[-1].merge_commit:
        return Rejected(POLICY_CHANGED, f"на T действует {seen[-1].merge_commit}")
    policy = ctx.policy_sets.get(
        seen[-1].merge_commit, Unavailable("набор не загружен")
    )
    if isinstance(policy, Unavailable):
        return policy
    if dict(pin.files_sha256) != dict(policy.files_sha256):
        return Rejected(MIXED_POLICY, "files_sha256 пина ≠ набора активации")
    return policy


def _legacy_verdict(ctx: DeliveryContext) -> Outcome:
    """Вердикт старого кода потребителя, вычисленный им до перебора."""
    if ctx.legacy is None:
        return Unavailable("вердикт старой проверки не вычислен")
    return ctx.legacy


def _decide(ctx: DeliveryContext, clock: Clock) -> Outcome:
    span = _admission(ctx, clock)
    if span == OLD_READER:
        return _legacy_verdict(ctx)
    if not isinstance(span, tuple):
        return span
    policy = _pinned_policy(ctx, span, clock)
    if not isinstance(policy, PolicySet):
        return policy
    effective = act_facts.effective(ctx.facts, clock)
    if isinstance(effective, Unavailable):
        return effective
    assert ctx.request is not None
    return decide(
        request=ctx.request,
        head_sha=ctx.head_sha,
        delivery=ctx.delivery,
        effective=effective,
        head_self_hashes=ctx.head_self_hashes,
        policy=policy,
    )
