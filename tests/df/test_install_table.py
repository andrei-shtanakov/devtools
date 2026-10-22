"""Tests of df.install_table: the spec 3.3 rules as data and their renderings."""

from __future__ import annotations

import itertools
import re
import textwrap
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from df import install_table as it
from df.install_table import (
    ACCOUNT_SOURCES,
    DIRS,
    DOMAINS,
    FACT_SOURCES,
    ROWS,
    UNKNOWN_ROW,
    Cube,
    Row,
    acquire_dirs,
    check_data,
    classify,
    matches,
    overlaps,
    render_protocols,
    render_route_calls,
    render_routes,
    render_settle,
    render_table,
    row_of,
    settle_dirs,
)

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "docs/superpowers/specs/2026-10-13-df-installer-design.md"
PLAN = REPO / "docs/superpowers/plans/2026-10-16-df-installer-stage1.md"


def block(text: str, name: str) -> str:
    """The text between `<!-- name:begin -->` and `<!-- name:end -->`, dedented."""
    begin = f"<!-- {name}:begin -->\n"
    start = text.index(begin) + len(begin)
    end = text.index(f"<!-- {name}:end -->", start)
    return textwrap.dedent(text[start:end]).rstrip()


def product_of(
    domains: Mapping[str, tuple[object, ...]],
) -> Iterator[dict[str, object]]:
    keys = list(domains)
    for values in itertools.product(*domains.values()):
        yield dict(zip(keys, values, strict=True))


@pytest.fixture(scope="module")
def spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_rows_disjoint_by_cubes() -> None:
    assert overlaps(ROWS) == []


def test_every_point_at_most_one_row_and_unknown_is_pre_registration() -> None:
    n = 0
    for f in product_of(DOMAINS):
        n += 1
        hits = [r for r in ROWS if any(matches(c, f) for c in r.cubes)]
        assert len(hits) <= 1
        if not hits:
            assert f["P"] and not f["reg"] and not f["cur"]
    assert n == 7680


def test_spec_table_is_rendering_of_data(spec: str) -> None:
    assert block(spec, "install-table") == render_table()


def _without(cube: Cube, fact: str) -> Cube:
    return {k: v for k, v in cube.items() if k != fact}


def mutated(rows: tuple[Row, ...], mutate: str) -> tuple[Row, ...]:
    """The table twins of r3: registered rows without `intact`, pre-registration
    rows without `not cur`."""
    if mutate == "drop-intact-from-registered":
        phases = ("launched", "active", "registered")
        return tuple(
            replace(r, cubes=tuple(_without(c, "intact") for c in r.cubes))
            if r.phase in phases
            else r
            for r in rows
        )
    assert mutate == "drop-not-cur-from-pre"
    return tuple(
        replace(
            r,
            cubes=tuple(
                _without(c, "cur") if c.get("reg") == {0} and "m" in c else c
                for c in r.cubes
            ),
        )
        for r in rows
    )


@pytest.mark.parametrize(
    "mutate", ["drop-intact-from-registered", "drop-not-cur-from-pre"]
)
def test_table_twins_overlap(mutate: str) -> None:
    assert overlaps(mutated(ROWS, mutate)) != []


def _cube(**facts: object) -> Cube:
    return {k: frozenset({v}) for k, v in facts.items()}


def _pre(**facts: object) -> Cube:
    return {**it.PRE, **_cube(**facts)}


def _point_in(cube: Cube) -> dict[str, object]:
    """A point of `cube`: its first allowed value, the first domain value elsewhere."""
    return {
        k: sorted(cube[k], key=list(d).index)[0] if k in cube else d[0]
        for k, d in DOMAINS.items()
    }


ROW_EXAMPLES = [
    pytest.param(_point_in(c), r.phase, id=f"{r.phase}-{i}")
    for r in ROWS
    for i, c in enumerate(r.cubes)
] + [
    # outside every row: always pre-registration
    pytest.param(
        _point_in(_pre(m="built", r="absent", S=0)), "unknown", id="built-no-tree"
    ),
    pytest.param(
        _point_in(_pre(m="none", r="root-700", S=0)), "unknown", id="none-root-tree"
    ),
    pytest.param(
        _point_in(_pre(m="copied", r="root-755", full=0)),
        "unknown",
        id="copied-not-full",
    ),
    pytest.param(
        _point_in(_pre(m="admin", r="b-full", S=1)), "unknown", id="admin-builder"
    ),
]


