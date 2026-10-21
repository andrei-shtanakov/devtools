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
        assert f"member {name!r}:" in msgs


def _replace(old: str, new: str) -> str:
    assert old in _fix()
    return _fix().replace(old, new, 1)


S1 = 'sha = "1111111111111111111111111111111111111111"'
CASES: dict[str, tuple[str, str, str]] = {
    "tag-only": (S1, "", "member 'devtools': sha must"),
    "short-sha": (S1, 'sha = "111111111111"', "member 'devtools': sha must"),
    "branch-in-sha": (S1, 'sha = "main"', "member 'devtools': sha must"),
    "uppercase-sha": (S1, 'sha = "' + "A" * 40 + '"', "member 'devtools': sha must"),
    "unknown-member": (
        '"steward", "spec-runner"]',
        '"nope", "spec-runner"]',
        "member 'nope': not in",
    ),
    "dup-key": (
        "[tools.devtools]",
        '[apps.devtools]\nrepo_url = "git@github.com:a/d.git"\n'
        'sha = "' + "9" * 40 + '"\n[tools.devtools]',
        "member 'devtools': key found in more than one section",
    ),
    "dup-member": (
        '"steward", "spec-runner"]',
        '"steward", "steward"]',
        "duplicate member 'steward'",
    ),
    "dup-member-base-child": (
        'members = ["impresario"]',
        'members = ["steward"]',
        "duplicate member 'steward'",
    ),
    "groups-for-non-member": (
        'devtools = ["governance"]',
        'impresario = ["x"]',
        "groups for 'impresario', not a member",
    ),
    "child-python-differs": (
        'extends = "core"',
        'extends = "core"\npython = "3.13.0"',
        "differs from base",
    ),
    "gitlab-host": (
        "git@github.com:andrei-shtanakov/steward.git",
        "git@gitlab.com:a/s.git",
        "member 'steward': repo_url",
    ),
    "python-minor-only": (
        'python = "3.12.13"',
        'python = "3.12"',
        "must be exact X.Y.Z",
    ),
    "unknown-profile-key": (
        'python = "3.12.13"',
        'python = "3.12.13"\nbogus = 1',
        "unknown key 'bogus'",
    ),
    "python-arabic-digits": (
        'python = "3.12.13"',
        'python = "٣.١٢.١٣"',
        "must be exact X.Y.Z",
    ),
    "python-leading-zero": (
        'python = "3.12.13"',
        'python = "03.12.13"',
        "must be exact X.Y.Z",
    ),
    "case-collision": (
        '"steward", "spec-runner"]',
        '"steward", "Steward"]',
        "differ only in case",
    ),
    "member-true": (
        "[apps.steward]\n",
        "[apps.steward]\nmember = true\n",
        "member 'steward': workspace-member",
    ),
    "pyproject-elsewhere": (
        "[apps.steward]\n",
        '[apps.steward]\npyproject_path = "steward/packages/x/pyproject.toml"\n',
        "member 'steward': pyproject_path",
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
    assert "no [df.profiles]" in _problems(CURRENT.read_text())


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


BAD_IDENTS = ["../../etc", "a/b", "-rf", "a\nb", "a\x00b", "..", ".", "a..b", ""]


@pytest.mark.parametrize("bad", BAD_IDENTS)
def test_member_names_refused(bad: str) -> None:
    toml = _fix() + (
        '\n[df.profiles.evil]\npython = "3.12.13"\nmembers = [' + _toml_str(bad) + "]\n"
    )
    assert "member" in _problems(toml, "evil")


def _toml_str(value: str) -> str:
    esc = value.replace("\\", "\\\\").replace('"', '\\"')
    esc = esc.replace("\n", "\\n").replace("\x00", "\\u0000")
    return f'"{esc}"'


@pytest.mark.parametrize("bad", ["--index-url=http://x", "a b", "x\ny", "../g"])
def test_group_names_refused(bad: str) -> None:
    toml = _replace('devtools = ["governance"]', f"devtools = [{_toml_str(bad)}]")
    assert "group" in _problems(toml)


def test_group_key_unsafe_and_unknown_reported_once() -> None:
    msgs = _problems(_replace('devtools = ["governance"]', 'ghost = ["x"]'))
    assert msgs.count("ghost") == 1


@pytest.mark.parametrize("bad", ["../core", "-x", "a/b"])
def test_extends_target_and_requested_profile_refused(bad: str) -> None:
    toml = _replace('extends = "core"', f"extends = {_toml_str(bad)}")
    assert "extends" in _problems(toml, "impresario")
    with pytest.raises(ManifestError):
        parse_profile(FIX.read_bytes(), bad)


def test_base_validated_on_its_own() -> None:
    toml = _replace('devtools = ["governance"]', 'impresario = ["g"]')
    assert "impresario" in _problems(toml, "impresario")
    toml = _replace('python = "3.12.13"', 'python = "3.12.13"\nbogus = 1')
    assert "bogus" in _problems(toml, "impresario")


def test_base_without_python_refused() -> None:
    toml = _replace('python = "3.12.13"\n', "")
    assert "python" in _problems(toml, "impresario")
    assert "python" in _problems(toml, "core")


def test_profile_needs_members_or_extends() -> None:
    toml = _fix() + '\n[df.profiles.empty]\npython = "3.12.13"\n'
    assert "members" in _problems(toml, "empty")


HOSTILE_URLS = [
    "https://github.com.evil.com/o/r.git",
    "https://github.com/o/r/x.git",
    "ssh://git@github.com/o/r.git",
    "HTTPS://github.com/o/r.git",
    "https://github.com/o/r/",
    "https://github.com/-o/r.git",
    "https://github.com/o/r.git\n",
    "git@github.com:o/r/x.git",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_repo_urls_refused(url: str) -> None:
    toml = _replace('"git@github.com:andrei-shtanakov/steward.git"', _toml_str(url))
    assert "steward" in _problems(toml)


class _Resp:
    def __init__(self, final: str, body: bytes = b"x") -> None:
        self.final, self.body, self.reads = final, body, []

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final

    def read(self, n: int) -> bytes:
        self.reads.append(n)
        return self.body[:n]


def test_https_get_reads_at_most_cap_plus_one(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _Resp(manifest_url("o/r", "a" * 40), b"x" * (2**20 + 50))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: resp)
    from df.manifest import _https_get

    assert len(_https_get(manifest_url("o/r", "a" * 40))) == 2**20 + 1
    assert resp.reads == [2**20 + 1]


def test_https_get_refuses_foreign_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    from df.manifest import _https_get

    resp = _Resp("http://evil.example/m.toml")
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: resp)
    with pytest.raises(ManifestError):
        _https_get(manifest_url("o/r", "a" * 40))


def test_https_get_http_exception_becomes_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client

    from df.manifest import _https_get

    def boom(*_a: object, **_k: object) -> None:
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(OSError):
        _https_get(manifest_url("o/r", "a" * 40))


def test_case_collision_needs_both_entries() -> None:
    extra = '\n[apps.Steward]\nrepo_url = "git@github.com:a/s.git"\n'
    extra += 'sha = "' + "8" * 40 + '"\n'
    toml = _replace('"steward", "spec-runner"]', '"steward", "Steward"]') + extra
    assert "differ only in case" in _problems(toml)


def test_bad_extends_has_no_python_required_noise() -> None:
    toml = _fix() + '\n[df.profiles.x]\nextends = "ghost"\nmembers = []\n'
    msgs = _problems(toml, "x")
    assert "not a valid base" in msgs and "python is required" not in msgs
