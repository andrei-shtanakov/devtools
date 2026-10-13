"""Журнал эпох доверия `approval-policy-source/v2/epochs.yaml` (спека §6.3).

Порядок эпох — позиция в списке; номер обязан ей равняться. Все эпохи,
кроме последней, закрыты (заполнены `last_confirmed`, `valid_until`,
`closed_reason`), последняя открыта. Единственная допустимая мутация —
атомарный переход: закрыть последнюю открытую и добавить следующую.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, replace

import yaml

from governance.act_time import parse_ts

SCHEMA = "approval-policy-epochs/v1"
_CLOSING = ("last_confirmed", "valid_until", "closed_reason")


class JournalError(ValueError):
    """Журнал не читается или переход недопустим → `unavailable`."""


@dataclass(frozen=True)
class Epoch:
    """Одна эпоха доверия."""

    epoch: int
    anchor: str
    last_confirmed: str | None = None
    valid_until: str | None = None
    closed_reason: str | None = None

    @property
    def closed(self) -> bool:
        """Эпоха закрыта человеком."""
        return self.last_confirmed is not None


def _closing_values(raw: dict[str, object], position: int) -> list[str | None]:
    values = [raw.get(key) for key in _CLOSING]
    for key, value in zip(_CLOSING, values, strict=True):
        if isinstance(value, (datetime.datetime, datetime.date)):
            raise JournalError(
                f"эпоха #{position}: {key} — YAML разобрал его как время; "
                "closing timestamps must be quoted ISO strings"
            )
    if any(v is not None for v in values) and not all(
        isinstance(v, str) and v for v in values
    ):
        raise JournalError(f"эпоха #{position}: закрыта частично")
    valid_until = values[_CLOSING.index("valid_until")]
    if isinstance(valid_until, str):
        try:
            parse_ts(valid_until)
        except ValueError as exc:
            raise JournalError(
                f"эпоха #{position}: valid_until {valid_until!r} — "
                "не ISO-метка с часовым поясом"
            ) from exc
    return [v if isinstance(v, str) else None for v in values]


def _epoch(raw: object, position: int) -> Epoch:
    if not isinstance(raw, dict) or set(raw) - {"epoch", "anchor", *_CLOSING}:
        raise JournalError(f"эпоха #{position}: неизвестная форма {raw!r}")
    number = raw.get("epoch")
    if type(number) is not int or number != position:
        raise JournalError(f"эпоха #{position}: номер {number!r} ≠ позиции (int)")
    anchor = raw.get("anchor")
    if not isinstance(anchor, str) or len(anchor) != 40:
        raise JournalError(f"эпоха #{position}: anchor обязан быть полным SHA")
    last_confirmed, valid_until, closed_reason = _closing_values(raw, position)
    return Epoch(position, anchor, last_confirmed, valid_until, closed_reason)


def parse_journal(text: str) -> tuple[Epoch, ...]:
    """Разбор и проверка формы журнала."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise JournalError(f"журнал не разобран как YAML: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise JournalError(f"схема журнала не {SCHEMA}")
    raw = data.get("epochs")
    if not isinstance(raw, list) or not raw:
        raise JournalError("журнал без эпох")
    epochs = tuple(_epoch(item, i) for i, item in enumerate(raw, start=1))
    if any(not e.closed for e in epochs[:-1]) or epochs[-1].closed:
        raise JournalError("открытой обязана быть ровно последняя эпоха")
    return epochs


def check_transition(prev: tuple[Epoch, ...], new: tuple[Epoch, ...]) -> None:
    """Сверка новой версии журнала с прежней (CI и каждый потребитель).

    Предусловие: обе версии — результат `parse_journal` (непусты, форма
    проверена); нарушение предусловия — `JournalError`, не падение.
    """
    if not prev or not new:
        raise JournalError("сверка перехода: журнал без эпох")
    if new == prev:
        return
    if len(new) != len(prev) + 1 or new[: len(prev) - 1] != prev[:-1]:
        raise JournalError("допустимо только закрытие последней эпохи + новая эпоха")
    closed, last = new[len(prev) - 1], prev[-1]
    reopened = replace(
        closed, last_confirmed=None, valid_until=None, closed_reason=None
    )
    if reopened != last or not closed.closed:
        raise JournalError("закрытие обязано менять только поля закрытия")
    if new[-1].closed:
        raise JournalError("добавленная эпоха обязана быть открытой")
