"""Task 6 — Python static probes on a read-only copy (§3.1, §1.5, §4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import detect_env
from selfcheck.model import Confidence
from selfcheck.probes.base import (
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from selfcheck.probes.python_tools import (
    DEPTRY,
    PYREFLY,
    PYTHON_PROBES,
    RADON,
    RUFF,
    VULTURE,
)
from tests.selfcheck.helpers import (
    fake_venv,
    make_repo,
    mi_rank_c_source,
    require_probe,
)

# [tool.ruff] cuts off any user-level ruff config on the test machine (review r2 m9).
PYPROJECT = (
    '[project]\nname = "x"\nversion = "0"\ndependencies = ["pyyaml>=6"]\n[tool.ruff]\n'
)


@pytest.fixture
def build(tmp_path: Path):
    copies: list[Path] = []

    def _build(
        files: dict[str, str],
        *,
        venv_marker: Path | None = None,
        pyproject: str = PYPROJECT,
    ) -> RepoTarget:
        n = len(copies)  # one repo and one copy per call
        repo = make_repo(
            tmp_path / f"repo{n}",
            {"pyproject.toml": pyproject, ".gitignore": ".venv/\n", **files},
        )
        if venv_marker is not None:
            fake_venv(repo, evil_marker=venv_marker)
        corpus = tuple(list_corpus(repo))
        copy = tmp_path / "run" / "src" / f"repo{n}"
        materialize(repo, corpus, copy, canary_files(PYTHON_PROBES))
        copies.append(copy)
        return RepoTarget(
            "repo", repo, copy, frozenset({"python"}), corpus, detect_env(repo)
        )

    yield _build
    for copy in copies:
        release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    require_probe(spec.binary or "", spec.version_args, spec.version_range)
    return run_probe(spec, target, tmp / "run" / "work")


CLEAN = {
    "pkg/__init__.py": "",
    "pkg/m.py": (
        "import yaml\n\n\ndef load(text: str) -> object:\n    return yaml.safe_load(text)\n"
    ),
}


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(
    spec: ProbeSpec, build, tmp_path: Path
) -> None:
    res = run(spec, build(CLEAN), tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit"


def test_ruff_reports_repo_violation(build, tmp_path: Path) -> None:
    res = run(RUFF, build({"a.py": "import os\n"}), tmp_path)
    assert [(f.rule, f.anchor, f.confidence) for f in res.findings] == [
        ("ruff/F401", "file:a.py", Confidence.LIKELY)
    ]


def test_vulture_confidence_scale(build, tmp_path: Path) -> None:
    source = "import os\n\n\ndef f():\n    return 1\n    print('x')\n"
    conf = {
        f.rule: f.confidence
        for f in run(VULTURE, build({"u.py": source}), tmp_path).findings
    }
    assert conf["vulture/unused-import"] is Confidence.CANDIDATE  # 90 %
    assert conf["vulture/unreachable-code-after"] is Confidence.LIKELY  # 100 %


def test_ruff_cli_restores_ignored_canary_rule(build, tmp_path: Path) -> None:
    res = run(
        RUFF,
        build({"ruff.toml": '[lint]\nignore = ["F401"]\n', "a.py": "x = 1\n"}),
        tmp_path,
    )
    assert res.status is ProbeStatus.OK


def test_ruff_per_file_ignore_suppresses_canary(build, tmp_path: Path) -> None:
    target = build(
        {
            "ruff.toml": '[lint.per-file-ignores]\n".selfcheck-canary/**" = ["F401"]\n',
            "a.py": "x = 1\n",
        }
    )
    res = run(RUFF, target, tmp_path)
    assert (res.status, res.reason) == (
        ProbeStatus.FAILED,
        "canary-suppressed-by-config",
    )


def test_vulture_syntax_error_on_stderr_is_partial(build, tmp_path: Path) -> None:
    res = run(VULTURE, build({"bad.py": "def f(:\n"}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL
    assert "bad.py" in res.coverage["skipped"]


def test_pyrefly_default_preset_without_own_config(build, tmp_path: Path) -> None:
    # bad-assignment is silent under the `basic` preset and is not the canary rule
    res = run(PYREFLY, build({"a.py": 'x: int = "s"\n'}), tmp_path)
    assert "pyrefly/bad-assignment" in {f.rule for f in res.findings}
    assert "--preset" in res.argv


def test_pyrefly_configured_repo_keeps_its_preset(build, tmp_path: Path) -> None:
    res = run(PYREFLY, build({"a.py": "x = 1\n", "pyrefly.toml": "\n"}), tmp_path)
    assert res.status is ProbeStatus.OK and "--preset" not in res.argv


def test_radon_cc_and_mi(build, tmp_path: Path) -> None:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(22))
    target = build(
        {
            "c.py": f"def big(x: int) -> int:\n{body}    return -1\n",
            "t.py": mi_rank_c_source(),
        }
    )
    rules = {(f.rule, f.anchor) for f in run(RADON, target, tmp_path).findings}
    assert ("radon/cc-D", "func:c.py::big") in rules
    assert ("radon/mi-C", "file:t.py") in rules


def test_deptry_env_as_data(build, tmp_path: Path) -> None:
    marker = tmp_path / "EXECUTED"
    target = build({"a.py": "import yaml\n"}, venv_marker=marker)
    deptry, pyrefly = run(DEPTRY, target, tmp_path), run(PYREFLY, target, tmp_path)
    assert not marker.exists(), "target startup hooks must not run"
    assert deptry.status is ProbeStatus.OK and pyrefly.status is ProbeStatus.OK
    assert not any(f.rule == "deptry/DEP003" for f in deptry.findings)
    assert not any("'yaml'" in e["detail"] for f in deptry.findings for e in f.evidence)
    assert any("DEP003 off" in note for note in deptry.coverage["notes"])


def test_deptry_dep002_per_package_distinct(build, tmp_path: Path) -> None:
    pyproject = (
        '[project]\nname = "x"\nversion = "0"\n'
        'dependencies = ["pyyaml>=6", "requests>=2"]\n'
    )
    res = run(DEPTRY, build({"a.py": "x = 1\n"}, pyproject=pyproject), tmp_path)
    dep002 = [f for f in res.findings if f.rule == "deptry/DEP002"]
    assert len(dep002) == 2 and len({f.id for f in dep002}) == 2


def test_deptry_excludes_really_apply(build, tmp_path: Path) -> None:
    pyproject = PYPROJECT + '[tool.deptry]\nextend_exclude = ["legacy"]\n'
    undeclared = "import undeclared_mod\n"
    target = build(
        {
            "a.py": "import yaml\n",
            "legacy/x.py": undeclared,
            "tests/test_x.py": undeclared,
        },
        pyproject=pyproject,
    )
    res = run(DEPTRY, target, tmp_path)
    assert res.coverage["expected_files"] == ["a.py"]
    flagged = {loc.path for f in res.findings for loc in f.locations}
    assert not flagged & {"legacy/x.py", "tests/test_x.py"}
    # foreign canaries: findings there are subtracted anyway, so pin the traversal itself
    assert ".selfcheck-canary" in res.argv


def test_ruff_repo_config_enters_config_hash(build, tmp_path: Path) -> None:
    first = run(
        RUFF,
        build({"a.py": "x = 1\n", "ruff.toml": '[lint]\nignore = ["E501"]\n'}),
        tmp_path / "one",
    )
    second = run(
        RUFF, build({"a.py": "x = 1\n", "ruff.toml": "[lint]\n"}), tmp_path / "two"
    )
    assert first.config_hash and first.config_hash != second.config_hash


BROKEN = {
    "ruff": "bad.py",
    "pyrefly": "bad.py",
    "vulture": "bad.py",
    "radon": "bad.py",
    "deptry": "bad.py",
}


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_parse_error_in_one_file_is_partial(
    spec: ProbeSpec, build, tmp_path: Path
) -> None:
    res = run(spec, build({**CLEAN, BROKEN[spec.name]: "def f(:\n"}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL, (res.status, res.reason)
    assert BROKEN[spec.name] in res.coverage["skipped"]


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_canary_rule_disabled_in_registry_is_missed(
    spec: ProbeSpec, build, tmp_path: Path
) -> None:
    broken = replace(spec, canary=replace(spec.canary, expect_rule="never/rule"))
    res = run(broken, build(CLEAN), tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")
