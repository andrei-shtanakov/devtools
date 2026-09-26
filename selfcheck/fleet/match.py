"""Fleet channels: precise ``fleet`` edges and text mentions (spec §9.3, §9.4).

``apply_fleet`` is the one wiring both the probe and the fleet canary use;
it calls the channel functions through module globals so a broken channel
is visible to the canary (tests patch them).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence

from selfcheck.fleet.reader import FleetRepo
from selfcheck.graph.build import build_graph
from selfcheck.graph.commands import module_name
from selfcheck.graph.model import NON_EXEC, EdgeKind, Graph, NodeKind
from selfcheck.model import Location
from selfcheck.roles import role_of

_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_IN_TOKEN = re.compile(r"[A-Za-z0-9_./-]+\Z")
_STARTS = frozenset("./")
_ENDS = frozenset("./-")


def match_external(
    target: str, scope_name: str, scope_files: frozenset[str]
) -> list[str]:
    """Scope paths ``target`` reaches through any segment named like the scope
    repo (case-insensitive; every occurrence is tried — spec §9.3)."""
    parts = [p for p in posixpath.normpath(target).split("/") if p]
    name = scope_name.lower()
    hits = {
        "/".join(parts[i + 1 :])
        for i, part in enumerate(parts)
        if part.lower() == name and "/".join(parts[i + 1 :]) in scope_files
    }
    return sorted(hits)


def fleet_edges(
    repo: FleetRepo, scope_name: str, scope_files: frozenset[str]
) -> list[tuple[str, Location]]:
    """Executable launches in ``repo`` that land on ``scope_files``."""
    files = sorted(repo.texts)
    g = build_graph(
        files, repo.path, role_of, repo_name=repo.name, sched_dir=None, texts=repo.texts
    )
    out: list[tuple[str, Location]] = []
    for target, kind, where in g.external:
        if kind in NON_EXEC:
            continue
        for path in match_external(target, scope_name, scope_files):
            out.append((path, Location(f"{repo.name}:{where.path}", where.line)))
    return list(dict.fromkeys(out))


def node_keys(path: str) -> tuple[str, ...]:
    """Basename, repo path and (for ``.py``) dotted module name (spec §9.4)."""
    keys = [posixpath.basename(path), path, module_name(path)]
    return tuple(dict.fromkeys(k for k in keys if k))


def _token_hits(
    text: str, table: Mapping[str, Sequence[str]], longest: int
) -> set[str]:
    found: set[str] = set()
    for match in _TOKEN.finditer(text):
        token = match.group().lower()
        starts = [0] + [i + 1 for i, ch in enumerate(token) if ch in _STARTS]
        ends = [i for i, ch in enumerate(token) if ch in _ENDS] + [len(token)]
        for a in starts:
            for b in ends:
                if a < b <= a + longest:
                    found.update(table.get(token[a:b], ()))
    return found


def _odd_hits(text: str, odd: Mapping[str, re.Pattern[str]]) -> set[str]:
    return {anchor for anchor, pattern in odd.items() if pattern.search(text)}


def find_mentions(
    keys: Mapping[str, Sequence[str]], texts: Mapping[str, str], source: str
) -> dict[str, list[str]]:
    """anchor → ``["<source>:<rel>"]`` for every whole-word key occurrence:
    left not ``[A-Za-z0-9_-]``, right not ``[A-Za-z0-9_]``, any case. Overlapping
    keys are all found (a pass over aligned substrings of each token)."""
    table: dict[str, list[str]] = {}
    odd: dict[str, re.Pattern[str]] = {}
    for anchor, anchor_keys in keys.items():
        for key in anchor_keys:
            if _IN_TOKEN.match(key):
                table.setdefault(key.lower(), []).append(anchor)
            else:  # non-ASCII or spaces: a separate search, same boundaries
                odd[f"{anchor}\0{key}"] = re.compile(
                    rf"(?<![A-Za-z0-9_-]){re.escape(key)}(?![A-Za-z0-9_])",
                    re.IGNORECASE,
                )
    longest = max((len(k) for k in table), default=0)
    out: dict[str, set[str]] = {}
    for rel, text in texts.items():
        hits = _token_hits(text, table, longest) if table else set()
        hits |= {name.split("\0", 1)[0] for name in _odd_hits(text, odd)}
        for anchor in hits:
            out.setdefault(anchor, set()).add(f"{source}:{rel}")
    return {anchor: sorted(places) for anchor, places in sorted(out.items())}


def apply_fleet(g: Graph, repos: Sequence[FleetRepo], scope_name: str) -> None:
    """Add ``fleet`` edges and fleet mentions from ``repos`` to ``g``."""
    files = {n.path: a for a, n in g.nodes.items() if n.kind is NodeKind.FILE}
    keys = {anchor: node_keys(path) for path, anchor in files.items()}
    for repo in repos:
        for path, where in fleet_edges(repo, scope_name, frozenset(files)):
            g.add(path, EdgeKind.FLEET, where)
        for anchor, places in find_mentions(keys, repo.texts, repo.name).items():
            for place in places:
                g.mention(anchor, place)
