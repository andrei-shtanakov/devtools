"""Task 2 — roles (§1.4), selfcheck.toml and allowlist (§2.4), manifest (§1.2)."""

from __future__ import annotations

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
        finding("func:other.py::f"),
        finding("func:issue_console.pyx::f"),
    ]
    res = apply_allowlist(items, cfg, date(2026, 9, 26))
    assert [f.anchor for f in res.kept] == [
        "func:other.py::f",
        "func:issue_console.pyx::f",
    ]
    assert len(res.suppressed) == 3 and res.expired == []


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
