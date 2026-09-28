"""lint-coverage — a repo language with no linter in its CI (spec §12)."""

from __future__ import annotations

import json
from pathlib import Path

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.lintcov import LINT_COVERAGE
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from tests.selfcheck.helpers import NOW, make_repo

WF = ".github/workflows/ci.yml"


def ci(*runs: str) -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return f"name: ci\non: [push]\njobs:\n  t:\n    runs-on: x\n    steps:\n{steps}"


def run(
    tmp: Path, files: dict[str, str], languages: set[str] | None = None
) -> ProbeResult:
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LINT_COVERAGE]))
    langs = frozenset({"python"} if languages is None else languages)
    target = RepoTarget("repo", repo, copy, langs, corpus, EnvInfo("no-env"), now=NOW)
    try:
        return run_probe(LINT_COVERAGE, target, tmp / "run" / "work")
    finally:
        release(copy)


def missing(res: ProbeResult) -> list[str]:
    return sorted(f.text_key or "" for f in res.findings)


def test_python_without_linter_is_a_finding(tmp_path: Path) -> None:
    res = run(tmp_path, {"pyproject.toml": "", WF: ci("uv run --frozen pytest -q")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    [f] = res.findings
    assert f.rule == "lint-coverage/missing" and f.anchor == "file:pyproject.toml"
    assert f.text_key == "python" and f.owner_repo == "repo"
    assert not any(e["kind"] == "s4-return" for e in f.evidence)


def test_ruff_check_covers_python(tmp_path: Path) -> None:
    files = {"pyproject.toml": "", WF: ci("uv run pytest", "uv run ruff check .")}
    assert run(tmp_path, files).findings == []


def test_formatter_is_not_a_linter(tmp_path: Path) -> None:
    files = {"pyproject.toml": "", WF: ci("uv run ruff format --check .")}
    assert missing(run(tmp_path, files)) == ["python"]


def test_commented_step_does_not_count(tmp_path: Path) -> None:
    wf = ci("uv run pytest") + "      # - run: uv run ruff check .\n"
    assert missing(run(tmp_path, {"pyproject.toml": "", WF: wf})) == ["python"]


def test_no_workflows_means_every_language_missing(tmp_path: Path) -> None:
    files = {"pyproject.toml": "", "Cargo.toml": ""}
    res = run(tmp_path, files, {"python", "rust"})
    assert missing(res) == ["python", "rust"]


def test_exec_languages_carry_the_s4_return_mark(tmp_path: Path) -> None:
    files = {"pyproject.toml": "", "Cargo.toml": "", WF: ci("uv run ruff check")}
    [f] = run(tmp_path, files, {"python", "rust"}).findings
    assert f.text_key == "rust" and f.anchor == "file:Cargo.toml"
    assert any(e["kind"] == "s4-return" for e in f.evidence)


def test_each_language_has_its_linters(tmp_path: Path) -> None:
    files = {
        "Cargo.toml": "",
        "mix.exs": "",
        "package.json": "{}",
        WF: ci("cargo clippy --all-targets -- -D warnings", "mix credo --strict")
        + "      - uses: some/eslint-action@v1\n",
    }
    assert run(tmp_path, files, {"rust", "elixir", "ts"}).findings == []


def test_make_target_is_followed_one_level(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": "",
        "Makefile": "test:\n\tuv run pytest\n\nlint: test\n\tuv run ruff check .\n",
        WF: ci("make -C . lint"),
    }
    assert run(tmp_path, files).findings == []


def test_unreached_make_target_does_not_count(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": "",
        "Makefile": "test:\n\tuv run pytest\nlint:\n\tuv run ruff check .\n",
        WF: ci("make test"),
    }
    assert missing(run(tmp_path, files)) == ["python"]


def test_npm_script_is_followed(tmp_path: Path) -> None:
    pkg = json.dumps({"scripts": {"check-types": "tsc --noEmit", "test": "vitest"}})
    files = {"package.json": pkg, WF: ci("npm ci", "npm run check-types")}
    assert run(tmp_path, files, {"ts"}).findings == []


def test_npm_test_alone_does_not_cover_ts(tmp_path: Path) -> None:
    pkg = json.dumps({"scripts": {"lint": "eslint .", "test": "vitest"}})
    files = {"package.json": pkg, WF: ci("npm ci", "npm test")}
    assert missing(run(tmp_path, files, {"ts"})) == ["ts"]


def test_pre_commit_ruff_hook_counts(tmp_path: Path) -> None:
    hooks = (
        "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.6.0\n    hooks:\n      - id: ruff\n      - id: ruff-format\n"
    )
    files = {
        "pyproject.toml": "",
        ".pre-commit-config.yaml": hooks,
        WF: ci("uvx pre-commit run --all-files"),
    }
    assert run(tmp_path, files).findings == []


def test_pre_commit_format_hook_alone_does_not_count(tmp_path: Path) -> None:
    hooks = (
        "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    hooks:\n      - id: ruff-format\n"
    )
    files = {
        "pyproject.toml": "",
        ".pre-commit-config.yaml": hooks,
        WF: ci("pre-commit run --all-files"),
    }
    assert missing(run(tmp_path, files)) == ["python"]


def test_external_reusable_workflow_is_named(tmp_path: Path) -> None:
    wf = (
        "on: [push]\njobs:\n  g:\n"
        "    uses: org/kit/.github/workflows/gate.yml@0123abcd\n"
    )
    res = run(tmp_path, {"pyproject.toml": "", WF: wf})
    assert missing(res) == ["python"]
    assert any("org/kit/.github/workflows/gate.yml" in n for n in res.coverage["notes"])


def test_repo_without_languages_is_skipped(tmp_path: Path) -> None:
    res = run(tmp_path, {"README.md": "x\n"}, set())
    assert res.status is ProbeStatus.SKIPPED
