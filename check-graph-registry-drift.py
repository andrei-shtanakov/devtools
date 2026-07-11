#!/usr/bin/env python3
"""graph-vs-registry drift — READ-ONLY check: prograph graph vs integration map.

Compares two views of cross-project structure:

  * authored — the human-curated integration map in
    `prograph-vault/authored/registry/registry.md` (section "## Integration map");
  * derived — alive edges of the latest prograph snapshot (`.prograph/graph.db`).

and reports the diff in both directions:

  * pair in the registry but not in the graph  → UNDETECTED integration
    (often a file-based integration the prograph detectors cannot see —
    candidate for a declared edge, see prograph/TODO.md "Declared edges");
  * pair in the graph but not in the registry  → UNDOCUMENTED integration
    (update the integration map);
  * a project listed under "Not yet connected (0 graph edges)" that DOES have
    edges → stale registry claim.

Known-acceptable pairs live in `graph-registry-allowlist.toml` next to this
script (fnmatch globs, each with a reason) — same pattern as the other
drift/conformance checkers in this directory.

Exit 0 — no drift. Exit 1 — at least one finding. Nothing is written.

Usage:
    python3 check-graph-registry-drift.py             # autodetect workspace root
    python3 check-graph-registry-drift.py --root /path/to/all_ai_orchestrators

Parser scope (deliberately simple; the registry is prose):
  * `A → B` / `A -> B` / `A ↔ B` pairs anywhere in an Integration-map bullet,
    with an optional canonical alias after the target: `B-long-name (b-alias)`;
  * contract cliques: `owners: A, B, C` (+ `consumer: D`) and `A + B` — all
    named projects of one bullet are pairwise connected via the shared contract;
  * `X internal sub-package graph (a, b, c)` — a COVERED group: graph edges
    inside {X, a, b, c} count as documented, but the registry does not claim
    any specific pair exists (the map says "there is an internal graph", not
    "all pairs exist");
  * the "Not yet connected (0 graph edges):" name list.
Anything the parser misses belongs in the allowlist with a reason, not in
smarter regexes.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sqlite3
import sys
import tomllib
from itertools import combinations
from pathlib import Path

REGISTRY_REL = "prograph-vault/authored/registry/registry.md"
GRAPH_DB_REL = ".prograph/graph.db"
ALLOWLIST = Path(__file__).with_name("graph-registry-allowlist.toml")

ARROW_RE = re.compile(
    r"(?P<a>[A-Za-z0-9_][A-Za-z0-9_.-]*)"
    r"\s*(?:→|->|↔)\s*"
    r"(?P<b>[A-Za-z0-9_][A-Za-z0-9_.-]*)"
    r"(?:\s*\((?P<balias>[A-Za-z0-9_.-]+)\))?"
)
OWNERS_RE = re.compile(r"owners?:\s*([^.;]+)")
CONSUMERS_RE = re.compile(r"consumers?:\s*([^.;]+)")
PLUS_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*\+\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)")
COVERED_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.-]*) internal sub-package graph \(([^)]+)\)")
NOT_CONNECTED_RE = re.compile(r"Not yet connected \(0 graph edges\):\**\s*([^.]+)")

Pair = frozenset  # of two project names


def _clean(name: str) -> str:
    """Trim markdown/punctuation residue around a project name (`x`, *x*, x.)."""
    return name.strip().strip("`*").rstrip(".")


def _names(csv: str) -> list[str]:
    return [c for n in csv.split(",") if (c := _clean(n))]


def parse_registry(md: str) -> tuple[set[Pair], list[set[str]], set[str]]:
    """Return (pairs, covered-groups, projects-declared-unconnected)."""
    lines = md.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("## Integration map"))
    except StopIteration:
        sys.exit(f"error: no '## Integration map' section in {REGISTRY_REL}")
    section: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        section.append(ln)

    pairs: set[Pair] = set()
    covered: list[set[str]] = []
    unconnected: set[str] = set()
    for ln in section:
        m = NOT_CONNECTED_RE.search(ln)
        if m:
            unconnected.update(_names(m.group(1)))
            continue
        km = COVERED_RE.search(ln)
        if km:
            covered.append({km.group(1), *_names(km.group(2))})
            continue
        for am in ARROW_RE.finditer(ln):
            a = _clean(am.group("a"))
            b = _clean(am.group("balias") or am.group("b"))
            if a and b and a != b:
                pairs.add(Pair((a, b)))
        for pm in PLUS_RE.finditer(ln):
            a, b = _clean(pm.group(1)), _clean(pm.group(2))
            if a and b and a != b:
                pairs.add(Pair((a, b)))
        clique: list[str] = []
        for rx in (OWNERS_RE, CONSUMERS_RE):
            cm = rx.search(ln)
            if cm:
                clique.extend(_names(cm.group(1)))
        for a, b in combinations(dict.fromkeys(clique), 2):  # dedup, keep order
            pairs.add(Pair((a, b)))
    return pairs, covered, unconnected


def graph_pairs(db_path: Path) -> set[Pair]:
    """Unordered project pairs connected in the latest snapshot.

    Direct edges (package_dep, mcp_call) connect their endpoints; contract_link
    edges connect all co-owners of one logical contract (grouped by declared_id,
    falling back to content_hash) — mirroring how the registry describes
    'shared contracts'.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        (latest,) = con.execute("SELECT MAX(id) FROM snapshots").fetchone()
        if latest is None:
            sys.exit(f"error: no snapshots in {db_path}")
        name = dict(
            con.execute("SELECT id, name FROM projects WHERE last_seen = ?", (latest,))
        )
        pairs: set[Pair] = set()
        rows = con.execute(
            "SELECT kind, from_kind, from_id, to_kind, to_id FROM edges WHERE last_seen = ?",
            (latest,),
        ).fetchall()
        contract_owners: dict[str, set[str]] = {}
        contract_key = dict(
            con.execute("SELECT id, COALESCE(declared_id, content_hash) FROM contracts")
        )
        for kind, fk, fid, tk, tid in rows:
            if fk == "project" and tk == "project":
                a, b = name.get(fid), name.get(tid)
                if a and b and a != b:
                    pairs.add(Pair((a, b)))
            elif fk == "project" and tk == "contract":
                key = contract_key.get(tid)
                owner = name.get(fid)
                if key and owner:
                    contract_owners.setdefault(key, set()).add(owner)
        for owners in contract_owners.values():
            for a, b in combinations(sorted(owners), 2):
                pairs.add(Pair((a, b)))
        return pairs
    finally:
        con.close()


