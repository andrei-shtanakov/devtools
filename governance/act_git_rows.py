"""Разбор first-parent истории `git log` (узкий модуль: только stdlib).

Вынесен из `governance.ops`, чтобы адаптер истории precheck не тянул `ops`;
`ops` сохраняет прежние имена как синонимы.
"""

from __future__ import annotations

FirstParentRows = list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]

FIRST_PARENT_LOG = (
    "log",
    "--first-parent",
    "--reverse",
    "--diff-merges=first-parent",
    "--no-renames",
    "--name-status",
    "--format=%x00%H %P",
)


def parse_first_parent(stdout: str) -> FirstParentRows:
    """Разбор `git log --first-parent --name-status --format=%x00%H %P`."""
    rows: FirstParentRows = []
    for chunk in stdout.split("\x00")[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        commit, *parents = lines[0].split()
        changes = tuple(
            (ln.split("\t", 1)[0], ln.split("\t", 1)[1])
            for ln in lines[1:]
            if "\t" in ln
        )
        rows.append((commit, tuple(parents), changes))
    return rows
