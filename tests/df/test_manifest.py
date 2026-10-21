"""Tests for manifest profile parsing and strict SHA pins."""

from __future__ import annotations

from pathlib import Path

import pytest

from df.manifest import (
    ManifestError,
    Profile,
    RepoPin,
    fetch_manifest,
    manifest_url,
    parse_profile,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIX = FIXTURES / "manifest-profiles.toml"
CURRENT = FIXTURES / "manifest-current.toml"
CORE = ["devtools", "discovery", "discovery-toolkit", "disputatio", "steward"]
CORE_NAMES = [*CORE, "spec-runner"]
CORE_SECTION = (
    '\n[df.profiles.core]\npython = "3.12.13"\nmembers = '
    + str(CORE_NAMES).replace("'", '"')
    + "\n"
)


def _fix() -> str:
    return FIX.read_text()


def _problems(text: str, profile: str = "core") -> str:
    with pytest.raises(ManifestError) as exc:
        parse_profile(text.encode(), profile)
    return "\n".join(exc.value.problems)


def test_core_profile_resolves() -> None:
    p = parse_profile(FIX.read_bytes(), "core")
    assert [r.name for r in p.repos] == CORE_NAMES
    assert p.repos[0].url == "https://github.com/andrei-shtanakov/devtools.git"
    assert p.repos[0].sha == "1" * 40
    assert p.repos[0].groups == ("governance",) and p.python == "3.12.13"
    assert p.repos[1].url.endswith("/discovery.git")
    assert p.repos[1].groups == ()


def test_impresario_extends_core() -> None:
    p = parse_profile(FIX.read_bytes(), "impresario")
    assert [r.name for r in p.repos] == [*CORE_NAMES, "impresario"]
    assert p.python == "3.12.13"
    assert p.repos[0].groups == ("governance",)
    assert p.repos[-1].groups == ()


def test_non_member_entry_without_sha_ignored() -> None:
    names = [r.name for r in parse_profile(FIX.read_bytes(), "core").repos]
    assert "atp-platform" not in names and "other-lab" not in names


def test_dataclasses_are_plain_and_frozen() -> None:
    pin = RepoPin("x", "file:///tmp/x", "zz", ())
    assert Profile("p", "3.12.13", (pin,)).repos == (pin,)
    with pytest.raises(AttributeError):
        pin.sha = "y"  # type: ignore[misc]


def test_current_manifest_names_every_bad_entry() -> None:
    msgs = _problems(CURRENT.read_text() + CORE_SECTION)
    for name in CORE_NAMES:
        assert name in msgs


def _replace(old: str, new: str) -> str:
    assert old in _fix()
    return _fix().replace(old, new, 1)


S1 = 'sha = "1111111111111111111111111111111111111111"'
CASES: dict[str, tuple[str, str, str]] = {
    "tag-only": (S1, "", "devtools"),
    "short-sha": (S1, 'sha = "111111111111"', "devtools"),
    "branch-in-sha": (S1, 'sha = "main"', "devtools"),
    "uppercase-sha": (S1, 'sha = "' + "A" * 40 + '"', "devtools"),
    "unknown-member": ('"steward", "spec-runner"]', '"nope", "spec-runner"]', "nope"),
    "dup-key": (
        "[tools.devtools]",
        '[apps.devtools]\nrepo_url = "git@github.com:a/d.git"\n'
        'sha = "' + "9" * 40 + '"\n[tools.devtools]',
        "devtools",
    ),
    "dup-member": ('"steward", "spec-runner"]', '"steward", "steward"]', "steward"),
    "dup-member-base-child": (
        'members = ["impresario"]',
        'members = ["steward"]',
        "steward",
    ),
    "groups-for-non-member": (
        'devtools = ["governance"]',
        'impresario = ["x"]',
        "impresario",
    ),
    "child-python-differs": (
        'extends = "core"',
        'extends = "core"\npython = "3.13.0"',
        "python",
    ),
    "gitlab-host": (
        "git@github.com:andrei-shtanakov/steward.git",
        "git@gitlab.com:a/s.git",
        "steward",
    ),
    "python-minor-only": ('python = "3.12.13"', 'python = "3.12"', "python"),
    "unknown-profile-key": (
        'python = "3.12.13"',
        'python = "3.12.13"\nbogus = 1',
        "bogus",
    ),
}


@pytest.mark.parametrize("case", CASES)
def test_profile_refusals(case: str) -> None:
    old, new, needle = CASES[case]
    profile = "impresario" if case.startswith("dup-member-base") else "core"
    if case == "child-python-differs":
        profile = "impresario"
    assert needle in _problems(_replace(old, new), profile)


def test_no_profile_section() -> None:
    text = "\n".join(ln for ln in _fix().splitlines() if "df.profiles" not in ln)
    with pytest.raises(ManifestError):
        parse_profile(text.encode(), "core")


def test_unknown_profile_and_invalid_toml() -> None:
    with pytest.raises(ManifestError):
        parse_profile(FIX.read_bytes(), "ghost")
    with pytest.raises(ManifestError):
        parse_profile(b"not = [toml", "core")


def test_extends_cycle_and_depth() -> None:
    cyc = _fix() + '\n[df.profiles.a]\nextends = "b"\nmembers = []\n'
    cyc += '[df.profiles.b]\nextends = "a"\nmembers = []\n'
    assert "extends" in _problems(cyc, "a")
    two = _fix() + '\n[df.profiles.deep]\nextends = "impresario"\nmembers = []\n'
    assert "extends" in _problems(two, "deep")
    selfy = _fix() + '\n[df.profiles.s]\nextends = "s"\nmembers = []\n'
    assert "extends" in _problems(selfy, "s")


def test_problems_accumulate() -> None:
    text = _replace(S1, 'sha = "main"').replace("[apps.steward]", "[apps.steward]\n#")
    text = text.replace('sha = "5555555555555555555555555555555555555555"', "")
    msgs = _problems(text)
    assert "devtools" in msgs and "steward" in msgs


def test_manifest_url() -> None:
    assert manifest_url("o/r", "a" * 40) == (
        "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/workspace-manifest.toml"
    )


def test_fetch_uses_raw_sha_url_and_caps_size() -> None:
    seen: list[str] = []

    def opener(url: str) -> bytes:
        seen.append(url)
        return b"x = 1"

    assert fetch_manifest("o/r", "a" * 40, opener=opener) == b"x = 1"
    assert seen == [manifest_url("o/r", "a" * 40)]
    with pytest.raises(ManifestError):
        fetch_manifest("o/r", "a" * 40, opener=lambda _u: b"x" * (2**20 + 1))
    assert len(fetch_manifest("o/r", "a" * 40, opener=lambda _u: b"x" * 2**20)) == 2**20


@pytest.mark.parametrize(
    ("repo", "sha"),
    [
        ("o", "a" * 40),
        ("o/r/x", "a" * 40),
        ("o/r", "abc"),
        ("o/r", "A" * 40),
        ("../r", "a" * 40),
    ],
)
def test_fetch_refuses_malformed_args(repo: str, sha: str) -> None:
    with pytest.raises(ManifestError):
        fetch_manifest(repo, sha, opener=lambda _u: b"")


def test_fetch_network_error_propagates() -> None:
    def boom(_url: str) -> bytes:
        raise OSError("down")

    with pytest.raises(OSError):
        fetch_manifest("o/r", "a" * 40, opener=boom)
