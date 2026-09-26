"""S2 Task 5 — fleet composition, completeness, fleet-partial, canary repo (§9.2, §9.5, §9.6)."""

from __future__ import annotations

import shutil
from pathlib import Path

from selfcheck.fleet import reader
from selfcheck.fleet.assemble import (
    CANARY_REPO,
    FleetView,
    build_canary,
    fleet_findings,
    fleet_names,
    load_fleet,
    manifest_repo,
    surface_repos,
)
from selfcheck.fleet.reader import FleetRepo, RepoState
from selfcheck.manifest import load_manifest
from tests.selfcheck.helpers import commit, fleet_ws, git, make_repo, synced


def _view(ws: Path, run_dir: Path, scope: str = "devtools") -> FleetView:
    info = load_manifest(ws / "umbrella" / "m.toml", ws)
    mrepo = manifest_repo(ws / "umbrella" / "m.toml")
    names = fleet_names(info, mrepo, [scope])
    paths = {mrepo.name: mrepo.path} if mrepo else {}
    return load_fleet(ws, names, run_dir, scope_name=scope, paths=paths)


def test_composition_includes_manifest_repo(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    info = load_manifest(ws / "umbrella" / "m.toml", ws)
    repo = manifest_repo(ws / "umbrella" / "m.toml")
    assert repo is not None and repo.name == "umbrella"
    assert fleet_names(info, repo, ["devtools"]) == ("nb", "docs-nb", "umbrella")
    assert fleet_names(info, None, ["devtools", "nb"]) == ("docs-nb",)


def test_root_umbrella_is_never_a_fleet_repo(tmp_path: Path) -> None:
    root = synced(
        make_repo(tmp_path / "root", {"m.toml": "", "_cowork_output/x.md": "x\n"})
    )
    assert manifest_repo(root / "m.toml") is None  # it tracks _cowork_output/


def test_complete_fleet(tmp_path: Path) -> None:
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    assert view.canary_misses == [] and view.status() == "complete"
    assert view.canary is not None and view.canary.name == CANARY_REPO
    assert (tmp_path / "run" / "fleet-canary" / "devtools" / ".git").exists()
    assert fleet_findings(view, "devtools") == []
    rows = surface_repos(view)
    assert [r["name"] for r in rows] == ["nb", "docs-nb", "umbrella"]
    assert set(rows[0]) == {
        "name", "head", "branch", "default", "behind", "ahead",
        "files", "binary", "dirty", "fetched_at",
    }  # fmt: skip
    assert all(r["files"] > 0 for r in rows)
    assert CANARY_REPO not in [r.name for r in view.repos]  # isolation


def test_canary_per_scope_repo(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    _view(ws, tmp_path / "run", "devtools")
    _view(ws, tmp_path / "run", "nb")  # a second R must not collide (m5)
    assert (tmp_path / "run" / "fleet-canary" / "nb" / ".git").exists()


def test_canary_commit_ignores_the_callers_git_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "intruder")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "intruder")
    root = build_canary(tmp_path / "c", "devtools")
    monkeypatch.delenv("GIT_AUTHOR_NAME")
    monkeypatch.delenv("GIT_COMMITTER_NAME")
    assert git(root, "log", "--format=%an|%cn").strip() == "selfcheck|selfcheck"


def test_broken_stale_check_is_a_canary_miss(tmp_path: Path, monkeypatch) -> None:
    clean = RepoState("0" * 40, "main", "main", 0, 0, False, None, ())
    monkeypatch.setattr(reader, "stale_reasons", lambda path: clean)
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    assert view.canary_misses == ["stale"] and view.status() == "partial"


def test_partial_causes_are_findings(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    shutil.rmtree(ws / "docs-nb")
    commit(ws / "nb", {"late.md": "x\n"}, date="2026-09-25T00:00:00")  # ahead
    view = _view(ws, tmp_path / "run")
    assert view.status() == "partial"
    found = fleet_findings(view, "devtools")
    assert sorted(f.text_key for f in found) == ["docs-nb:missing", "nb:stale"]
    for f in found:
        assert (f.rule, f.owner_repo, f.anchor, f.severity, f.category) == (
            "selfcheck/fleet-partial",
            "devtools",
            "probe:devtools#fleet",
            "medium",
            "selfcheck",
        )


def test_one_finding_per_repo_and_cause(tmp_path: Path) -> None:
    repo = FleetRepo(
        "nb", tmp_path, {"a.md": "x"}, 0, [("unreadable", "a"), ("unreadable", "b")]
    )
    view = FleetView([repo], ("nb",), [], None)
    (only,) = fleet_findings(view, "devtools")
    assert only.text_key == "nb:unreadable"
    assert [loc.path for loc in only.locations] == ["nb:a", "nb:b"]


def test_count_mismatch_is_partial_with_evidence(tmp_path: Path) -> None:
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    view.repos = [r for r in view.repos if r.name != "nb"]  # composition bug
    assert view.status() == "partial"
    (count,) = fleet_findings(view, "devtools")
    assert count.text_key == "fleet:count"
    details = {e["kind"]: e["detail"] for e in count.evidence}
    assert details == {
        "expected": "docs-nb, nb, umbrella",
        "actual": "docs-nb, umbrella",
    }


def test_canary_commit_survives_gpgsign_and_hooks(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /bin/false\n"
        f"[core]\n\thooksPath = {hooks}\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = build_canary(tmp_path / "c", "devtools")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert git(root, "log", "--format=%an").strip() == "selfcheck"