@pytest.mark.parametrize("facts,phase", ROW_EXAMPLES)
def test_classify_rows(facts: dict[str, object], phase: str) -> None:
    assert classify(facts) == phase
    assert row_of(phase).phase == phase


def test_classify_accepts_bool_facts() -> None:
    point = _point_in(row_of("launched").cubes[0])
    facts = {k: bool(v) if DOMAINS[k] == (0, 1) else v for k, v in point.items()}
    assert classify(facts) == "launched"


def test_unknown_row() -> None:
    assert row_of("unknown") is UNKNOWN_ROW
    assert UNKNOWN_ROW == Row("unknown", (), "cleanup", ("none",))
    assert UNKNOWN_ROW.phase not in {r.phase for r in ROWS}
    with pytest.raises(KeyError):
        row_of("nonexistent")


def test_classify_overlap_is_program_error(monkeypatch: pytest.MonkeyPatch) -> None:
    twin = mutated(ROWS, "drop-intact-from-registered")
    monkeypatch.setattr(it, "ROWS", twin)
    point = _point_in(_cube(P=1, reg=1, intact=0, cur=1, L=1))
    with pytest.raises(AssertionError):
        it.classify(point)


def test_spec_route_table_is_rendering_of_data(spec: str) -> None:
    assert block(spec, "install-routes") == render_routes()


def test_spec_protocol_table_is_rendering_of_data(spec: str) -> None:
    assert block(spec, "install-protocols") == render_protocols()


def test_spec_settle_table_is_rendering_of_data(spec: str) -> None:
    assert block(spec, "install-settle") == render_settle()


def test_plan_route_calls_is_rendering_of_data() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    assert block(plan, "install-route-calls") == render_route_calls()


def test_route_leaves_have_one_kind() -> None:
    check_data()
    leaves = it.route_leaves()
    assert all((c in it.PROTOCOLS) != (c in it.CALLS) for c in leaves)
    assert set(it.ABSENT_OK) <= set(it.PROTOCOLS)
    settled = set(settle_dirs())
    assert all(set(it.GUARD_SOURCES[g]) <= settled for g in it.GUARD_NAMES)
    assert it.GUARD_NAMES == {g for st in it.ROUTES.values() for g, _, _ in st if g}


def _drop(
    sources: Mapping[str, tuple[str, ...]], fact: str, d: str
) -> dict[str, tuple[str, ...]]:
    return {
        f: tuple(x for x in ds if x != d) if f == fact else ds
        for f, ds in sources.items()
    }


def test_twin_a_without_admin_python_fails() -> None:
    with pytest.raises(AssertionError, match="admin_python"):
        check_data(fact_sources=_drop(FACT_SOURCES, "A", "admin_python"))


TWINS = {
    "leaf-both-protocol-and-call": ("CALLS", {**it.CALLS, "seal": ("check", "")}),
    "leaf-neither": ("CALLS", {k: v for k, v in it.CALLS.items() if k != "run_build"}),
    "guard-without-text": (
        "GUARD_TEXT",
        {k: v for k, v in it.GUARD_TEXT.items() if k != "S=tree"},
    ),
    "guard-source-not-settled": (
        "GUARD_SOURCES",
        {**it.GUARD_SOURCES, "S=tree": ("quarantine",)},
    ),
    "absent-ok-not-protocol": ("ABSENT_OK", {**it.ABSENT_OK, "run_build": "x"}),
    "absent-ok-without-absent-variant": (
        "ABSENT_OK",
        {**it.ABSENT_OK, "remove_durably": "x"},
    ),
    "absent-variant-with-root-change": (
        "PROTOCOLS",
        {
            **it.PROTOCOLS,
            "cleanup_tree": {
                **it.variants("cleanup_tree"),
                "absent": (("sync_dir", "parent", ""),),
            },
        },
    ),
    "variant-without-condition": (
        "VARIANT_WHEN",
        {**it.VARIANT_WHEN, "seal": {"new": "x"}},
    ),
    "condition-without-variants": (
        "VARIANT_WHEN",
        {**it.VARIANT_WHEN, "verify_chmod": {"x": "y"}},
    ),
    "not-modeled-unknown-variant": (
        "NOT_MODELED",
        {**it.NOT_MODELED, ("cleanup_tree", "gone"): "x"},
    ),
    "op-not-in-ops": (
        "PROTOCOLS",
        {**it.PROTOCOLS, "verify_chmod": (("chmod", "x", ""),)},
    ),
    "sync-of-unnamed-dir": (
        "PROTOCOLS",
        {**it.PROTOCOLS, "verify_chmod": (("sync_dir", "nowhere", ""),)},
    ),
    "enotempty-as-tree": (
        "cleanup_tree_variant",
        lambda obj, cl, rm, lb: (
            "tree"
            if obj == "dir" and cl == "ok"
            else (obj if obj != "dir" else ("quarantine" if lb else "failed"))
        ),
    ),
}


