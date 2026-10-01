"""Чтения среза 1 в collect: зонтик профиля (§9.1; ревью P2-4) и id событий
закрытия (§7.1; решение владельца 2)."""

from pathlib import Path

from conductor.collect import _slice1, collect, read_manifest
from conductor.manifest import UMBRELLA, fleet_repos
from tests.conductor.fixtures import ROADMAP
from tests.conductor.test_collect import _repo

SANDBOX = "conductor-sandbox"
MANIFEST = (
    f'[cores.{SANDBOX}]\nrepo_url = "https://github.com/own/{SANDBOX}.git"\n'
    f'git_dir = "{SANDBOX}"\n'
    f"[cores.{SANDBOX}-outside]\n"
    f'repo_url = "https://github.com/own/{SANDBOX}-outside.git"\n'
    f'git_dir = "{SANDBOX}-outside"\n'
)


def test_fleet_repos_add_profile_umbrella_only() -> None:
    keys = {r.key for r in fleet_repos(MANIFEST, SANDBOX)}
    assert keys == {SANDBOX, f"{SANDBOX}-outside"}
    assert UMBRELLA in {r.key for r in fleet_repos(MANIFEST)}


def test_acceptance_collect_reads_only_sandbox(tmp_path: Path) -> None:
    _repo(
        tmp_path / SANDBOX,
        {
            "TODO.md": "- [ ] x @owner:TBD @id:x\n",
            "roadmap.toml": ROADMAP,
            "workspace-manifest.toml": MANIFEST,
        },
    )
    _repo(tmp_path / f"{SANDBOX}-outside", {"TODO.md": ""})
    # настоящий зонтик рядом есть, и его чтение было бы заметно
    _repo(tmp_path / UMBRELLA, {"TODO.md": "- [ ] real @owner:TBD @id:real\n"})
    calls: list[str] = []

    def runner(args: list[str]) -> tuple[int, str, str]:
        calls.append(" ".join(args))
        return 1, "", "offline"

    text, origin, errors = read_manifest(tmp_path, False, SANDBOX)
    assert text == MANIFEST
    inp = collect(
        tmp_path,
        text,
        origin,
        None,
        False,
        runner,
        "h",
        "2026-10-01T12:00:00Z",
        errors,
        slice1=True,
        umbrella_dir=SANDBOX,
    )
    assert {t.repo for t in inp.todos} == {SANDBOX, f"{SANDBOX}-outside"}
    assert inp.roadmap_text == ROADMAP and inp.roadmap_source == "origin"
    assert inp.umbrella_full == f"own/{SANDBOX}"
    assert calls and not any(UMBRELLA in c for c in calls)
    assert not any("real" in (t.text or "") for t in inp.todos)


def test_closed_issue_records_get_closed_event_ids(tmp_path: Path) -> None:
    repos = {r.key: r for r in fleet_repos(MANIFEST, SANDBOX)}
    timeline = '[[{"event": "closed", "node_id": "CE_9"}]]'

    def runner(args: list[str]) -> tuple[int, str, str]:
        if any("issues/7/timeline" in a for a in args):
            return 0, timeline, ""
        if any("issues/8/timeline" in a for a in args):
            return 1, "", "boom"
        return 1, "", "offline"

    records = [
        {"repo": SANDBOX, "number": 7, "is_pr": False, "state": "closed"},
        {"repo": SANDBOX, "number": 8, "is_pr": False, "state": "closed"},
        {"repo": SANDBOX, "number": 9, "is_pr": True, "state": "closed"},
        {"repo": SANDBOX, "number": 10, "is_pr": False, "state": "open"},
    ]
    errors: list[str] = []
    snapshot = {"nodes": [], "references": []}
    out = _slice1(
        tmp_path, repos, snapshot, set(), "own", runner, errors, SANDBOX, records
    )
    assert out["closed_events"] == {f"{SANDBOX}#7": "CE_9"}
    assert any("#8: timeline закрытия не прочитан" in e for e in errors)
