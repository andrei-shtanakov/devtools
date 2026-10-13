"""Решение об акте и доставке: D1–D3 на срезе T (спека §3, §8).

Чистая функция: факты уже нормализованы на текущем `Clock` (steward
`effective_reviews`), политика — набор на пине заявки, проверенный
вызывающим на совпадение с активацией на T. Здесь нет чтений форджи.
Импорт steward — только внутри `act_policy.classify`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from governance.act_policy import PolicySet, classify, same_login
from governance.act_request import NodeRequest
from governance.act_time import Unavailable

D1_NO_ACT = "d1_no_act"
HEAD_MOVED = "head_moved"
NOT_IN_BASE = "not_in_base"
I_BYTES = "i_bytes"
I_CONTENT = "i_content"
D3_MERGER = "d3_merger"


@dataclass(frozen=True)
class ReviewView:
    """Эффективное ревью логина на T (восстановленное состояние)."""

    review_id: str
    login: str
    type_hint: str
    state: str
    commit_id: str


@dataclass(frozen=True)
class DeliveryView:
    """Доставка: факты мержа и пары blob (merge, head) по путям."""

    merged: bool
    merge_commit: str | None
    merged_at: str | None
    merged_by_login: str | None
    merged_by_type_hint: str | None
    head_at_merge: str | None
    in_base: bool
    blobs: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class Accepted:
    """Акт действителен, доставка засчитана; approve — свидетельство."""

    merge_commit: str
    merged_by: str
    review_id: str = field(default="", compare=False)
    login: str = field(default="", compare=False)


@dataclass(frozen=True)
class Rejected:
    """Установленный отказ. Вердикт — сам факт отказа; `code` и `detail` —
    свидетельства (разные причины отказа в разных порядках §8.2 — один
    вердикт «отказ», не неоднозначность). Ветвиться по `code` можно только
    после `resolve`."""

    code: str = field(compare=False)
    detail: str = field(default="", compare=False)


@dataclass(frozen=True)
class WaitingDelivery:
    """PR не смёржен — ничего не продвигается."""


Decision = Accepted | Rejected | WaitingDelivery | Unavailable

_DELIVERY_FACTS = ("merge_commit", "merged_at", "merged_by_login", "head_at_merge")


def _missing_fact(d: DeliveryView, effective: Mapping[str, ReviewView]) -> str | None:
    """Первый неустановленный факт (§5.5) либо `None`."""
    for name in _DELIVERY_FACTS:
        if getattr(d, name) is None:
            return f"доставка: нет {name}"
    for key, review in effective.items():
        if key != review.login:
            return f"ревью под ключом {key!r} принадлежит {review.login!r}"
    return None


def _act(
    head_sha: str, effective: Mapping[str, ReviewView], policy: PolicySet
) -> ReviewView | None:
    """Действительный approve человека на head (бот не human — в classify).

    Полномочия проверяются у логина самого ревью, которое станет актом.
    """
    for review in sorted(effective.values(), key=lambda r: r.login):
        if (
            review.state == "APPROVED"
            and review.commit_id == head_sha
            and review.login in policy.approvers
            and classify(policy, review.login, review.type_hint) == "human"
        ):
            return review
    return None


def _merger_ok(policy: PolicySet, login: str, hint: str | None) -> bool:
    actor = classify(policy, login, hint)
    if actor == "agent":
        return any(same_login(login, m) for m in policy.mergers)
    return actor == "human" and login in policy.approvers


def _delivery_refusal(
    request: NodeRequest,
    head_sha: str,
    d: DeliveryView,
    self_hashes: Mapping[str, str],
) -> Rejected | Unavailable | None:
    if d.head_at_merge != head_sha:
        return Rejected(HEAD_MOVED, f"голова при мерже {d.head_at_merge} ≠ {head_sha}")
    if not d.in_base:
        return Rejected(NOT_IN_BASE, "merge-коммит недостижим из базы")
    for entry in request.nodes.values():
        if entry.path not in d.blobs:
            return Unavailable(f"{entry.path}: пара blob не прочитана")
        if entry.path not in self_hashes:
            return Unavailable(f"{entry.path}: хеш проекции не вычислен")
        at_merge, at_head = d.blobs[entry.path]
        if at_merge != at_head:
            return Rejected(I_BYTES, f"{entry.path} доставлен не теми байтами")
        if self_hashes[entry.path] != entry.content_hash:
            return Rejected(I_CONTENT, f"{entry.path} не соответствует заявке")
    return None


def decide(
    *,
    request: NodeRequest,
    head_sha: str,
    delivery: DeliveryView,
    effective: Mapping[str, ReviewView],
    head_self_hashes: Mapping[str, str],
    policy: PolicySet,
) -> Decision:
    """D1–D3 по одному срезу; порядок — доставка, акт, байты, мержер.

    Неустановленный обязательный факт — `Unavailable`, никогда не отказ.
    """
    if not delivery.merged:
        return WaitingDelivery()
    missing = _missing_fact(delivery, effective)
    if missing is not None:
        return Unavailable(missing)
    act = _act(head_sha, effective, policy)
    if act is None:
        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")
    refusal = _delivery_refusal(request, head_sha, delivery, head_self_hashes)
    if refusal is not None:
        return refusal
    login, commit = delivery.merged_by_login, delivery.merge_commit
    if login is None or commit is None:  # сужение: проверено в _missing_fact
        return Unavailable("доставка: нет мержера или merge-коммита")
    if not _merger_ok(policy, login, delivery.merged_by_type_hint):
        return Rejected(D3_MERGER, f"мержер {login} не имеет права доставки")
    return Accepted(commit, login, act.review_id, act.login)
