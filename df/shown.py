"""Text another party wrote, made inert before it enters a line root prints.

`doctor` and `setup-trust` print what the publisher answered: reasons,
finding details, event reasons -- some written by a role (`record-violation`
from `df-exec`), some derived from GitHub. A line break would forge a line
(«ok» under a heading), an ESC or C1 control a terminal sequence, a bidi or
format character a reordered or hidden text. `inert` shows each of them as a
visible escape, so one printed line is exactly one line of what it says.
"""

from __future__ import annotations

import unicodedata

#: Categories shown escaped: controls (C0, DEL, C1), format characters
#: (bidi embeddings and isolates, zero-width, BOM), line and paragraph
#: separators, surrogates, private use and unassigned code points.
ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})


def inert(text: str) -> str:
    """`text` with every character of `ESCAPED_CATEGORIES` -- the tab, CR
    and LF included -- written as a visible escape (`\\xNN`, `\\uNNNN`,
    `\\UNNNNNNNN`); everything else unchanged."""
    return "".join(_escape(c) if _odd(c) else c for c in text)


def inert_lines(lines: list[str]) -> str:
    """The lines joined by LF, each made `inert` first: the only line breaks
    of the result are the joins."""
    return "\n".join(inert(line) for line in lines)


def _odd(char: str) -> bool:
    return unicodedata.category(char) in ESCAPED_CATEGORIES


def _escape(char: str) -> str:
    code = ord(char)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"
