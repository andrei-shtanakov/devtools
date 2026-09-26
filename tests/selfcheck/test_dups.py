"""Task 11 — ast-dup and cli-overlap (§3.3, §2.1 participant identity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.dups import AST_DUP, CLI_OVERLAP, dup_findings, function_hashes, overlaps
from selfcheck.env import EnvInfo
from selfcheck.model import Confidence
from selfcheck.probes.base import (
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from tests.selfcheck.helpers import NOW, make_repo

BODY = "".join(f"    {n} = {n}_src + {i}\n" for i, n in enumerate("abcdefg"))


def func(
    name: str, threshold: int = 5, var: str = "x", deco: str = "", ann: str = ""
) -> str:
    sig = (
        f"def {name}({var}{ann}, a_src=0, b_src=0, c_src=0, d_src=0, e_src=0, "
        f"f_src=0, g_src=0):\n"
    )
    return (
        f'{deco}{sig}    """doc"""\n{BODY.replace("a_src", var)}'
        f"    return a if a > {threshold} else b\n"
    )


def hashes(*sources: tuple[str, str]) -> list:
    return [h for src, path in sources for h in function_hashes(src, path)]


def test_exact_ignores_local_names_and_docstrings() -> None:
    one = function_hashes(func("one", var="x"), "a.py")[0]
    two = function_hashes(
        func("two", var="y").replace('"""doc"""', '"""other"""'), "b.py"
    )[0]
    assert one.exact == two.exact


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (func("one"), func("two", deco="@cache\n")),
        (func("one"), func("two", ann=": int")),
    ],
)
def test_decorator_or_annotation_difference_is_not_exact(left: str, right: str) -> None:
    rules = [
        f.rule for f in dup_findings(hashes((left, "a.py"), (right, "b.py")), "repo")
    ]
    assert "ast-dup/exact" not in rules


def test_structural_only_when_literals_differ() -> None:
    found = dup_findings(
        hashes((func("one", 5), "a.py"), (func("two", 9), "b.py")), "repo"
    )
    assert [(f.rule, f.confidence) for f in found] == [
        ("ast-dup/structural", Confidence.CANDIDATE)
    ]
    assert any("9" in e["detail"] for e in found[0].evidence)


def test_exact_group_members_carry_qualname() -> None:
    found = dup_findings(
        hashes(*[(func(f"f{i}"), f"m{i}.py") for i in range(3)]), "repo"
    )
    assert [(f.rule, f.occurrences, f.confidence) for f in found] == [
        ("ast-dup/exact", 3, Confidence.CONFIRMED)
    ]
    assert {r["member"] for r in found[0].related} == {"f0", "f1", "f2"}
    assert found[0].text_key is None and found[0].severity == "medium"


def test_short_and_different_functions_ignored() -> None:
    short = "def s(x):\n    return x\n"
    other = func("o").replace("+", "-")
    assert (
        dup_findings(
            hashes(
                (short, "a.py"), (short, "b.py"), (func("f"), "c.py"), (other, "d.py")
            ),
            "repo",
        )
        == []
    )


@pytest.mark.parametrize(
    ("a", "b", "hit"),
    [
        (
            {"--a", "--b", "--c", "--d"},
            {"--a", "--b", "--c", "--d", "--e"},
            True,
        ),  # 0.8
        ({"--a", "--b", "--c"}, {"--a", "--b", "--c", "--d"}, False),  # 0.75
        ({"--a"}, {"--a"}, False),  # |∩| = 1
    ],
)
def test_jaccard_threshold(a: set[str], b: set[str], hit: bool) -> None:
    assert overlaps(frozenset(a), frozenset(b)) is hit


def run(spec: ProbeSpec, tmp: Path, files: dict[str, str]) -> ProbeResult:
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(spec, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_ast_dup_probe(tmp_path: Path) -> None:
    res = run(AST_DUP, tmp_path, {"a.py": func("one"), "b.py": func("two")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert [f.rule for f in res.findings] == ["ast-dup/exact"]


PARSER = (
    "import argparse\n\n\ndef {n}():\n    p = argparse.ArgumentParser()\n"
    "    p.add_argument('--repo')\n    p.add_argument('--owner')\n"
    "    p.add_argument('--number')\n    p.add_argument('{extra}')\n    return p\n"
)


def test_cli_overlap_parsers_and_make_recipes(tmp_path: Path) -> None:
    res = run(
        CLI_OVERLAP,
        tmp_path,
        {
            "a.py": PARSER.format(n="a", extra="--mode"),
            "b.py": PARSER.format(n="b", extra="--mode"),
            "c.py": PARSER.format(n="c", extra="--other").replace("--owner", "--x"),
            "Makefile": (
                "one: ; @python3 ./a.py $(ARGS)\ntwo: ; @python3 ./a.py $(X)\n"
                "three: ; @python3 ./b.py\n"
            ),
        },
    )
    assert res.status is ProbeStatus.OK
    assert sorted(f.anchor.split(":")[1] for f in res.findings) == ["cli", "make"]
    make = next(f for f in res.findings if f.anchor.startswith("dup:make:"))
    assert make.occurrences == 2 and {r["member"] for r in make.related} == {
        "one",
        "two",
    }
    assert {f.severity for f in res.findings} == {"medium"}
    assert {f.confidence for f in res.findings} == {Confidence.CANDIDATE}


@pytest.mark.parametrize("spec", [AST_DUP, CLI_OVERLAP], ids=lambda s: s.name)
def test_parse_error_is_partial(spec: ProbeSpec, tmp_path: Path) -> None:
    res = run(spec, tmp_path, {"a.py": func("one"), "bad.py": "def f(:\n"})
    assert res.status is ProbeStatus.PARTIAL and "bad.py" in res.coverage["skipped"]
