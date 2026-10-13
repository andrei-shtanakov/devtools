"""Журнал эпох (спека §6.3): форма и единственная допустимая мутация."""

from __future__ import annotations

import dataclasses

import pytest

from governance.act_epochs import Epoch, JournalError, check_transition, parse_journal

A1 = "a" * 40
A2 = "b" * 40
LC = "c" * 40
REASON_LINE = '    closed_reason: "агенту выдана запись, devtools#000"\n'

ONE_OPEN = f"""\
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: {A1}
"""

CLOSED_AND_OPEN = f"""\
schema: approval-policy-epochs/v1
epochs:
  - epoch: 1
    anchor: {A1}
    last_confirmed: {LC}
    valid_until: "2026-10-20T10:00:00Z"
{REASON_LINE}  - epoch: 2
    anchor: {A2}
"""


def test_parses_open_journal() -> None:
    (only,) = parse_journal(ONE_OPEN)
    assert only == Epoch(1, A1, None, None, None)
    assert not only.closed


def test_parses_closed_then_open() -> None:
    first, second = parse_journal(CLOSED_AND_OPEN)
    assert first.closed and not second.closed


@pytest.mark.parametrize(
    "text",
    [
        "schema: approval-policy-epochs/v1\nepochs: []\n",
        ONE_OPEN.replace("epoch: 1", "epoch: 2"),
        CLOSED_AND_OPEN.replace(REASON_LINE, ""),
        CLOSED_AND_OPEN + f"  - epoch: 3\n    anchor: {A1}\n",
        ONE_OPEN.replace("approval-policy-epochs/v1", "approval-policy-epochs/v9"),
        ONE_OPEN.replace("epoch: 1", "epoch: true"),
        ONE_OPEN.replace("epoch: 1", "epoch: 1.0"),
    ],
    ids=[
        "empty",
        "number-not-position",
        "half-closed",
        "two-open",
        "unknown-schema",
        "bool-number",
        "float-number",
    ],
)
def test_refuses_malformed(text: str) -> None:
    with pytest.raises(JournalError):
        parse_journal(text)


def test_unquoted_timestamp_gets_specific_message() -> None:
    text = CLOSED_AND_OPEN.replace('"2026-10-20T10:00:00Z"', "2026-10-20T10:00:00Z")
    with pytest.raises(JournalError, match="quoted ISO"):
        parse_journal(text)


def test_atomic_close_and_append_allowed() -> None:
    check_transition(parse_journal(ONE_OPEN), parse_journal(CLOSED_AND_OPEN))


def test_identity_transition_allowed() -> None:
    journal = parse_journal(CLOSED_AND_OPEN)
    check_transition(journal, journal)


def test_editing_closed_epoch_refused() -> None:
    prev = parse_journal(CLOSED_AND_OPEN)
    edited = (dataclasses.replace(prev[0], closed_reason="переписано"), prev[1])
    with pytest.raises(JournalError):
        check_transition(prev, edited)


def test_moving_anchor_refused() -> None:
    prev = parse_journal(ONE_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, (dataclasses.replace(prev[0], anchor=A2),))


def test_deleting_epoch_refused() -> None:
    prev = parse_journal(CLOSED_AND_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, prev[:1])


def test_append_without_closing_refused() -> None:
    prev = parse_journal(ONE_OPEN)
    with pytest.raises(JournalError):
        check_transition(prev, (prev[0], Epoch(2, A2, None, None, None)))


@pytest.mark.parametrize(
    "value",
    ['"not a date"', '"2026-10-20"', '"2026-10-20T10:00:00"', '"2026-13-01T00:00Z"'],
    ids=["text", "date-only", "naive", "bad-month"],
)
def test_valid_until_must_be_aware_iso_timestamp(value: str) -> None:
    """`valid_until` проверяет журнал, а не каждый потребитель по-своему."""
    text = CLOSED_AND_OPEN.replace('"2026-10-20T10:00:00Z"', value)
    with pytest.raises(JournalError, match="valid_until"):
        parse_journal(text)


def test_transition_from_empty_journal_is_journal_error() -> None:
    """Предусловие — разобранный журнал; пустой prev — JournalError, не IndexError."""
    with pytest.raises(JournalError):
        check_transition((), parse_journal(ONE_OPEN))
