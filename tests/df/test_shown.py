"""`df.shown.inert`: text another party wrote, inert in a printed line
(Task 13 re-review N-1)."""

from __future__ import annotations

import pytest

from df.shown import inert, inert_lines

EVIL = "x\n  trust.status: ok\r\x1b[2K‮ko\x9b ​\t"


def test_inert_escapes_every_control_bidi_and_format_char() -> None:
    assert inert(EVIL) == (
        "x\\x0a  trust.status: ok\\x0d\\x1b[2K\\u202eko\\x9b\\u2028\\u200b\\x09"
    )


@pytest.mark.parametrize("char", ["\x00", "\x7f", "\x85", "⁦", "﻿", "\U000e0001"])
def test_inert_leaves_no_raw_odd_char(char: str) -> None:
    out = inert(f"a{char}b")
    assert char not in out and out.startswith("a\\") and out.endswith("b")


def test_inert_keeps_ordinary_text() -> None:
    text = "уровень 3: не достигнут — trust.status: нарушено; «ok» 50 % ✓"
    assert inert(text) == text


def test_inert_lines_breaks_only_at_joins() -> None:
    assert inert_lines(["a\nb", "c"]).splitlines() == ["a\\x0ab", "c"]
