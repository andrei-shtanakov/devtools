"""S5 — the judge (spec §5, §11)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.config import load_config
from selfcheck.corpus import list_corpus
from selfcheck.judge import clone_lines, is_candidate, order_key
from selfcheck.model import Confidence, Finding, Location
from tests.selfcheck.helpers import make_repo


def _f(
    rule: str,
    conf: Confidence,
    repos: tuple[str, ...] = ("a",),
    path: str = "x.py",
    line: int = 1,
    lines: int | None = None,
) -> Finding:
    category = "llm-replaceable" if rule.startswith("llm") else "duplicate"
    members = repos * (2 if len(repos) == 1 else 1)
    related = [
        {"owner_repo": r, "path": path, "line": line + i, "member": str(i)}
        for i, r in enumerate(members)
    ]
    return Finding(
        rule=rule,
        category=category,
        severity="medium",
        confidence=conf,
        owner_repo=repos[0],
        anchor=f"{rule}:{path}:{line}:{lines}:{'-'.join(repos)}",
        locations=[Location(path, line)],
        related=related if category == "duplicate" else [],
        evidence=[{"kind": "lines", "detail": str(lines)}] if lines else [],
    )


def test_candidates_are_llm_and_dups_below_confirmed() -> None:
    assert is_candidate(_f("llm-sites/replaceable", Confidence.CANDIDATE))
    assert is_candidate(_f("jscpd/clone", Confidence.LIKELY, lines=9))
    assert not is_candidate(_f("ast-dup/exact", Confidence.CONFIRMED))
    other = _f("ruff/F401", Confidence.LIKELY)
    other.category = "quality"
    assert not is_candidate(other)


def test_order_is_normative() -> None:
    fs = [
        _f("jscpd/clone", Confidence.LIKELY, lines=10),
        _f("jscpd/clone", Confidence.LIKELY, lines=40),
        _f("cli-overlap/argparse", Confidence.CANDIDATE),
        _f("ast-dup/structural", Confidence.CANDIDATE),
        _f("llm-sites/replaceable", Confidence.CANDIDATE),
        _f("jscpd/clone", Confidence.LIKELY, repos=("a", "b"), lines=5),
        _f("ast-dup/structural", Confidence.CANDIDATE, repos=("a", "b")),
    ]
    got = [(f.rule, clone_lines(f)) for f in sorted(fs, key=order_key)]
    assert got == [
        ("ast-dup/structural", 0),  # cross-repo, kind before level
        ("jscpd/clone", 5),
        ("llm-sites/replaceable", 0),  # intra: llm → ast → cli → jscpd
        ("ast-dup/structural", 0),
        ("cli-overlap/argparse", 0),
        ("jscpd/clone", 40),  # longest clone first
        ("jscpd/clone", 10),
    ]


def test_obsidian_plugins_are_out_of_the_corpus(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[2] / "selfcheck.toml")
    repo = make_repo(
        tmp_path / "vault",
        {
            ".obsidian/plugins/p/main.js": "x();\n",
            ".obsidian/app.json": "{}",
            "a.md": "#\n",
        },
    )
    corpus = list_corpus(repo, config.corpus_exclude)
    assert ".obsidian/plugins/p/main.js" not in corpus and "a.md" in corpus