#: The fragment of check_data's message each twin must trigger.
TWIN_CAUSES = {
    "leaf-both-protocol-and-call": "'seal'",
    "leaf-neither": "'run_build'",
    "guard-without-text": "guard S=tree",
    "guard-source-not-settled": "guard S=tree",
    "absent-ok-not-protocol": "ABSENT_OK run_build",
    "absent-ok-without-absent-variant": "remove_durably: no variant absent",
    "absent-variant-with-root-change": "cleanup_tree: no variant absent",
    "variant-without-condition": "seal: conditions",
    "condition-without-variants": "verify_chmod: VARIANT_WHEN without variants",
    "not-modeled-unknown-variant": "NOT_MODELED ('cleanup_tree', 'gone')",
    "op-not-in-ops": "verify_chmod/None: op chmod",
    "sync-of-unnamed-dir": "verify_chmod/None: sync of nowhere",
    "enotempty-as-tree": "cleanup_tree: ENOTEMPTY is not a failed cleanup",
}


@pytest.mark.parametrize("twin", sorted(TWINS))
def test_check_data_twins_fail(monkeypatch: pytest.MonkeyPatch, twin: str) -> None:
    attr, value = TWINS[twin]
    monkeypatch.setattr(it, attr, value)
    with pytest.raises(AssertionError, match=re.escape(TWIN_CAUSES[twin])):
        check_data()


def test_every_fact_has_durability_sources() -> None:
    assert set(FACT_SOURCES) == set(DOMAINS)
    assert all(FACT_SOURCES[f] and set(FACT_SOURCES[f]) <= set(DIRS) for f in DOMAINS)


def test_acquire_dirs_cover_table_and_account() -> None:
    assert set(acquire_dirs()) == set(settle_dirs()) | {
        d for ds in ACCOUNT_SOURCES.values() for d in ds
    }
    assert "home_parent" in acquire_dirs()
    assert "home_parent" not in settle_dirs()
    assert list(acquire_dirs()) == [d for d in DIRS if d in acquire_dirs()]


def test_leaf_variants_and_effects() -> None:
    assert it.leaf_effects("write_mark") == ("rename", "sync_dir(parent)")
    assert it.leaf_effects("reap_user_processes") == ("process(df-build)",)
    assert it.leaf_effects("check_links") == ()
    assert it.leaf_variants("seal")["reuse"] == (
        "renamed",
        "sync_dir(releases)",
        "sync_dir(staging)",
    )
    with pytest.raises(AssertionError):
        it.leaf_effects("cleanup_tree")


def test_cleanup_tree_cases_cover_selection() -> None:
    assert {v for _, v in it.CLEANUP_TREE_CASES} == set(it.variants("cleanup_tree"))
    assert set(it.CLEANUP_OUTCOME) == set(it.variants("cleanup_tree"))
    assert it.CLEANUP_OUTCOME == {
        "tree": "removed",
        "link": "removed",
        "absent": "removed",
        "quarantine": "quarantined",
        "failed": "failed",
    }
    assert len(it.CLEANUP_TREE_CASES) == 2 + 2 + 2 * (2 + 1 + 1)
    assert it.cleanup_tree_variant("dir", "ok", "ENOTEMPTY", False) == "failed"
