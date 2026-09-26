"""S2 Task 2 — precise channel (§9.3) and text channel keys (§9.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.fleet.assemble import (
    CANARY_NODE,
    CANARY_REPO,
    build_canary,
    canary_misses,
)
from selfcheck.fleet.match import (
    apply_fleet,
    find_mentions,
    fleet_edges,
    match_external,
    node_keys,
)
from selfcheck.fleet.reader import read_repo
from selfcheck.graph.build import build_graph
from selfcheck.graph.model import EdgeKind
from selfcheck.roles import role_of
from tests.selfcheck.helpers import make_repo, synced, write

FILES = frozenset({"x.py", "sub/y.sh", "maestro/x.py"})


@pytest.mark.parametrize(
    ("target", "scope", "expected"),
    [
        ("../devtools/x.py", "devtools", ["x.py"]),
        ("ws/devtools/x.py", "devtools", ["x.py"]),
        ("/abs/p/devtools/sub/y.sh", "devtools", ["sub/y.sh"]),
        ("../DevTools/x.py", "devtools", ["x.py"]),  # case-insensitive segment
        ("../maestro/maestro/x.py", "maestro", ["maestro/x.py", "x.py"]),  # each
        ("chrome/devtools/x.py", "devtools", ["x.py"]),  # accepted compromise
        ("x.py", "devtools", []),  # no segment
        ("../devtools/nope.py", "devtools", []),
        ("../devtoolsx/x.py", "devtools", []),  # whole segment only
    ],
)
def test_match_external(target: str, scope: str, expected: list[str]) -> None:
    assert match_external(target, scope, FILES) == expected


def test_fleet_edges_from_neighbour_sources(tmp_path: Path) -> None:
    nb = synced(
        make_repo(
            tmp_path / "nb",
            {
                ".github/workflows/c.yml": (
                    "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - run: python3 ws/devtools/check.py\n"
                    "      - working-directory: ../devtools\n"
                    "        run: ./wd.sh\n"
                    '      - run: bash "$WS/devtools/var.sh"\n'
                ),
                "Makefile": "t:\n\tsh ../devtools/tool.sh\n",
                "notes.md": "see ../devtools/prose.sh\n",
            },
        )
    )
    scope = frozenset({"check.py", "wd.sh", "tool.sh", "var.sh", "prose.sh"})
    edges = fleet_edges(read_repo(nb, "nb"), "devtools", scope)
    assert {path for path, _ in edges} == {"check.py", "wd.sh", "tool.sh"}
    where = {path: w for path, w in edges}
    assert where["check.py"].path == "nb:.github/workflows/c.yml"
    assert where["tool.sh"].path == "nb:Makefile" and where["tool.sh"].line == 2


def test_node_keys() -> None:
    assert node_keys("governance/console.py") == (
        "console.py",
        "governance/console.py",
        "governance.console",
    )
    assert node_keys("attest-vendor.sh") == ("attest-vendor.sh",)
    assert node_keys("src/pkg/__init__.py") == (
        "__init__.py",
        "src/pkg/__init__.py",
        "pkg",
    )


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("xx.sh", False),
        ("attest-x.sh", False),  # '-' on the left glues
        ("x.shell", False),
        ("run ../devtools/x.sh.", True),  # sentence dot on the right
        ("RUN X.SH", True),  # case-insensitive
        ("`x.sh`", True),
        ("x.sh-helper", True),  # '-' on the right is a boundary
    ],
)
def test_mention_boundaries(text: str, hit: bool) -> None:
    found = find_mentions({"file:x.sh": ("x.sh",)}, {"a.md": text}, "nb")
    assert (found == {"file:x.sh": ["nb:a.md"]}) is hit


def test_module_key_boundaries() -> None:
    keys = {"file:g/c.py": node_keys("g/c.py")}
    texts = {
        "t1.py": 'mock.patch("g.c.main")',
        "t2.py": "import a.g.c",
        "t3.py": "g.cx",
    }
    assert find_mentions(keys, texts, "nb") == {"file:g/c.py": ["nb:t1.py", "nb:t2.py"]}


def test_overlapping_keys_are_all_found() -> None:
    """One pass must not drop a key hidden inside a longer one (review r1 M1)."""
    keys = {
        "file:a/run.sh": node_keys("a/run.sh"),
        "file:b/run.sh": node_keys("b/run.sh"),
    }
    found = find_mentions(keys, {"ci.yml": "sh ../devtools/a/run.sh"}, "nb")
    assert found == {"file:a/run.sh": ["nb:ci.yml"], "file:b/run.sh": ["nb:ci.yml"]}
    keys = {
        "file:g/c.py": node_keys("g/c.py"),
        "file:g/c/main.py": node_keys("g/c/main.py"),
    }
    found = find_mentions(keys, {"j": "python -m g.c.main"}, "nb")
    assert found == {"file:g/c.py": ["nb:j"], "file:g/c/main.py": ["nb:j"]}


def test_apply_fleet_wires_edges_and_mentions(tmp_path: Path) -> None:
    scope = tmp_path / "devtools"
    write(scope, {"check.py": "x = 1\n", "attest.sh": "#!/bin/sh\n"})
    g = build_graph(
        ["check.py", "attest.sh"], scope, role_of, repo_name="devtools", sched_dir=None
    )
    nb = synced(
        make_repo(
            tmp_path / "nb",
            {
                "Makefile": "t:\n\tpython3 ../devtools/check.py\n",
                "TODO.md": "attest.sh\n",
            },
        )
    )
    apply_fleet(g, [read_repo(nb, "nb")], "devtools")
    (edge,) = g.incoming("file:check.py")
    assert (edge.kind, edge.where.path) == (EdgeKind.FLEET, "nb:Makefile")
    assert g.mentions["file:attest.sh"] == ["nb:TODO.md"]


def _canary_graph(tmp_path: Path):
    root = tmp_path / "scope"
    write(root, {CANARY_NODE: "x = 1\n"})
    return build_graph(
        [CANARY_NODE], root, role_of, repo_name="devtools", sched_dir=None
    )


def test_canary_passes_through_apply_fleet(tmp_path: Path) -> None:
    g = _canary_graph(tmp_path)
    canary = read_repo(build_canary(tmp_path / "c", "devtools"), CANARY_REPO)
    apply_fleet(g, [canary], "devtools")
    assert canary_misses(g) == []


def test_canary_counts_only_its_own_files(tmp_path: Path) -> None:
    """A real repo mentioning fleet_canary.py, and the bare name inside the
    canary workflow, do not satisfy the text form (§9.5)."""
    nb = synced(make_repo(tmp_path / "nb", {"n.md": "fleet_canary.py\n"}))
    only_precise = build_canary(tmp_path / "c", "devtools", forms=("precise",))
    g = _canary_graph(tmp_path)
    apply_fleet(
        g, [read_repo(nb, "nb"), read_repo(only_precise, CANARY_REPO)], "devtools"
    )
    assert canary_misses(g) == ["text"]
    g = _canary_graph(tmp_path / "2")
    only_text = build_canary(tmp_path / "t", "devtools", forms=("text",))
    apply_fleet(g, [read_repo(only_text, CANARY_REPO)], "devtools")
    assert canary_misses(g) == ["precise"]


def test_keys_outside_the_token_class_are_found() -> None:
    """Non-ASCII and spaces in a node name: a separate search, same boundaries."""
    keys = {
        "file:запуск.sh": node_keys("запуск.sh"),
        "file:a b.sh": node_keys("a b.sh"),
    }
    texts = {
        "a.md": "run ./запуск.sh now",
        "b.md": 'run "a b.sh"',
        "c.md": "xзапуск.sh",
    }
    assert find_mentions(keys, texts, "nb") == {
        "file:a b.sh": ["nb:b.md"],
        "file:запуск.sh": ["nb:a.md"],
    }


def test_parsing_neighbour_code_emits_no_syntax_warning(tmp_path: Path) -> None:
    """Acceptance 2026-09-26: 22 repos of foreign code must not spam stderr with
    SyntaxWarning ('invalid escape sequence') — it is their code, not a finding."""
    import warnings

    nb = synced(
        make_repo(tmp_path / "nb", {"x.py": 'import os\nP = "\\\\`"\nQ = "\\`"\n'})
    )
    # "error" would not do: compile() turns an erroring SyntaxWarning into a
    # SyntaxError, which the builder swallows — record instead
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fleet_edges(read_repo(nb, "nb"), "devtools", frozenset())
    assert [w for w in caught if issubclass(w.category, SyntaxWarning)] == []
