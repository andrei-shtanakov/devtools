"""Entry file of the `darkfactory` command (spec 3.9).

Placeholder of Task 8a part 1: only `entry_text` and the `_ROOT` line it
substitutes. Task 8a part 2 writes the entry itself into this module. The
module imports nothing but stdlib: its text runs under the admin Python with
`-I -B -S` before any `df` code is trusted.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from df.paths import Layout

#: The layout root of the installed entry; `entry_text` substitutes this line.
_ROOT = "/"
_ROOT_LINE = '_ROOT = "/"\n'


def entry_text(layout: Layout) -> str:
    """The text of `bin/darkfactory-entry.py` for `layout` (F14).

    This module's own source with its single `_ROOT` line set to the layout
    root; not exactly one such line is an `AssertionError`.
    """
    lines = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines(True)
    found = [i for i, line in enumerate(lines) if line == _ROOT_LINE]
    if len(found) != 1:
        raise AssertionError(f"entry text: {len(found)} lines {_ROOT_LINE!r}")
    lines[found[0]] = f"_ROOT = {str(layout.root)!r}\n"
    return "".join(lines)
