#!/usr/bin/env python3
"""agent-id conformance — READ-ONLY cross-repo check for ADR-ECO-003.

Verifies the vendored/generated agent rosters across ATP, arbiter and Maestro
stay consistent with the SSOT catalog `atp-platform/method/agents-catalog.toml`
(canon since 2026-07-03 — the SSOT must live inside a distributable repo;
`_cowork_output` is a dev-only coordination workspace that installed users do
not have, so `contracts/agents-catalog.toml` is a communication MIRROR, not the
source). Its whole job is to turn the "silent None → re-rank no-op" failure
mode (a mismatched `agent_id` that the arbiter join drops without error) into a
loud CI failure.

Checks (ADR-ECO-003 §CI-conformance, pre-D2 variant):
  1. Every `routable = true` agent_id has a byte-for-byte `["<id>"]` section in
     arbiter/config/agents.toml.
  2. Every non-canon copy (arbiter/config/, _cowork_output/contracts/ mirror) is
     byte-for-byte identical to the SSOT — so all consumers see the same pairs.
  3. Every `routable = true` harness has a registered Maestro spawner — i.e. a
     concrete AgentSpawner in maestro/spawners/ whose `agent_type` returns it
     (SpawnerRegistry membership, per ADR-002 D2; superseded the AgentType enum
     gate, which also listed the non-spawnable `auto` routing sentinel).
  4. No two agent_ids collapse to the same `safe_agent_id` (filesystem stem).
  5. No `[[agents]]` row references a model that is missing or `status = retired`.

Exit 0 — all invariants hold. Exit 1 — at least one hard invariant broke.
Nothing is written; the script only reads and diffs.

Usage:
    python3 check-agent-id-conformance.py            # autodetect workspace root
    python3 check-agent-id-conformance.py --root /path/to/all_ai_orchestrators
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# Mirror atp-platform/method/run_pipe_check.py::safe_agent_id — must stay in sync.
_SAFE_ID_RE = re.compile(r"[@:.]")


def safe_agent_id(agent_id: str) -> str:
    """Filesystem-safe stem for an agent_id (lossy; mirrors ATP's helper)."""
    return _SAFE_ID_RE.sub("_", agent_id)


def find_workspace_root(start: Path) -> Path | None:
    """Walk up until a directory contains both arbiter/ and maestro/."""
    for candidate in [start, *start.parents]:
        if (candidate / "arbiter").is_dir() and (candidate / "maestro").is_dir():
            return candidate
    return None


def arbiter_section_ids(agents_toml: Path) -> set[str]:
    """Quoted `["<id>"]` section headers in arbiter's agents.toml (fused ids).

    Bare `[aider]`-style legacy sections are pre-convention and intentionally
    excluded — they are not routable fused keys.
    """
    ids: set[str] = set()
    for line in agents_toml.read_text().splitlines():
        match = re.match(r'^\["([^"]+)"\]\s*$', line.strip())
        if match:
            ids.add(match.group(1))
    return ids


def maestro_spawner_agent_types(spawners_dir: Path) -> set[str]:
    """Registered spawner keys — the D2 replacement for the AgentType enum gate.

    ``create_default_registry()`` runs ``discover_from_directory`` over
    ``maestro/spawners/`` and registers every concrete ``AgentSpawner`` under the
    string its ``agent_type`` property returns. We mirror that statically (this
    check imports nothing) by scraping those return literals. The abstract base
    returns ``...`` rather than a literal, so — exactly like directory discovery —
    it is naturally excluded.
    """
    pattern = re.compile(
        r'def agent_type\(self\)\s*->\s*str:\s*'
        r'(?:"""[^"]*"""\s*)?'  # optional one-line docstring
        r'return\s+"([a-z_]+)"',
        re.S,
    )
    types: set[str] = set()
    for py in sorted(spawners_dir.glob("*.py")):
        types.update(pattern.findall(py.read_text()))
    return types


def _selftest() -> int:
    """Verify the spawner-scrape (Check 3's core) without touching the workspace."""
    import tempfile

    with_doc = (
        "class FooSpawner(AgentSpawner):\n"
        "    @property\n"
        "    def agent_type(self) -> str:\n"
        '        """Return the agent type identifier."""\n'
        '        return "foo_cli"\n'
    )
    no_doc = (
        "class BarSpawner(AgentSpawner):\n"
        "    @property\n"
        "    def agent_type(self) -> str:\n"
        '        return "bar_cli"\n'
    )
    abstract = (
        "class AgentSpawner(ABC):\n"
        "    @property\n"
        "    @abstractmethod\n"
        "    def agent_type(self) -> str:\n"
        '        """Unique identifier for this agent type."""\n'
        "        ...\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "foo.py").write_text(with_doc)
        (d / "bar.py").write_text(no_doc)
        (d / "base.py").write_text(abstract)  # abstract → no literal → excluded
        got = maestro_spawner_agent_types(d)
    assert got == {"foo_cli", "bar_cli"}, got
    print("selftest OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="workspace root")
    parser.add_argument(
        "--selftest", action="store_true", help="run built-in checks and exit"
    )
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    root = args.root or find_workspace_root(Path(__file__).resolve())
    if root is None or not (root / "arbiter").is_dir():
        print("✗ could not locate workspace root (needs arbiter/ + maestro/)")
        return 1

    # Canon (2026-07-03, owner ruling): the SSOT lives INSIDE a distributable
    # repo — atp-platform. `_cowork_output` is dev-only coordination that users
    # and teams installing the projects do NOT have; a canon there would turn a
    # dev folder into a production dependency. contracts/ = communication mirror.
    ssot = root / "atp-platform" / "method" / "agents-catalog.toml"
    # Every copy that must stay byte-for-byte identical to the SSOT.
    vendored_copies = {
        "arbiter": root / "arbiter" / "config" / "agents-catalog.toml",
        "workspace-mirror (contracts/)": (
            root / "_cowork_output" / "contracts" / "agents-catalog.toml"
        ),
    }
    arbiter_toml = root / "arbiter" / "config" / "agents.toml"
    maestro_spawners = root / "Maestro" / "maestro" / "spawners"

    if not ssot.exists():
        print(f"✗ SSOT catalog missing: {ssot}")
        return 1

    catalog = tomllib.loads(ssot.read_text())
    models: dict[str, dict[str, object]] = catalog.get("models", {})
    harnesses: dict[str, dict[str, object]] = catalog.get("harnesses", {})
    agents: list[dict[str, object]] = catalog.get("agents", [])

    failures: list[str] = []
    oks: list[str] = []

    def agent_id(row: dict[str, object]) -> str:
        return f"{row['harness']}@{row['model']}"

    routable_ids = [agent_id(a) for a in agents if a.get("routable")]
    routable_harnesses = {
        str(a["harness"]) for a in agents if a.get("routable")
    }

    # --- Check 1: routable agent_id ↔ arbiter agents.toml section ---
    if arbiter_toml.exists():
        sections = arbiter_section_ids(arbiter_toml)
        missing = [aid for aid in routable_ids if aid not in sections]
        if missing:
            failures.append(
                f"[1] routable agent_id(s) absent from arbiter agents.toml: {missing}"
            )
        else:
            oks.append(f"[1] all {len(routable_ids)} routable ids present in arbiter")
    else:
        failures.append(f"[1] arbiter agents.toml not found: {arbiter_toml}")

    # --- Check 2: every vendored copy == SSOT (byte-for-byte) ---
    ssot_bytes = ssot.read_bytes()
    drifted = False
    for repo_name, copy in vendored_copies.items():
        if not copy.exists():
            failures.append(f"[2] {repo_name} vendored catalog missing: {copy}")
            drifted = True
        elif copy.read_bytes() != ssot_bytes:
            failures.append(
                f"[2] {repo_name} vendored catalog drifted from SSOT "
                f"(re-vendor {copy} from {ssot})"
            )
            drifted = True
    if not drifted:
        oks.append(
            f"[2] all {len(vendored_copies)} vendored copies "
            f"({', '.join(vendored_copies)}) match SSOT byte-for-byte"
        )

    # --- Check 3: routable harness ↔ Maestro spawner registry membership (D2) ---
    if maestro_spawners.is_dir():
        known = maestro_spawner_agent_types(maestro_spawners)
        missing_h = sorted(h for h in routable_harnesses if h not in known)
        if missing_h:
            failures.append(
                f"[3] routable harness(es) with no registered Maestro spawner: "
                f"{missing_h}"
            )
        else:
            oks.append(
                f"[3] routable harnesses {sorted(routable_harnesses)} "
                f"have Maestro spawners"
            )
    else:
        failures.append(f"[3] Maestro spawners dir not found: {maestro_spawners}")

    # --- Check 4: no safe_agent_id collisions ---
    seen: dict[str, str] = {}
    collided = False
    for row in agents:
        aid = agent_id(row)
        stem = safe_agent_id(aid)
        if stem in seen and seen[stem] != aid:
            failures.append(
                f"[4] safe_agent_id collision: {seen[stem]!r} vs {aid!r} → {stem!r}"
            )
            collided = True
        seen[stem] = aid
    if not collided:
        oks.append(f"[4] no safe_agent_id collisions across {len(agents)} agents")

    # --- Check 5: no [[agents]] references a missing/retired model ---
    bad_models: list[str] = []
    for row in agents:
        model = str(row["model"])
        spec = models.get(model)
        if spec is None:
            bad_models.append(f"{agent_id(row)} → model {model!r} not declared")
        elif spec.get("status") == "retired":
            bad_models.append(f"{agent_id(row)} → model {model!r} is retired")
    if bad_models:
        failures.append("[5] enrollment references missing/retired models:")
        failures.extend(f"      {m}" for m in bad_models)
    else:
        oks.append(f"[5] all enrolled models declared and not retired")

    for line in oks:
        print(f"✓ {line}")
    for line in failures:
        print(f"✗ {line}")

    print()
    if failures:
        print("Result: agent-id conformance BROKEN — sync before merge.")
        return 1
    print(
        f"Result: agent-id conformance holds "
        f"({len(routable_ids)} routable, {len(agents)} enrolled)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
