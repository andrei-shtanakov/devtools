"""Tests for the expected distribution set of a lock (spec 3.5, Task 7a).

Synthetic locks are written inline; the real member locks and uv's own
installs are compared in the golden tests.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from df.inputs import InstalledDist
from df.lockset import (
    ExpectedDist,
    distribution_problems,
    expected_distributions,
    uv_config_problems,
)
from df.markers import marker_env
from df.sources import SourceId, UnsupportedLock

ROOT_DIR = Path("/build/tmp/rel1/src/repo")
MACOS = marker_env("macos-arm64", "3.12.13")
LINUX = marker_env("linux-x86_64", "3.12.13")
PYPI = 'source = { registry = "https://pypi.org/simple" }'
HEADER = 'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n'
PLAN_FIELDS_COMMIT = "f7f2cbcbbffc8d83870848317228c0a3487780ec"
PLAN_FIELDS_GIT = (
    "https://github.com/andrei-shtanakov/dispatcher"
    f"?subdirectory=packages%2Fplan-fields&rev={PLAN_FIELDS_COMMIT}"
    f"#{PLAN_FIELDS_COMMIT}"
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def pkg(name: str, version: str = "1.0", source: str = PYPI, body: str = "") -> str:
    return f'[[package]]\nname = "{name}"\nversion = "{version}"\n{source}\n{body}\n'


def root(body: str, kind: str = "editable") -> str:
    return pkg("app", "0.1.0", f'source = {{ {kind} = "." }}', body)


def lock(*packages: str, header: str = HEADER) -> bytes:
    return (header + "\n" + "\n".join(packages)).encode()


def registry(name: str, version: str = "1.0") -> ExpectedDist:
    return ExpectedDist(
        name, version, SourceId("registry", url="https://pypi.org/simple")
    )


ROOT_DIST = ExpectedDist(
    "app", "0.1.0", SourceId("directory", path=str(ROOT_DIR), editable=False)
)


def expected(
    data: bytes,
    groups: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> frozenset[ExpectedDist]:
    return expected_distributions(
        data, groups=groups, env=env or dict(MACOS), root_dir=ROOT_DIR
    )


# ---------- expected_distributions ----------


def test_virtual_root_excluded_editable_root_included() -> None:
    deps = 'dependencies = [{ name = "a" }]'
    virtual = lock(root(deps, kind="virtual"), pkg("a"))
    assert expected(virtual) == {registry("a")}
    editable = lock(root(deps, kind="editable"), pkg("a"))
    assert expected(editable) == {ROOT_DIST, registry("a")}
    directory = lock(root(deps, kind="directory"), pkg("a"))
    assert expected(directory) == {ROOT_DIST, registry("a")}


def test_transitive_and_markers_per_platform() -> None:
    data = lock(
        root(
            'dependencies = [{ name = "a" }, '
            '{ name = "lin", marker = "sys_platform == \'linux\'" }]'
        ),
        pkg(
            "a",
            body='dependencies = [{ name = "b", '
            "marker = \"python_full_version < '3.13'\" }, "
            '{ name = "win", marker = "sys_platform == \'win32\'" }]',
        ),
        pkg("b"),
        pkg("lin"),
        pkg("win"),
    )
    assert expected(data) == {ROOT_DIST, registry("a"), registry("b")}
    assert expected(data, env=dict(LINUX)) == {
        ROOT_DIST,
        registry("a"),
        registry("b"),
        registry("lin"),
    }
    py313 = dict(marker_env("macos-arm64", "3.13.1"))
    assert expected(data, env=py313) == {ROOT_DIST, registry("a")}


def test_extra_pulls_optional_dependencies() -> None:
    """pyjwt[crypto] -> cryptography; the extra is not taken without `extra`."""
    data = lock(
        root('dependencies = [{ name = "pyjwt", extra = ["crypto"] }]'),
        pkg(
            "pyjwt",
            "2.15.1",
            body='[package.optional-dependencies]\ncrypto = [{ name = "cryptography" }]',
        ),
        pkg("cryptography", "46.0.1"),
    )
    assert expected(data) == {
        ROOT_DIST,
        registry("pyjwt", "2.15.1"),
        registry("cryptography", "46.0.1"),
    }
    plain = data.replace(b', extra = ["crypto"]', b"")
    assert expected(plain) == {ROOT_DIST, registry("pyjwt", "2.15.1")}


def test_extra_after_plain() -> None:
    """F7a: a package reached first plain and later with `[e]` keeps `[e]`."""
    data = lock(
        root('dependencies = [{ name = "md" }, { name = "textual" }]'),
        pkg("textual", body='dependencies = [{ name = "md", extra = ["linkify"] }]'),
        pkg(
            "md",
            body='[package.optional-dependencies]\nlinkify = [{ name = "linkify" }]',
        ),
        pkg("linkify"),
    )
    assert registry("linkify") in expected(data)


def test_root_extra_not_selected() -> None:
    """F7b: the build passes no `--extra`, so root extras are not expected."""
    data = lock(
        root(
            'dependencies = [{ name = "a" }]\n'
            '[package.optional-dependencies]\ndev = [{ name = "cov" }]'
        ),
        pkg("a"),
        pkg("cov"),
    )
    assert expected(data) == {ROOT_DIST, registry("a")}


def test_group_entry_marker_and_extra() -> None:
    """F7c: group entries are evaluated like dependencies."""
    data = lock(
        root(
            "[package.dev-dependencies]\n"
            'dev = [{ name = "keyring", marker = "sys_platform == \'linux\'" }, '
            '{ name = "cov", extra = ["toml"] }]\n'
            'other = [{ name = "never" }]'
        ),
        pkg("keyring"),
        pkg("cov", body='[package.optional-dependencies]\ntoml = [{ name = "toml" }]'),
        pkg("toml"),
        pkg("never"),
    )
    assert expected(data) == {ROOT_DIST}
    assert expected(data, groups=("dev",)) == {
        ROOT_DIST,
        registry("cov"),
        registry("toml"),
    }
    assert expected(data, groups=("dev",), env=dict(LINUX)) == {
        ROOT_DIST,
        registry("keyring"),
        registry("cov"),
        registry("toml"),
    }


def test_candidate_selected_by_version_and_raw_source() -> None:
    """N2: two indexes are told apart by the raw table, before projection."""
    other = 'source = { registry = "https://example.org/simple" }'
    data = lock(
        root(
            'dependencies = [{ name = "a", version = "2.0", '
            'source = { registry = "https://example.org/simple" } }]'
        ),
        pkg("a", "2.0"),
        pkg("a", "2.0", other),
        pkg("a", "1.0"),
    )
    want = ExpectedDist(
        "a", "2.0", SourceId("registry", url="https://example.org/simple")
    )
    assert expected(data) == {ROOT_DIST, want}


def test_git_source_and_normalised_name() -> None:
    data = lock(
        root('dependencies = [{ name = "plan-fields" }, { name = "Zope.Interface" }]'),
        pkg("plan-fields", "0.1.0", f'source = {{ git = "{PLAN_FIELDS_GIT}" }}'),
        pkg("Zope.Interface", "7.0"),
    )
    git = SourceId(
        "git",
        url="https://github.com/andrei-shtanakov/dispatcher",
        subdirectory="packages/plan-fields",
        commit=PLAN_FIELDS_COMMIT,
    )
    assert expected(data) == {
        ROOT_DIST,
        ExpectedDist("plan-fields", "0.1.0", git),
        registry("zope-interface", "7.0"),
    }


@pytest.mark.parametrize(
    "extra_header",
    [
        "resolution-markers = [\"sys_platform == 'win32'\"]",
        "supported-markers = [\"sys_platform == 'darwin'\"]",
        "required-markers = [\"sys_platform == 'darwin'\"]",
        '[manifest]\nconstraints = [{ name = "a", specifier = "<2" }]',
        '[manifest]\noverrides = [{ name = "a", specifier = "<2" }]',
        "[options]",
    ],
)
def test_accepted_header_constructs(extra_header: str) -> None:
    data = lock(
        root('dependencies = [{ name = "a" }]'),
        pkg("a"),
        header=HEADER + extra_header + "\n",
    )
    assert expected(data) == {ROOT_DIST, registry("a")}


ROOT_OK = root('dependencies = [{ name = "a" }]')
REFUSALS: dict[str, bytes] = {
    "revision-4": lock(ROOT_OK, pkg("a"), header="version = 1\nrevision = 4\n"),
    "version-2": lock(ROOT_OK, pkg("a"), header="version = 2\nrevision = 3\n"),
    "no-header": lock(ROOT_OK, pkg("a"), header=""),
    "conflicts": lock(
        ROOT_OK,
        pkg("a"),
        header=HEADER + 'conflicts = [[{ package = "app", extra = "x" }]]\n',
    ),
    "options-present": lock(
        ROOT_OK, pkg("a"), header=HEADER + '[options]\nexclude-newer = "2026-01-01"\n'
    ),
    "manifest-dependency-metadata": lock(
        ROOT_OK,
        pkg("a"),
        header=HEADER + '[[manifest.dependency-metadata]]\nname = "a"\n',
    ),
    "manifest-members": lock(
        ROOT_OK, pkg("a"), header=HEADER + '[manifest]\nmembers = ["app"]\n'
    ),
    "unknown-top-key": lock(ROOT_OK, pkg("a"), header=HEADER + "frobnicate = 1\n"),
    "dep-extra-marker": lock(
        root('dependencies = [{ name = "a", marker = "extra == \'x\'" }]'), pkg("a")
    ),
    "ambiguous-duplicate": lock(
        ROOT_OK, pkg("a"), pkg("a", source='source = { registry = "https://x.org/s" }')
    ),
    "zero-candidates": lock(ROOT_OK),
    "version-mismatch": lock(
        root('dependencies = [{ name = "a", version = "2.0" }]'), pkg("a")
    ),
    "missing-group": lock(ROOT_OK, pkg("a")),
    "missing-extra": lock(
        root('dependencies = [{ name = "a", extra = ["x"] }]'), pkg("a")
    ),
    "two-roots": lock(ROOT_OK, pkg("a"), pkg("b", source='source = { virtual = "." }')),
    "no-root": lock(pkg("a")),
    "unknown-dependency-key": lock(
        root('dependencies = [{ name = "a", flavour = "x" }]'), pkg("a")
    ),
    # F5: constructs out of reach are refused too, on every platform.
    "unreached-bad-marker": lock(
        ROOT_OK,
        pkg("a"),
        pkg("z", body='dependencies = [{ name = "a", marker = "os_name < \'x\'" }]'),
    ),
    "unreached-bad-source": lock(
        ROOT_OK, pkg("a"), pkg("z", source='source = { path = "z.whl" }')
    ),
    "unreached-bad-optional": lock(
        ROOT_OK,
        pkg(
            "a",
            body="[package.optional-dependencies]\n"
            'x = [{ name = "a", marker = "platform_release == \'1\'" }]',
        ),
    ),
    "not-toml": b"version = 1\nrevision = \n",
    "not-utf8": HEADER.encode() + b"\n# \xff\n",
}


#: A fragment of each refusal: the case is refused for its own reason.
REASONS: dict[str, str] = {
    "revision-4": "revision 4",
    "version-2": "version 2",
    "no-header": "version None",
    "conflicts": "conflicts",
    "options-present": "[options] exclude-newer",
    "manifest-dependency-metadata": "[manifest] dependency-metadata",
    "manifest-members": "[manifest] members",
    "unknown-top-key": "frobnicate",
    "dep-extra-marker": "extra == 'x'",
    "ambiguous-duplicate": "кандидатов 2",
    "zero-candidates": "кандидатов 0",
    "version-mismatch": "кандидатов 0",
    "missing-group": "нет группы 'dev'",
    "missing-extra": "нет extra 'x'",
    "two-roots": "корней проекта 2",
    "no-root": "корней проекта 0",
    "unknown-dependency-key": "flavour",
    "unreached-bad-marker": "os_name < 'x'",
    "unreached-bad-source": "z.whl",
    "unreached-bad-optional": "platform_release",
    "not-toml": "TOML",
    "not-utf8": "utf-8",
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_lock_refusals(case: str) -> None:
    groups = ("dev",) if case == "missing-group" else ()
    with pytest.raises(UnsupportedLock) as exc:
        expected(REFUSALS[case], groups=groups)
    assert REASONS[case] in str(exc.value)


def test_every_refusal_has_a_reason() -> None:
    assert set(REASONS) == set(REFUSALS)


@pytest.mark.parametrize("key", ["options", "manifest", "frobnicate", "conflicts"])
def test_refusal_names_the_key(key: str) -> None:
    case = {
        "options": "options-present",
        "manifest": "manifest-dependency-metadata",
        "frobnicate": "unknown-top-key",
        "conflicts": "conflicts",
    }[key]
    with pytest.raises(UnsupportedLock) as exc:
        expected(REFUSALS[case])
    assert key in str(exc.value)


# ---------- distribution_problems ----------

GIT = SourceId(
    "git",
    url="https://github.com/andrei-shtanakov/dispatcher",
    subdirectory="packages/plan-fields",
    commit=PLAN_FIELDS_COMMIT,
)
ROOT_SOURCE = SourceId("directory", path=str(ROOT_DIR), editable=False)


def installed(name: str, version: str, source: SourceId) -> InstalledDist:
    return InstalledDist(name, version, source)


EXPECTED = frozenset(
    {
        ExpectedDist("plan-fields", "0.1.0", GIT),
        ExpectedDist("app", "0.1.0", ROOT_SOURCE),
        registry("a"),
    }
)
INSTALLED = [
    installed("plan-fields", "0.1.0", GIT),
    installed("app", "0.1.0", ROOT_SOURCE),
    installed("a", "1.0", SourceId("registry")),
]


def test_equal_sets_have_no_problems() -> None:
    """The registry index is projected away: the installed side declares none."""
    assert distribution_problems(INSTALLED, EXPECTED) == []


def test_missing_and_extra_installed_both_reported() -> None:
    have = [*INSTALLED[:2], installed("b", "2.0", SourceId("registry"))]
    problems = distribution_problems(have, EXPECTED)
    assert len(problems) == 2
    assert any(p.startswith("нет: a 1.0") for p in problems)
    assert any(p.startswith("лишнее: b 2.0") for p in problems)
    assert problems == sorted(problems)


def test_version_mismatch_reported_both_ways() -> None:
    have = [*INSTALLED[:2], installed("a", "1.1", SourceId("registry"))]
    problems = distribution_problems(have, EXPECTED)
    assert len(problems) == 2


def test_git_commit_mismatch_reported_as_declared() -> None:
    other = dataclasses.replace(GIT, commit="0" * 40)
    have = [installed("plan-fields", "0.1.0", other), *INSTALLED[1:]]
    problems = distribution_problems(have, EXPECTED)
    assert len(problems) == 1
    assert problems[0].startswith("plan-fields 0.1.0: заявлено ")
    assert "0" * 40 in problems[0] and PLAN_FIELDS_COMMIT in problems[0]
    assert "проверено" not in problems[0]


@pytest.mark.parametrize(
    "change",
    ["url", "subdirectory", "commit", "editable-true", "registry-has-direct-url"],
)
def test_source_identity_mismatch_reported(change: str) -> None:
    """Same name/version, another identity of the source: a problem."""
    have = list(INSTALLED)
    if change == "url":
        have[0] = installed(
            "plan-fields",
            "0.1.0",
            dataclasses.replace(GIT, url="https://github.com/x/dispatcher"),
        )
    elif change == "subdirectory":
        have[0] = installed(
            "plan-fields",
            "0.1.0",
            dataclasses.replace(GIT, subdirectory="packages/other"),
        )
    elif change == "commit":
        have[0] = installed(
            "plan-fields", "0.1.0", dataclasses.replace(GIT, commit="1" * 40)
        )
    elif change == "editable-true":
        have[1] = installed(
            "app", "0.1.0", dataclasses.replace(ROOT_SOURCE, editable=True)
        )
    else:
        have[2] = installed(
            "a", "1.0", SourceId("directory", path="/x", editable=False)
        )
    problems = distribution_problems(have, EXPECTED)
    assert len(problems) == 1
    assert "заявлено" in problems[0]


def test_same_name_version_different_source_distinguished() -> None:
    """One name/version expected from git, installed from the registry."""
    have = [installed("plan-fields", "0.1.0", SourceId("registry")), *INSTALLED[1:]]
    assert len(distribution_problems(have, EXPECTED)) == 1


def test_duplicate_installed_name_reported() -> None:
    have = [*INSTALLED, installed("a", "1.0", SourceId("registry"))]
    problems = distribution_problems(have, EXPECTED)
    assert problems == ["a: установлено дважды"]


# ---------- uv_config_problems ----------

CLEAN_LOCK = lock(ROOT_OK, pkg("a"))
CLEAN_PYPROJECT = b'[project]\nname = "app"\nversion = "0.1.0"\n'


def pyproject(tool_uv: str) -> bytes:
    return CLEAN_PYPROJECT + b"\n[tool.uv]\n" + tool_uv.encode() + b"\n"


def test_clean_inputs_have_no_problems() -> None:
    assert uv_config_problems(CLEAN_PYPROJECT, None, CLEAN_LOCK) == []


@pytest.mark.parametrize(
    "tool_uv",
    [
        "package = false",
        'default-groups = ["dev"]',
        'constraint-dependencies = ["a<2"]',
        'override-dependencies = ["a<2"]',
        "environments = [\"sys_platform == 'darwin'\"]",
        '[tool.uv.sources]\na = { git = "https://example.org/a" }',
        '[[tool.uv.index]]\nname = "pypi"\nurl = "https://pypi.org/simple"',
    ],
)
def test_project_metadata_keys_allowed(tool_uv: str) -> None:
    assert uv_config_problems(pyproject(tool_uv), None, CLEAN_LOCK) == []


@pytest.mark.parametrize(
    "key,line",
    [
        ("compile-bytecode", "compile-bytecode = true"),
        ("exclude-newer", 'exclude-newer = "2026-01-01T00:00:00Z"'),
        ("no-binary", "no-binary = true"),
        ("cache-dir", 'cache-dir = "/tmp/c"'),
        ("conflicts", 'conflicts = [[{ extra = "a" }, { extra = "b" }]]'),
        ("workspace", 'workspace = { members = ["x"] }'),
        ("managed", "managed = false"),
        ("dependency-metadata", '[[tool.uv.dependency-metadata]]\nname = "a"'),
    ],
)
def test_setting_keys_refused(key: str, line: str) -> None:
    problems = uv_config_problems(pyproject(line), None, CLEAN_LOCK)
    assert len(problems) == 1
    assert problems[0].startswith(f"pyproject.toml [tool.uv] {key}: ")


def test_uv_toml_present_is_a_problem_whatever_it_holds() -> None:
    problems = uv_config_problems(CLEAN_PYPROJECT, b"", CLEAN_LOCK)
    assert len(problems) == 1 and problems[0].startswith("uv.toml")


def test_lock_only_options_are_a_problem_per_key() -> None:
    """A CLI flag at lock time leaves no pyproject key, only `[options]`."""
    data = lock(
        ROOT_OK,
        pkg("a"),
        header=HEADER
        + '[options]\nexclude-newer = "2026-01-01"\nprerelease-mode = "allow"\n',
    )
    problems = uv_config_problems(CLEAN_PYPROJECT, None, data)
    assert [p.split(":")[0] for p in problems] == [
        "uv.lock [options] exclude-newer",
        "uv.lock [options] prerelease-mode",
    ]


def test_manifest_dependency_metadata_is_a_problem() -> None:
    problems = uv_config_problems(
        CLEAN_PYPROJECT, None, REFUSALS["manifest-dependency-metadata"]
    )
    assert [p.split(":")[0] for p in problems] == [
        "uv.lock [manifest] dependency-metadata"
    ]


def test_constraint_dependencies_in_both_files_ok() -> None:
    data = lock(
        ROOT_OK,
        pkg("a"),
        header=HEADER
        + '[manifest]\nconstraints = [{ name = "a", specifier = "<2" }]\n',
    )
    tool = pyproject('constraint-dependencies = ["a<2"]')
    assert uv_config_problems(tool, None, data) == []


@pytest.mark.parametrize("case", ["unknown-top-key", "conflicts", "manifest-members"])
def test_lock_keys_shared_with_expected_distributions(case: str) -> None:
    """One key set: what `expected_distributions` refuses for the lock layout
    is also a problem of `uv_config_problems`."""
    assert uv_config_problems(CLEAN_PYPROJECT, None, REFUSALS[case]) != []


@pytest.mark.parametrize(
    "pyproject_bytes,lock_bytes,file",
    [
        (b"[tool.uv\n", CLEAN_LOCK, "pyproject.toml"),
        (b"\xff", CLEAN_LOCK, "pyproject.toml"),
        (b"[tool]\nuv = 1\n", CLEAN_LOCK, "pyproject.toml"),
        (CLEAN_PYPROJECT, b"version = \n", "uv.lock"),
    ],
)
def test_unreadable_input_is_a_problem(
    pyproject_bytes: bytes, lock_bytes: bytes, file: str
) -> None:
    problems = uv_config_problems(pyproject_bytes, None, lock_bytes)
    assert len(problems) == 1 and problems[0].startswith(file)


def test_devtools_own_pyproject_and_lock_pass() -> None:
    problems = uv_config_problems(
        (REPO_ROOT / "pyproject.toml").read_bytes(),
        None,
        (REPO_ROOT / "uv.lock").read_bytes(),
    )
    assert problems == []
