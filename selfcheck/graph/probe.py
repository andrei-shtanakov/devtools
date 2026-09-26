"""usage-graph probe: repo graph + isolated canary graph (spec §3.2)."""

from __future__ import annotations

from selfcheck.corpus import last_commit_ts
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import Surface, classify, graph_payload
from selfcheck.graph.model import NodeKind
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec
from selfcheck.roles import Role, role_of

CANARY_DIR = ".selfcheck-canary/usage-graph/"


def _analyze(ctx: ProbeCtx) -> ParseResult:
    target = ctx.target

    def role(path: str) -> Role:
        return role_of(path, target.roles)

    history: dict[str, bool] = {}

    def ages(path: str) -> float | None:
        ts = last_commit_ts(target.source, path)
        return float(ts) if ts is not None else None

    graph = build_graph(
        list(target.corpus),
        target.copy,
        role,
        repo_name=target.name,
        sched_dir=target.sched_dir,
    )
    for node in graph.nodes.values():
        if node.kind is NodeKind.FILE:
            history[node.path] = ages(node.path) is not None
    surface = Surface(
        target.fleet,
        str(target.sched_dir) if target.sched_dir else None,
        list(graph.plists),
    )
    findings = classify(
        graph, repo=target.name, surface=surface, ages=ages, now=target.now
    )
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
    return ParseResult(
        findings,
        skipped=[e.split(":", 1)[0] for e in graph.errors],
        diagnostics=list(graph.errors),
        extra={
            "graph": graph_payload(graph),
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
    logic_version=1,
    analyze=_analyze,
)
