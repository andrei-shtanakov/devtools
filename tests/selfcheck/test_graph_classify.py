"""Task 10 — dead classification D1–D12, roots, usage-graph probe (§2.3, §3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import NodeFacts, Surface, dead_confidence, klass_of
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.model import Confidence
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from selfcheck.roles import role_of
from tests.selfcheck.helpers import NOW, USAGE_FILES, ago, make_repo, plist_dir, write

C, L, K = Confidence.CONFIRMED, Confidence.LIKELY, Confidence.CANDIDATE

# spec §2.3 matrix: id, class, root, zone, fleet, sched-dir, history, age, mention → result
MATRIX = [
    ("D1", "orphan", False, False, "complete", True, True, 90, False, C),
    ("D2", "orphan", False, False, "absent", True, True, 90, False, L),
    ("D3", "orphan", False, False, "complete", False, True, 90, False, L),
    ("D4", "orphan", False, False, "complete", True, True, 10, False, L),
    ("D5", "orphan", False, False, "complete", True, False, None, False, L),
    ("D6", "orphan", False, False, "complete", True, True, 90, True, L),
    ("D7", "test-only", False, False, "complete", True, True, 90, False, K),
    ("D8", "doc-only", False, False, "absent", False, False, None, True, K),
    ("D9", "orphan", True, False, "complete", True, True, 90, False, None),
    ("D10", "orphan", False, True, "complete", True, True, 90, False, None),
    ("D11", "live", False, False, "complete", True, True, 90, False, None),
]


@pytest.mark.parametrize("row", MATRIX, ids=lambda r: r[0])
def test_dead_matrix(row) -> None:
    _, klass, root, zone, fleet, sched, history, age, mention, expected = row
    surface = Surface(fleet, "/sched" if sched else None, [])
    facts = NodeFacts(klass, root, zone, history, age, mention)
    assert dead_confidence(facts, surface)[0] == expected


def test_d12_plist_makes_live(tmp_path: Path) -> None:
    sched = plist_dir(tmp_path, ["/w/repo/job.py"])
    write(tmp_path / "repo", {"job.py": 'if __name__ == "__main__":\n    pass\n'})
    g = build_graph(
        ["job.py"], tmp_path / "repo", role_of, repo_name="repo", sched_dir=sched
    )
    assert klass_of(g, "file:job.py") == "live"


def run_usage(tmp: Path, files: dict[str, str], *, date: str = "") -> ProbeResult:
    repo = make_repo(tmp / "repo", files, date=date or ago(90))
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([USAGE_GRAPH]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(USAGE_GRAPH, target, tmp / "run" / "work")
    finally:
        release(copy)


def by_rule(res: ProbeResult, rule: str) -> dict[str, object]:
    return {f.anchor: f for f in res.findings if f.rule == rule}


def test_s1_orphan_likely_roots_never_dead(tmp_path: Path) -> None:
    res = run_usage(tmp_path, USAGE_FILES)
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    dead = by_rule(res, "usage-graph/dead.file")
    assert set(dead) == {"file:orphan.py"}
    finding = dead["file:orphan.py"]
    assert finding.confidence is Confidence.LIKELY and finding.text_key is None
    assert {"P1", "P2"} <= {e["detail"] for e in finding.evidence if e["kind"] == "cap"}
    assert not any(
        f.anchor.startswith(("make:", "skill:", "cli:", "unit:", "workflow:"))
        for f in res.findings
        if f.rule.startswith("usage-graph/dead")
    )


def test_zone_reported_not_dead_and_id_stable(tmp_path: Path) -> None:
    files = {
        "review.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
        "scripts/review/local.sh": "#!/bin/sh\necho\n",
    }
    first = run_usage(tmp_path / "a", files)
    shifted = run_usage(
        tmp_path / "b", {**files, "review.sh": '#!/bin/sh\n\n\nsh "$kit/local.sh"\n'}
    )
    assert "file:scripts/review/local.sh" not in by_rule(first, "usage-graph/dead.file")
    ids = [
        {f.id for f in r.findings if f.rule == "usage-graph/unresolved-exec"}
        for r in (first, shifted)
    ]
    assert ids[0] and ids[0] == ids[1]
    assert {
        f.severity for f in first.findings if f.rule == "usage-graph/unresolved-exec"
    } == {"low"}


def test_broken_and_stale_roots(tmp_path: Path) -> None:
    res = run_usage(
        tmp_path,
        {"Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\n'},
        date=ago(400),
    )
    assert "make:Makefile#gone" in by_rule(res, "usage-graph/broken-root")
    assert "make:Makefile#gone" in by_rule(res, "usage-graph/root-stale")
    fresh = run_usage(
        tmp_path / "f",
        {"Makefile": 'help:\n\t@echo "make x"\nx:\n\t@true\n'},
        date=ago(30),
    )
    assert by_rule(fresh, "usage-graph/root-stale") == {}


def test_syntax_error_in_source_is_partial(tmp_path: Path) -> None:
    assert run_usage(tmp_path, {"bad.py": "def f(:\n"}).status is ProbeStatus.PARTIAL


def test_report_graph_payload(tmp_path: Path) -> None:
    res = run_usage(tmp_path, USAGE_FILES)
    graph = res.extra["graph"]
    assert set(graph) == {
        "file:live.py",
        "file:orphan.py",
        "make:Makefile#help",
        "make:Makefile#go",
        "skill:skills/s/SKILL.md",
    }
    assert graph["file:live.py"] == {
        "class": "live",
        "root": False,
        "edges": [{"kind": "make", "from": "Makefile:3"}],
        "fleet_only": False,  # S2 payload contract (§9.3)
        "vendored": [],  # S2 payload contract (§9.7)
    }
    assert graph["file:orphan.py"]["class"] == "orphan"
    assert graph["skill:skills/s/SKILL.md"]["root"] is True
    assert res.extra["surface"] == {
        "fleet": "absent",
        "sched_dir": None,
        "plists": [],
        "history": {"live.py": True, "orphan.py": True},
    }
