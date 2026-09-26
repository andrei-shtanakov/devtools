"""S2 Task 4 — matrix D13–D16, cap P6, class fleet-only (§2.3, §9.3, §9.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import (
    NodeFacts,
    Surface,
    classify,
    dead_confidence,
    fleet_only,
    graph_payload,
)
from selfcheck.graph.model import EdgeKind
from selfcheck.model import Confidence, Location
from selfcheck.roles import role_of
from selfcheck.vendor import Declaration
from tests.selfcheck.helpers import NOW, USAGE_FILES, make_repo

C, L = Confidence.CONFIRMED, Confidence.LIKELY
FULL = Surface("complete", "/sched", [])


@pytest.mark.parametrize(
    ("row", "facts", "expected", "caps"),
    [
        ("D13", NodeFacts("live", False, False, True, 90, False), None, []),
        ("D14", NodeFacts("orphan", False, False, True, 90, True), L, ["P5"]),
        (
            "D15",
            NodeFacts("orphan", False, False, True, 90, False, vendored=True),
            None,
            [],
        ),
        (
            "D16",
            NodeFacts("orphan", False, False, True, 90, False, vendored=True),
            None,
            [],
        ),
        (
            "P6",
            NodeFacts("orphan", False, False, True, 90, False, decl_cap=True),
            L,
            ["P6"],
        ),
        ("D1", NodeFacts("orphan", False, False, True, 90, False), C, []),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_s2_matrix(row, facts, expected, caps) -> None:
    assert dead_confidence(facts, FULL) == (expected, caps)


def _graph(tmp_path: Path):
    files = {**USAGE_FILES, "fleet_only.py": "x = 1\n", "both.py": "x = 1\n"}
    files["Makefile"] += "b: ; @python3 ./both.py\n"
    repo = make_repo(tmp_path / "devtools", files)
    g = build_graph(sorted(files), repo, role_of, repo_name="devtools", sched_dir=None)
    g.add("fleet_only.py", EdgeKind.FLEET, Location("nb:.github/workflows/c.yml", 1))
    g.add("both.py", EdgeKind.FLEET, Location("nb:Makefile", 2))
    return g


def test_fleet_only_class_and_payload(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    assert fleet_only(g) == ["file:fleet_only.py"]
    payload = graph_payload(g)
    node = payload["file:fleet_only.py"]
    assert node["class"] == "live" and node["fleet_only"] is True
    assert node["edges"] == [{"kind": "fleet", "from": "nb:.github/workflows/c.yml:1"}]
    assert payload["file:both.py"]["fleet_only"] is False
    assert payload["file:both.py"]["vendored"] == []
    decl = Declaration("k/PIN", "A", "steward", "5bfd829", ("both.py",))
    with_decl = graph_payload(g, vendored={"both.py": [decl]})
    assert with_decl["file:both.py"]["vendored"] == [
        {"owner": "steward", "ref": "5bfd829", "declaration": "k/PIN"}
    ]


def test_classify_protected_and_decl_cap(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    found = classify(
        g,
        repo="devtools",
        surface=FULL,
        ages=lambda p: NOW - 90 * 86400,
        now=NOW,
        protected=frozenset({"orphan.py"}),
        decl_cap=False,
    )
    assert not [f for f in found if f.anchor == "file:orphan.py"]
    capped = classify(
        _graph(tmp_path / "2"),
        repo="devtools",
        surface=FULL,
        ages=lambda p: NOW - 90 * 86400,
        now=NOW,
        decl_cap=True,
    )
    (dead,) = [f for f in capped if f.anchor == "file:orphan.py"]
    assert dead.confidence is L
    assert {"kind": "cap", "detail": "P6"} in dead.evidence


def test_mentions_capped_at_five_with_a_count(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    for i in range(7):
        g.mention("file:orphan.py", f"nb:f{i}.md")
    (dead,) = [
        f
        for f in classify(
            g, repo="devtools", surface=FULL, ages=lambda p: NOW - 90 * 86400, now=NOW
        )
        if f.anchor == "file:orphan.py"
    ]
    places = [e["detail"] for e in dead.evidence if e["kind"] == "mentioned-in"]
    assert places == [f"nb:f{i}.md" for i in range(5)]
    assert {"kind": "mentioned-in-total", "detail": "7"} in dead.evidence
