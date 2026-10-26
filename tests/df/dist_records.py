"""Native files and rule (в) problems by the distribution that installed
them (Task 14-B inventory): data only, never a verdict.

The owner of a file is the `*.dist-info` whose `RECORD` lists it; a file no
`RECORD` lists is `NO_RECORD`. A problem line of `boundary._NativeCheck` is
`<rel>: <reason>`; its file is the longest native path it starts with, and
its reason class is the reason with the variable parts (the dependency, the
search path, the file paths) replaced by `*`.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

#: The owner of a file that no `RECORD` lists.
NO_RECORD = "<no RECORD>"
#: The file of a problem line that starts with no native path.
NO_FILE = "<no file>"
#: Variable parts of a rule (в) reason, in order: dependency, search path,
#: then anything path-like left.
_CLASS_SUBS = (
    (re.compile(r"^зависимость [^:]+: "), "зависимость *: "),
    (re.compile(r"^путь поиска '[^']*': "), "путь поиска *: "),
    (re.compile(r"(?:@rpath|@loader_path|\$\{?ORIGIN\}?|/)[^\s:,;)]*"), "*"),
)


def record_owners(site: Path) -> dict[str, str]:
    """Each path listed by a `RECORD` under `site` (relative to `site`,
    normalised) mapped to its distribution (the `dist-info` name without
    the version)."""
    owners: dict[str, str] = {}
    for info in sorted(site.glob("*.dist-info")):
        record = info / "RECORD"
        if not record.is_file():
            continue
        dist = info.name.removesuffix(".dist-info").rsplit("-", 1)[0]
        with open(record, newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if row and row[0]:
                    owners[str(PurePosixPath(_normal(row[0])))] = dist
    return owners


def by_distribution(
    natives: Iterable[str], problems: Iterable[str], owners: Mapping[str, str]
) -> dict[str, dict[str, Any]]:
    """Per distribution: its native files, its problem lines and their
    distinct reason classes."""
    files = sorted(natives)
    out: dict[str, dict[str, Any]] = {}

    def entry(dist: str) -> dict[str, Any]:
        return out.setdefault(dist, {"natives": [], "problems": [], "classes": []})

    for rel in files:
        entry(owners.get(rel, NO_RECORD))["natives"].append(rel)
    for line in problems:
        rel = max((f for f in files if line.startswith(f"{f}: ")), key=len, default="")
        dist = owners.get(rel, NO_RECORD) if rel else NO_FILE
        item = entry(dist)
        item["problems"].append(line)
        reason = reason_class(line.removeprefix(f"{rel}: ") if rel else line)
        if reason not in item["classes"]:
            item["classes"].append(reason)
    for item in out.values():
        item["classes"].sort()
    return out


def reason_class(reason: str) -> str:
    """`reason` with its variable parts replaced by `*`."""
    for pattern, repl in _CLASS_SUBS:
        reason = pattern.sub(repl, reason)
    return reason


def _normal(path: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)
