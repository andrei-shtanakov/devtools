from pathlib import Path

import pytest

from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
from conductor.manifest import UMBRELLA, fleet_repos, github_owner, manifest_index

MANIFEST = (
    '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    '[cores.a-sdk]\nmember = true\nrepo_url = "git@github.com:own/a.git"\n'
    'git_dir = "a"\n'
    '[tools.ecosystem-kb]\nrepo_url = "git@github.com:own/prograph-vault.git"\n'
    'git_dir = "prograph-vault"\n'
)


def _inputs() -> Inputs:
    return Inputs(
        captured_at="2026-09-29T12:00:00Z",
        host="mac",
        owner="own",
        manifest_text=MANIFEST,
        todos=[RepoTodo("a", "- [ ] x @owner:TBD @id:x\n", "abc", "read")],
        gh_records=[{"repo": "a", "number": 1, "is_pr": False}],
        gh_state="read",
        gh_detail="",
        roadmap_text="schema_version = 1\n",
        roadmap_state="read",
        roadmap_source="origin",
        roadmap_sha="def",
        epics={"eco.tooling": {"status": "active"}},
        epics_state="read",
        epics_detail="",
        repo_names={"prograph-vault": "ecosystem-kb", "a": "a"},
        movement={"todo://a/x": "2026-09-28T00:00:00Z"},
        wait_since={},
        history={},
        trigger_facts={},
    )


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "inputs.json"
    save_inputs(_inputs(), path)
    assert load_inputs(path) == _inputs()


def test_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "inputs.json"
    path.write_text('{"version": 99}', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_inputs(path)


def test_manifest_from_text() -> None:
    repos = {r.key: r for r in fleet_repos(MANIFEST)}
    assert set(repos) == {"a", "ecosystem-kb", UMBRELLA}
    assert repos["ecosystem-kb"].github_name == "prograph-vault"
    assert github_owner(MANIFEST) == "own"
    assert "ecosystem-kb" in manifest_index(MANIFEST).canonical_keys
