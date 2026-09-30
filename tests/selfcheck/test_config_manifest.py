"""Task 2 — roles (§1.4), selfcheck.toml and allowlist (§2.4), manifest (§1.2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from selfcheck.config import ConfigError, apply_allowlist, load_config
from selfcheck.manifest import load_manifest
from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import Role, glob_match, role_of


@pytest.mark.parametrize(
    ("path", "role"),
    [
        (".selfcheck-canary/ruff/canary.py", Role.CANARY),
        ("selfcheck_canary/__init__.py", Role.CANARY),
        ("reports/2026-07-10-x.md", Role.DIAGNOSTIC_OUTPUT),
        ("skills/fleet-check/SKILL.md", Role.SKILL_ROOT),
        (".claude/skills/kb/SKILL.md", Role.SKILL_ROOT),
        ("authored/skills/kb-search/SKILL.md", Role.SKILL_ROOT),
        (".claude/commands/do.md", Role.SKILL_ROOT),
        ("tests/test_x.py", Role.TEST),
        ("governance/test_helper.py", Role.TEST),
        ("README.md", Role.DOCUMENTATION),
        ("docs/runbook.txt", Role.DOCUMENTATION),
        ("issue_worker.py", Role.SOURCE),
        ("skills/fleet-check/extra/SKILL.md", Role.DOCUMENTATION),
        ("test/contracts/vendored_test.exs", Role.TEST),
        ("apps/a/test/a_test.exs", Role.TEST),
        ("test/support/fixtures/x.json", Role.TEST),
        ("lib/kapelle/test_helper.ex", Role.SOURCE),
    ],
)
def test_default_roles(path: str, role: Role) -> None:
    assert role_of(path) is role


def test_glob_semantics() -> None:
    assert glob_match("skills/*/SKILL.md", "skills/a/SKILL.md")
    assert not glob_match("skills/*/SKILL.md", "skills/a/b/SKILL.md")
    assert glob_match("**/*.md", "a/b/c.md") and glob_match("**/*.md", "c.md")


def test_configured_roles_win() -> None:
    assert (
        role_of("notes/x.md", {"diagnostic-output": ["notes/**"]})
        is Role.DIAGNOSTIC_OUTPUT
    )


def cfg_file(tmp: Path, text: str) -> Path:
    path = tmp / "selfcheck.toml"
    path.write_text(text)
    return path


def test_missing_config_is_empty(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.allow == () and cfg.corpus_exclude == () and cfg.roles == {}


@pytest.mark.parametrize(
    "body",
    [
        '[[allow]]\nanchor = "file:x.py"\nuntil = 2027-01-01\n',
        '[[allow]]\nanchor = "file:x.py"\nreason = "r"\n',
        '[[allow]]\nreason = "r"\nuntil = 2027-01-01\n',
        '[[allow]]\nanchor = "file:x.py"\nreason = "r"\nuntil = "2027-01-01"\n',
        '[roles]\nbogus = ["x"]\n',
        "not toml ===",
    ],
)
def test_bad_config_raises(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(cfg_file(tmp_path, body))


def finding(anchor: str) -> Finding:
    return Finding(
        rule="ruff/X",
        category="bug",
        severity="low",
        confidence=Confidence.LIKELY,
        owner_repo="devtools",
        anchor=anchor,
        locations=[Location("x.py", 1)],
    )


def test_file_anchor_covers_its_file(tmp_path: Path) -> None:
    cfg = load_config(
        cfg_file(
            tmp_path,
            '[[allow]]\nanchor = "file:issue_console.py"\n'
            'reason = "owner"\nuntil = 2027-01-01\n',
        )
    )
    items = [
        finding("func:issue_console.py::main"),
        finding("file:issue_console.py"),
        finding("llm:issue_console.py::run"),
        finding("llm:issue_console.py"),  # a shell site: no `::` (#411)
        finding("func:other.py::f"),
        finding("func:issue_console.pyx::f"),
        finding("llm:issue_console.pyx"),
    ]
    res = apply_allowlist(items, cfg, date(2026, 9, 26))
    assert [f.anchor for f in res.kept] == [
        "func:other.py::f",
        "func:issue_console.pyx::f",
        "llm:issue_console.pyx",
    ]
    assert len(res.suppressed) == 4 and res.expired == []


def test_expired_entry_becomes_finding(tmp_path: Path) -> None:
    cfg = load_config(
        cfg_file(
            tmp_path,
            '[[allow]]\nanchor = "file:a.py"\nreason = "r"\nuntil = 2026-01-01\n',
        )
    )
    res = apply_allowlist([finding("file:a.py")], cfg, date(2026, 9, 26))
    assert len(res.kept) == 1
    assert [f.rule for f in res.expired] == ["selfcheck/allow-expired"]


def test_manifest_dedup_missing_languages(tmp_path: Path) -> None:
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "a" / "pyproject.toml").write_text("")
    (tmp_path / "a" / "Cargo.toml").write_text("")
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[cores.a]\ngit_dir = "a"\n[cores.a-sdk]\ngit_dir = "a"\n'
        'member = true\n[apps.b]\ngit_dir = "b"\n[tools.c]\ngit_dir = "a"\n'
    )
    info = load_manifest(manifest, tmp_path)
    assert info.entries_read == 4
    assert [r.name for r in info.repos] == ["a"]
    assert info.repos[0].path.is_absolute()
    assert info.repos[0].languages == frozenset({"python", "rust"})
    assert info.missing == ("b",)


def test_manifest_entry_without_git_dir(tmp_path: Path) -> None:
    manifest = tmp_path / "m.toml"
    manifest.write_text("[apps.x]\nrepo_url = 'u'\n")
    with pytest.raises(ConfigError):
        load_manifest(manifest, tmp_path)


def test_allow_repo_limits_the_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = "a"\nreason = "r"\n'
        "until = 2099-01-01\n"
    )
    entry = load_config(cfg).allow[0]
    mine = Finding("r/x", "quality", "low", Confidence.LIKELY, "a", "file:x.py", [])
    assert entry.matches(mine)
    assert not entry.matches(replace(mine, owner_repo="b"))


def test_allow_empty_repo_is_rejected(tmp_path: Path) -> None:
    """#437.5: `repo = ""` limited the entry to no repo at all — silently
    inert. A config error (exit 4), not a no-op."""
    cfg = tmp_path / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = ""\nreason = "r"\nuntil = 2099-01-01\n'
    )
    with pytest.raises(ConfigError, match="repo"):
        load_config(cfg)


def _at(rule: str, *paths: str) -> Finding:
    return Finding(
        rule=rule,
        category="quality",
        severity="low",
        confidence=Confidence.LIKELY,
        owner_repo="devtools",
        anchor=f"file:{paths[0]}",
        locations=[Location(p, 1) for p in paths],
    )


def test_class_entry_matches_rule_glob_and_every_location(tmp_path: Path) -> None:
    """A class entry (`rule` + `path` globs) silences a whole noise class —
    e.g. ARG in tests — but a finding with any location outside `path`
    (a clone spanning tests and code) stays: it is not wholly in the class."""
    cfg = load_config(
        cfg_file(
            tmp_path,
            '[[allow]]\nrule = "ruff/ARG*"\npath = "tests/**"\n'
            'reason = "pytest fixtures"\nuntil = 2027-01-01\n',
        )
    )
    items = [
        _at("ruff/ARG001", "tests/test_a.py"),
        _at("ruff/ARG002", "tests/sub/test_b.py"),
        _at("ruff/ARG001", "governance/a.py"),
        _at("ruff/B905", "tests/test_a.py"),
        _at("ruff/ARG001", "tests/test_a.py", "governance/a.py"),
    ]
    res = apply_allowlist(items, cfg, date(2026, 9, 30))
    assert [(f.rule, len(f.locations)) for f in res.suppressed] == [
        ("ruff/ARG001", 1),
        ("ruff/ARG002", 1),
    ]
    assert len(res.kept) == 3


def test_class_entry_without_path_covers_every_file(tmp_path: Path) -> None:
    cfg = load_config(
        cfg_file(
            tmp_path,
            '[[allow]]\nrule = "radon/*"\nreason = "complexity is a metric"\n'
            "until = 2027-01-01\n",
        )
    )
    res = apply_allowlist(
        [_at("radon/cc-D", "a.py"), _at("ruff/C901", "a.py")], cfg, date(2026, 9, 30)
    )
    assert [f.rule for f in res.suppressed] == ["radon/cc-D"]


@pytest.mark.parametrize(
    "body",
    [
        # path alone would be a file anchor under another name — say which rules
        '[[allow]]\npath = "tests/**"\nreason = "r"\nuntil = 2027-01-01\n',
        # one form per entry: mixing makes the match ambiguous
        '[[allow]]\nid = "sc-1"\nrule = "ruff/*"\nreason = "r"\nuntil = 2027-01-01\n',
        (
            '[[allow]]\nanchor = "file:x.py"\nrule = "ruff/*"\nreason = "r"\n'
            "until = 2027-01-01\n"
        ),
    ],
)
def test_class_entry_shape_errors(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(cfg_file(tmp_path, body))


def test_expired_class_entry_names_its_class(tmp_path: Path) -> None:
    cfg = load_config(
        cfg_file(
            tmp_path,
            '[[allow]]\nrule = "ruff/ARG*"\npath = "tests/**"\nreason = "r"\n'
            "until = 2026-01-01\n",
        )
    )
    res = apply_allowlist([], cfg, date(2026, 9, 30))
    assert [f.anchor for f in res.expired] == ["allow:rule=ruff/ARG*@tests/**"]
