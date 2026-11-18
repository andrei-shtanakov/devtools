"""Тестовые утилиты английского режима личного пути `dfl`."""

from __future__ import annotations

import re
from collections.abc import Iterable

_CYRILLIC = re.compile("[А-Яа-яЁё]")


def cyrillic(text: str, allowed: Iterable[str] = ()) -> list[str]:
    """Строки `text` с кириллицей после вычёркивания `allowed`."""
    for fragment in allowed:
        text = text.replace(fragment, "")
    return [line for line in text.splitlines() if _CYRILLIC.search(line)]


#: Ответ интервьюера с брифом (JSON) — та же форма, что у русского агента.
EN_BRIEF_TURN = """\
{"brief": {
  "goals": [{"id": "G-01", "title": "Notes at hand",
             "text": "I can jot a note and find it again in a second."}],
  "functions": [
    {"id": "FR-01", "title": "Add a note", "goals": ["G-01"],
     "criteria": ["`notes add <text>` appends a line and prints \\"added\\""],
     "words": "just type it and forget"},
    {"id": "FR-02", "title": "Find notes", "goals": ["G-01"],
     "criteria": ["`notes find <word>` prints only matching lines"],
     "words": "find it by a word"}
  ],
  "context": {"users": "only me", "auth": "none", "network": "none",
              "local": "writes ~/.notes.txt", "platform": "macOS"},
  "open_items": ["sync between machines"]
}}"""

#: Дизайн — та же грамматика, что `tests/fixtures/personal/20-design.md`.
EN_DESIGN = """\
## Structure

One module `notes.py` with an `argparse` CLI: subcommands `add` and `find`.
Data is a text file `~/.notes.txt`, one note per line.

#### D-01: Storage — a line in `~/.notes.txt`
`add` opens the file for appending and prints "added". No network and no
authorization (only me, macOS).
**Functions:** FR-01

#### D-02: Search — a line filter by substring
`find` reads the file and prints lines containing the word; no file — empty.
**Functions:** FR-02
"""

#: Декомпозиция — та же грамматика, что `tests/fixtures/personal/30-decomposition.md`.
EN_DECOMPOSITION = """\
## Checks

#### BEH-01: add appends a note
`notes add buy bread` appends a line to the notes file and prints "added".
- **checked_by**: `kind: pytest` `target: tests/test_add.py::test_add_appends_line`
**Criteria:** FR-01/1

#### BEH-02: find prints matches
With notes "buy bread" and "call mom", `notes find bread` prints only the first.
- **checked_by**: `kind: pytest` `target: tests/test_find.py::test_find_prints_matches`
**Criteria:** FR-02/1

## Tasks

#### DT-01: The add command · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo
**Design decisions:** D-01
CLI `notes.py` with the `add` subcommand, appending to `~/.notes.txt`.

#### DT-02: The find command · type: implement · owner: dev
scenarios: [BEH-02]
depends_on: [DT-01]
parallel_group: solo
**Design decisions:** D-02
The `find` subcommand: a line filter over the notes file.
"""
