"""Task 7 — shell, workflow and text-clone probes (§3.1)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.probes.base import (
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from selfcheck.probes.other_tools import (
    ACTIONLINT,
    JSCPD,
    OTHER_PROBES,
    SHELLCHECK,
    ZIZMOR,
    shell_files,
)
from tests.selfcheck.helpers import make_repo, require_npx_package, require_tool

WORKFLOW = """on: push
permissions: {}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
BODY = "".join(f"    v{i} = a + {i}\n" for i in range(10))
DUP = f"def one(a):\n{BODY}    return a\n\n\ndef two(a):\n{BODY}    return a\n"
CLEAN = {
    "run.sh": '#!/bin/sh\necho "$1"\n',
    "tool": '#!/usr/bin/env bash\nset -eu\necho "ok"\n',
    ".github/workflows/ci.yml": WORKFLOW,
    "m.py": "def f(a):\n    return a\n",
}


@pytest.fixture
def build(tmp_path: Path):
    copies: list[Path] = []

    def _build(files: dict[str, str]) -> RepoTarget:
        repo = make_repo(tmp_path / "repo", files)
        corpus = tuple(list_corpus(repo))
        copy = tmp_path / "run" / "src" / "repo"
        materialize(repo, corpus, copy, canary_files(OTHER_PROBES))
        copies.append(copy)
        return RepoTarget("repo", repo, copy, frozenset(), corpus, EnvInfo("no-env"))

    yield _build
    for copy in copies:
        release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    if spec is JSCPD or spec.name == "jscpd":
        require_npx_package("jscpd@4.3.0")
    else:
        require_tool(spec.binary or "")
    return run_probe(spec, target, tmp / "run" / "work")


@pytest.mark.parametrize("spec", OTHER_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(
    spec: ProbeSpec, build, tmp_path: Path
) -> None:
    res = run(spec, build(CLEAN), tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit" and res.findings == []


def test_shell_selection_by_suffix_and_shebang(build) -> None:
    target = build(
        {**CLEAN, "script.py": "#!/usr/bin/env python3\n", "notes": "plain\n"}
    )
    assert set(shell_files(target)) == {"run.sh", "tool"}


def test_shellcheck_finds_unquoted(build, tmp_path: Path) -> None:
    res = run(SHELLCHECK, build({"x.sh": "#!/bin/sh\necho $1\n"}), tmp_path)
    assert [f.rule for f in res.findings] == ["shellcheck/SC2086"]


def test_workflow_injection(build, tmp_path: Path) -> None:
    bad = WORKFLOW.replace("echo ok", "echo ${{ github.event.head_commit.message }}")
    target = build({".github/workflows/ci.yml": bad})
    assert {f.rule for f in run(ACTIONLINT, target, tmp_path).findings} == {
        "actionlint/expression"
    }
    assert "zizmor/template-injection" in {
        f.rule for f in run(ZIZMOR, target, tmp_path).findings
    }


def test_jscpd_clone_reported_coverage(build, tmp_path: Path) -> None:
    res = run(JSCPD, build({"d.py": DUP, "e.py": "x = 1\n"}), tmp_path)
    assert res.status is ProbeStatus.OK  # e.py (< 5 lines) is not "unprocessed"
    assert res.coverage["unprocessed"] == []
    assert [f.anchor.split(":")[1] for f in res.findings] == ["text"]
    clone = res.findings[0]
    assert (clone.occurrences, clone.severity, clone.confidence.value) == (
        2,
        "medium",
        "likely",
    )


BROKEN = {
    "shellcheck": ("bad.sh", "#!/bin/sh\nif then fi (\n"),
    "actionlint": (".github/workflows/bad.yml", "on: [push\njobs: {\n"),
    "zizmor": (".github/workflows/bad.yml", "on: [push\njobs: {\n"),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_parse_error_in_one_file_is_partial(name: str, build, tmp_path: Path) -> None:
    spec = next(s for s in OTHER_PROBES if s.name == name)
    path, text = BROKEN[name]
    res = run(spec, build({**CLEAN, path: text}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL, (res.status, res.reason)
    assert path in res.coverage["skipped"] + res.coverage.get("unprocessed", [])


@pytest.mark.parametrize("spec", OTHER_PROBES, ids=lambda s: s.name)
def test_canary_rule_disabled_in_registry_is_missed(
    spec: ProbeSpec, build, tmp_path: Path
) -> None:
    broken = replace(spec, canary=replace(spec.canary, expect_rule="never/rule"))
    res = run(broken, build(CLEAN), tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


# ---- regressions found by the S1 acceptance run on devtools (2026-09-26) --------


def test_jscpd_copy_inside_gitignored_dir(tmp_path: Path) -> None:
    """The real copy lives in devtools/out/ (gitignored): jscpd must not skip it."""
    require_npx_package("jscpd@4.3.0")
    outer = make_repo(tmp_path / "outer", {".gitignore": "out/\n"})
    repo = make_repo(tmp_path / "repo", {"d.py": DUP})
    corpus = tuple(list_corpus(repo))
    copy = outer / "out" / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files(OTHER_PROBES))
    target = RepoTarget("repo", repo, copy, frozenset(), corpus, EnvInfo("no-env"))
    try:
        res = run_probe(JSCPD, target, outer / "out" / "run" / "work")
    finally:
        release(copy)
    assert res.status is ProbeStatus.OK, res.reason


def test_shellcheck_directive_warning_is_not_a_parse_error(
    build, tmp_path: Path
) -> None:
    source = '#!/bin/sh\n# shellcheck disable=SC2086 reason: x\necho "$1"\n'
    res = run(SHELLCHECK, build({"d.sh": source}), tmp_path)
    assert res.status is ProbeStatus.OK, (res.reason, res.diagnostics)


def test_jscpd_processes_large_files(build, tmp_path: Path) -> None:
    big = "".join(f"value_{i} = {i} * {i}\n" for i in range(1500))
    res = run(JSCPD, build({"big.py": big, "d.py": DUP}), tmp_path)
    assert res.status is ProbeStatus.OK, (res.reason, res.coverage.get("unprocessed"))


def test_odd_file_names_keep_probes_ok(build, tmp_path: Path) -> None:
    """Final review I7: spaces, parentheses and non-ASCII in real probe inputs."""
    target = build(
        {
            ".github/workflows/my flow.yml": WORKFLOW,
            "pkg/a (copy).py": DUP,
            "pkg/ю.py": DUP.replace("one", "три"),
        }
    )
    for spec in (ZIZMOR, JSCPD):
        res = run(spec, target, tmp_path / spec.name)
        assert res.status is ProbeStatus.OK, (spec.name, res.reason, res.coverage)
