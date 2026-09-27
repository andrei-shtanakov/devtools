"""usage-graph probe: repo graph + isolated canary graph (spec §3.2, §9)."""

from __future__ import annotations

import dataclasses

from selfcheck.corpus import last_commit_ts, shown
from selfcheck.fleet import match
from selfcheck.fleet.assemble import CANARY_NODE, CANARY_REPO, canary_misses
from selfcheck.graph.build import build_graph, read_corpus_text
from selfcheck.graph.classify import Surface, classify, graph_payload
from selfcheck.graph.model import Graph, NodeKind
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec
from selfcheck.roles import Role, role_of
from selfcheck.vendor import vendor_roles

CANARY_DIR = ".selfcheck-canary/usage-graph/"
FLEET_CANARY = frozenset({CANARY_NODE})


class FleetCanaryMissed(Exception):
    """The fleet canary did not come through: the fleet channels are not trusted."""


def _isolate(g: Graph) -> None:
    """Canary-sourced edges and mentions count only for the canary (§9.5)."""
    prefix = f"{CANARY_REPO}:"
    anchor = f"file:{CANARY_NODE}"
    g.edges = [
        e for e in g.edges if e.target == anchor or not e.where.path.startswith(prefix)
    ]
    for node, places in g.mentions.items():
        if node != anchor:
            places[:] = [p for p in places if not p.startswith(prefix)]


def _apply_fleet(g: Graph, ctx: ProbeCtx) -> None:
    view = ctx.target.fleet_view
    if view is None:
        return
    if view.canary_misses or view.canary is None:
        raise FleetCanaryMissed(f"fleet-canary: {','.join(view.canary_misses)}")
    match.apply_fleet(g, [*view.repos, view.canary], ctx.target.name)
    misses = canary_misses(g)
    if misses:
        raise FleetCanaryMissed(f"fleet-canary: {','.join(misses)}")
    _isolate(g)


def _operator(g: Graph, ctx: ProbeCtx) -> list[Finding]:
    """[[operator]] paths become roots in the graph; a missing one is a finding."""
    target = ctx.target
    corpus = set(target.corpus)
    missing: list[Finding] = []
    for path in target.operator:
        anchor = f"file:{path}"
        if anchor in g.nodes:
            g.nodes[anchor] = dataclasses.replace(g.nodes[anchor], root=True)
        if path not in corpus:
            missing.append(
                Finding(
                    rule="selfcheck/operator-missing",
                    category="selfcheck",
                    severity="medium",
                    confidence=Confidence.CONFIRMED,
                    owner_repo=target.name,
                    anchor=f"probe:{target.name}#usage-graph",
                    locations=[Location(path, 1)],
                    text_key=path,
                    suggestion="уберите запись [[operator]] или верните файл",
                )
            )
    return missing


def _analyze(ctx: ProbeCtx) -> ParseResult:
    target = ctx.target

    def role(path: str) -> Role:
        return role_of(path, target.roles)

    history: dict[str, bool] = {}

    def ages(path: str) -> float | None:
        ts = last_commit_ts(target.source, path)
        return float(ts) if ts is not None else None

    corpus = list(target.corpus)
    files = corpus + ([CANARY_NODE] if target.fleet_view is not None else [])
    graph = build_graph(
        files, target.copy, role, repo_name=target.name, sched_dir=target.sched_dir
    )
    node_paths = frozenset(
        n.path for n in graph.nodes.values() if n.kind is NodeKind.FILE
    )
    texts = {rel: read_corpus_text(target.copy, rel) for rel in corpus}
    vendor = vendor_roles(target.name, corpus, texts, role, node_paths)
    _apply_fleet(graph, ctx)
    operator_missing = _operator(graph, ctx)
    for node in graph.nodes.values():
        if node.kind is NodeKind.FILE and node.path not in FLEET_CANARY:
            history[node.path] = ages(node.path) is not None
    surface = Surface(
        target.fleet,
        shown(str(target.sched_dir)) if target.sched_dir else None,
        list(graph.plists),
    )
    findings = classify(
        graph,
        repo=target.name,
        surface=surface,
        ages=ages,
        now=target.now,
        protected=frozenset(vendor.protected),
        decl_cap=vendor.broken,
        vendored=vendor.members,
        exclude=FLEET_CANARY,
    )
    findings += vendor.findings + operator_missing
    canary = [p for p in ctx.inputs if p.startswith(CANARY_DIR)]
    canary_graph = build_graph(
        canary,
        target.copy,
        lambda p: Role.SOURCE,
        repo_name=target.name,
        sched_dir=None,
    )
    findings += classify(
        canary_graph,
        repo=target.name,
        surface=surface,
        ages=lambda p: None,
        now=target.now,
    )
    broken_decls = sorted({f.locations[0].path for f in vendor.findings})
    return ParseResult(
        findings,
        skipped=[e.split(":", 1)[0] for e in graph.errors] + broken_decls,
        diagnostics=list(graph.errors)
        + [f"{f.rule}: {f.anchor} {f.text_key or ''}".strip() for f in vendor.findings],
        extra={
            "graph": graph_payload(graph, vendor.members, FLEET_CANARY),
            "vendored": {
                path: [
                    {"owner": d.owner, "ref": d.ref, "declaration": d.path}
                    for d in decls
                ]
                for path, decls in sorted(vendor.members.items())
            },
            "surface": {
                "fleet": surface.fleet,
                "sched_dir": surface.sched_dir,
                "plists": surface.plists,
                "history": history,
            },
        },
    )


USAGE_GRAPH = ProbeSpec(
    name="usage-graph",
    languages=frozenset({"any"}),
    input_mode="files",
    select=lambda t: t.corpus,
    canary=Canary(
        f"{CANARY_DIR}orphan_canary.py",
        'if __name__ == "__main__":\n    print("canary")\n',
        "usage-graph/dead.file",
        f"file:{CANARY_DIR}orphan_canary.py",
    ),
    rules=("dead.file", "dead.module", "unresolved-exec", "broken-root", "root-stale"),
    logic_version=5,  # BOM-aware corpus reads, make-call parsing (#411)
    analyze=_analyze,
)
