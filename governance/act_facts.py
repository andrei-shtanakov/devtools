"""Единственная точка импорта общего читателя фактов steward (спека D4, §5).

Остальные `act_*` модули видят только свои типы (`DeliveryView`,
`ReviewView`, `ActivationFact`, `HistoryBreak`) и `act_time.Unavailable`:
смена API steward меняет этот файл и ничего больше. steward импортируется
внутри функций (`_v3()`), чтобы модуль загружался без группы governance
(контракт `ci.yml:27`). Любое исключение из вызова библиотеки — «факт не
установлен» (инструкция steward, «Транспорт»), никогда не разрешение.
Второй реализации логики ревью здесь нет — только перекладка полей.

Транспорт выбирает вызывающий (правило steward §12 п. 1): факты акта —
`gh_transport` (GraphQL с токеном), история и активация — транспорт
источников доверия, который строит `load_trust` (Task 11c).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governance.act_decision import DeliveryView, ReviewView
from governance.act_policy import POLICY_FILES
from governance.act_source import SourceV2
from governance.act_time import Clock, EventKey, Unavailable, parse_ts

#: Канон меток steward (`YYYY-MM-DDTHH:MM:SSZ`).
STAMP = "%Y-%m-%dT%H:%M:%SZ"
_REVIEW_KEYS = ("review", "dismissal")


@dataclass(frozen=True)
class ActivationFact:
    """Активация источника: PR, merge-коммит, время мержа PR, sha256 файлов."""

    pr: int
    merge_commit: str
    merged_at: datetime
    files_sha256: Mapping[str, str]


@dataclass(frozen=True)
class HistoryBreak:
    """Установленное нарушение формы истории (steward `HistoryViolation`).

    `reason` — `"<code>: <detail>"`; коды в решении не различаются (§12 п. 5).
    """

    reason: str


def _v3() -> Any:
    from steward.approvalfacts import v3

    return v3


def _call[T](what: str, fn: Callable[[], T]) -> T | Unavailable:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — граница библиотеки (инструкция steward)
        return Unavailable(f"steward {what}: {type(exc).__name__}: {exc}")


def _own(got: Any) -> Any:
    """`v3.Unavailable` → `act_time.Unavailable` с той же причиной; прочее — как есть."""
    if isinstance(got, _v3().Unavailable):
        return Unavailable(got.reason)
    return got


def gh_transport(gh_config_dir: Path) -> Any:
    """Транспорт фактов акта: `gh api` под профилем `gh_config_dir` (О10)."""
    return _v3().GhTransport(gh_config_dir, timeout=60)


def _delivery(d: Any) -> DeliveryView:
    return DeliveryView(
        merged=d.merged,
        merge_commit=d.merge_commit,
        merged_at=d.merged_at,
        merged_by_login=d.merged_by_login,
        merged_by_type_hint=d.merged_by_type_hint,
        head_at_merge=d.head_at_merge,
        in_base=d.in_base,
        blobs={p.path: (p.blob_at_merge, p.blob_at_head) for p in d.paths},
    )


def read(
    transport: Any, repo: str, pr: int, paths: Sequence[str]
) -> tuple[DeliveryView, Any | None] | Unavailable:
    """Доставка и факты ревью на `T = merged_at` (steward §3.3, спека §5.5).

    Не смёржен — `(DeliveryView(merged=False, …), None)`: ревью не читались,
    предварительный результат для допуска не переиспользуется.
    """
    v3 = _v3()
    got = _own(
        _call(
            "read_act_facts",
            lambda: v3.read_act_facts(transport, repo, pr, paths=tuple(paths)),
        )
    )
    if isinstance(got, Unavailable):
        return got
    delivery, facts = got
    return _delivery(delivery), facts


def open_review(transport: Any, repo: str, pr: int) -> Any | Unavailable:
    """Факты ревью открытого PR (два прохода §3.4); решение по ним не окончательно."""
    v3 = _v3()
    return _own(
        _call("read_open_review", lambda: v3.read_open_review(transport, repo, pr))
    )


def effective(facts: Any, clock: Clock) -> dict[str, ReviewView] | Unavailable:
    """Эффективные ревью на `clock` (steward §3.5); ключ — `review.login`."""
    v3 = _v3()
    # Clock принимает любую aware-зону; steward ждёт UTC-метку — приводим, а не клеймим `Z`.
    stamp = clock.t.astimezone(UTC).strftime(STAMP)
    before = frozenset(k for k in clock.before if k[0] in _REVIEW_KEYS)
    got = _own(
        _call(
            "effective_reviews",
            lambda: v3.effective_reviews(facts, stamp, before=before),
        )
    )
    if isinstance(got, Unavailable):
        return got
    return {
        r.login: ReviewView(r.review_id, r.login, r.type_hint, r.state, r.commit_id)
        for r in got.values()
    }


def event_stamps(facts: Any) -> dict[EventKey, datetime]:
    """Метки событий ревью и отзывов — для группы равных меток (§8.2)."""
    stamps: dict[EventKey, datetime] = {}
    for r in facts.reviews:
        if r.submitted_at is not None:
            stamps[("review", r.review_id)] = parse_ts(r.submitted_at)
    for d in facts.dismissals:
        stamps[("dismissal", d.review_id)] = parse_ts(d.created_at)
    return stamps


def review_requires(facts: Any) -> dict[EventKey, frozenset[EventKey]]:
    """Отправка ревью предшествует его отзыву (§8.2 п. 2)."""
    return {
        ("dismissal", d.review_id): frozenset({("review", d.review_id)})
        for d in facts.dismissals
    }


def _activation(a: Any) -> ActivationFact:
    return ActivationFact(
        a.pr, a.merge_commit, parse_ts(a.merged_at), dict(a.files_sha256)
    )


def history(
    transport: Any,
    source: SourceV2,
    anchor: str,
    until: str | None,
    *,
    head: str | None = None,
) -> tuple[ActivationFact, ...] | HistoryBreak | Unavailable:
    """Активации ветки источника `anchor…until` по first-parent (steward §3.7).

    Транспорт — источников доверия, который строит `load_trust` (Task 11c); ветка
    обязана быть default branch
    источника (О11), иначе `Unavailable: branch_not_default`.

    `head` — закреплённый издателем P (спека установщика §6.1): открытая эпоха
    (`until is None`) читается до P по SHA, а не до текущей головы ветки, и
    откат политики после P исход не меняет. Закрытая эпоха читается до своего
    `until`; без `head` поведение 2b — до текущей головы.
    """
    v3 = _v3()
    stop = until if until is not None else head
    got = _own(
        _call(
            "read_policy_history",
            lambda: v3.read_policy_history(
                transport,
                source.repo,
                source.branch,
                anchor=anchor,
                until=stop,
                files=POLICY_FILES,
            ),
        )
    )
    if isinstance(got, Unavailable):
        return got
    if isinstance(got, v3.HistoryViolation):
        return HistoryBreak(got.reason)
    return tuple(_activation(a) for a in got)


def activation(
    transport: Any, source: SourceV2, merge_commit: str
) -> ActivationFact | Unavailable:
    """Факт одной активации (якорь эпохи); ветку не проверяет — это делает история."""
    v3 = _v3()
    got = _own(
        _call(
            "read_activation",
            lambda: v3.read_activation(
                transport, source.repo, merge_commit, files=POLICY_FILES
            ),
        )
    )
    if isinstance(got, Unavailable):
        return got
    return _activation(got)
