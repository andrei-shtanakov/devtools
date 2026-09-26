"""Task 1 — finding identity, spec §2.1 forms C1–C10."""

from __future__ import annotations

import random

from selfcheck.anchors import python_anchor
from selfcheck.model import (
    Confidence,
    Finding,
    Location,
    aggregate,
    cap,
    finding_id,
    make_text_key,
)

SRC = """def alpha():
    x = eval("1")
    y = eval("1")
    return x + y


def beta():
    return eval("2")
"""


def raw(rule: str, source: str, line: int, path: str = "m.py") -> Finding:
    text = source.splitlines()[line - 1]
    return Finding(
        rule=rule,
        category="bug",
        severity="medium",
        confidence=Confidence.LIKELY,
        owner_repo="devtools",
        anchor=python_anchor(source, path, line),
        locations=[Location(path, line)],
        text_key=make_text_key(text),
    )


def ids(findings: list[Finding]) -> set[str]:
    return {f.id for f in aggregate(findings)}


def test_c1_insert_above_keeps_id() -> None:
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", "\n\n" + SRC, 10).id


def test_c2_c3_identical_lines_aggregate() -> None:
    same = SRC.replace('    y = eval("1")', '    x = eval("1")')
    two = aggregate([raw("ruff/S307", same, 2), raw("ruff/S307", same, 3)])
    one = aggregate([raw("ruff/S307", SRC.replace('    y = eval("1")\n', ""), 2)])
    assert len(two) == 1 and two[0].occurrences == 2
    assert two[0].id == one[0].id and one[0].occurrences == 1


def test_c4_changed_text_changes_id() -> None:
    changed = SRC.replace('return eval("2")', 'return eval("3")')
    assert raw("ruff/S307", SRC, 8).id != raw("ruff/S307", changed, 8).id


def test_c5_c6_new_anchor_new_id() -> None:
    renamed = SRC.replace("def beta", "def gamma")
    assert raw("ruff/S307", SRC, 8).anchor == "func:m.py::beta"
    assert raw("ruff/S307", renamed, 8).id != raw("ruff/S307", SRC, 8).id


def test_c7_two_rules_two_findings() -> None:
    assert len(ids([raw("ruff/S307", SRC, 8), raw("pyrefly/x", SRC, 8)])) == 2


def test_c8_probe_order_irrelevant() -> None:
    items = [raw("ruff/S307", SRC, n) for n in (2, 3, 8)]
    shuffled = items[:]
    random.Random(1).shuffle(shuffled)
    assert ids(items) == ids(shuffled)


def test_c9_whitespace_inside_line_ignored() -> None:
    spaced = SRC.replace('return eval("2")', 'return   eval( "2")'.replace("( ", "("))
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", spaced, 8).id


def test_c10_dup_id_excludes_owner() -> None:
    assert finding_id("ast-dup/exact", "devtools", "dup:exact:abc", None) == finding_id(
        "ast-dup/exact", "maestro", "dup:exact:abc", None
    )


def test_module_level_and_broken_source_get_file_anchor() -> None:
    assert python_anchor("x = 1\n", "m.py", 1) == "file:m.py"
    assert python_anchor("def f(:\n", "m.py", 1) == "file:m.py"


def test_cap_takes_lower() -> None:
    assert cap(Confidence.CONFIRMED, Confidence.LIKELY) is Confidence.LIKELY
    assert cap(Confidence.CANDIDATE, Confidence.LIKELY) is Confidence.CANDIDATE