def autodetect_root() -> Path:
    """Walk upward from this script until a dir holds both required artifacts."""
    for base in Path(__file__).resolve().parents:
        if (base / REGISTRY_REL).is_file() and (base / GRAPH_DB_REL).is_file():
            return base
    sys.exit(
        f"error: could not autodetect workspace root — no ancestor of "
        f"{Path(__file__).resolve().parent} holds both {REGISTRY_REL} and "
        f"{GRAPH_DB_REL}; pass --root explicitly"
    )


def load_allowlist() -> list[tuple[str, str, str]]:
    if not ALLOWLIST.is_file():
        return []
    data = tomllib.loads(ALLOWLIST.read_text(encoding="utf-8"))
    rules: list[tuple[str, str, str]] = []
    for e in data.get("allow", []):
        reason = e.get("reason", "").strip()
        if not reason:
            sys.exit(
                f"error: allowlist entry {e['a']} / {e['b']} in {ALLOWLIST.name} "
                f"has no reason (every entry must carry one)"
            )
        rules.append((e["a"], e["b"], reason))
    return rules


def allowed(pair: Pair, rules: list[tuple[str, str, str]]) -> str | None:
    x, y = sorted(pair)
    for pa, pb, reason in rules:
        if (fnmatch.fnmatch(x, pa) and fnmatch.fnmatch(y, pb)) or (
            fnmatch.fnmatch(y, pa) and fnmatch.fnmatch(x, pb)
        ):
            return reason or "(allowlisted)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None, help="workspace root")
    args = ap.parse_args()
    root = args.root.resolve() if args.root else autodetect_root()

    registry_path = root / REGISTRY_REL
    db_path = root / GRAPH_DB_REL
    for p in (registry_path, db_path):
        if not p.is_file():
            sys.exit(f"error: {p} not found (wrong --root?)")

    reg_pairs, covered_groups, reg_unconnected = parse_registry(
        registry_path.read_text(encoding="utf-8")
    )
    g_pairs = graph_pairs(db_path)
    rules = load_allowlist()

    findings = 0

    undetected = sorted(tuple(sorted(p)) for p in reg_pairs - g_pairs if not allowed(p, rules))
    if undetected:
        findings += len(undetected)
        print(f"UNDETECTED — in the integration map, no graph edge ({len(undetected)}):")
        for a, b in undetected:
            print(f"  {a} ↔ {b}    (candidate for a declared edge or an allowlist entry)")

    def documented(pair: Pair) -> bool:
        return pair in reg_pairs or any(pair <= grp for grp in covered_groups)

    undocumented = sorted(
        tuple(sorted(p)) for p in g_pairs if not documented(p) and not allowed(p, rules)
    )
    if undocumented:
        findings += len(undocumented)
        print(f"UNDOCUMENTED — graph edge missing from the integration map ({len(undocumented)}):")
        for a, b in undocumented:
            print(f"  {a} ↔ {b}    (update {REGISTRY_REL})")

    connected = {n for p in g_pairs for n in p}
    stale = sorted(reg_unconnected & connected)
    if stale:
        findings += len(stale)
        print(f"STALE CLAIM — listed as 'not yet connected' but has edges ({len(stale)}):")
        for n in stale:
            partners = sorted({m for p in g_pairs if n in p for m in p if m != n})
            print(f"  {n} ↔ {', '.join(partners)}")

    if findings:
        print(f"\n{findings} finding(s). Known-acceptable pairs go to {ALLOWLIST.name} with a reason.")
        return 1
    print(
        f"OK: integration map and graph agree "
        f"({len(reg_pairs)} registry pairs, {len(g_pairs)} graph pairs, "
        f"{len(rules)} allowlist rules)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
