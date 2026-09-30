import subprocess
from pathlib import Path

from conductor.collect import collect, read_epics, read_manifest
from conductor.manifest import UMBRELLA


def _repo(path: Path, files: dict[str, str]) -> None:
    up = path.parent / (path.name + "-up")
    up.mkdir(parents=True)
    subprocess.run(["git", "-C", str(up), "init", "-q", "-b", "master"], check=True)
    for name, text in files.items():
        (up / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )
    subprocess.run(["git", "clone", "-q", str(up), str(path)], check=True)


def test_read_epics_states(tmp_path: Path) -> None:
    assert read_epics(tmp_path)[:2] == ({}, "error")
    _repo(tmp_path / UMBRELLA, {"epics.toml": "x = ["})
    assert read_epics(tmp_path)[1] == "error"


def test_read_epics_ok(tmp_path: Path) -> None:
    _repo(
        tmp_path / UMBRELLA,
        {
            "epics.toml": (
                'schema_version = "1.0.0"\nadopted_at = "2026-09-01"\n'
                "[coverage_policy]\nrobin_cutover_todo = 0.98\n"
                "robin_cutover_issues = 0.90\nrobin_cutover_prs = 0.90\n"
                'missing_error_after = "2026-11-01"\nmin_sample = 10\n'
                "[exclusions]\nmerge_commits = true\nbot_authors = []\npaths = []\n"
                '[programs.eco]\ntitle = "e"\nkind = "ecosystem"\n'
                '[epics."eco.tooling"]\ntitle = "t"\nstatus = "active"\ngoal = "g"\n'
                'opened = "2026-01-01"\n[defect_classes.code]\ntitle = "c"\n'
            )
        },
    )
    epics, state, detail, sha = read_epics(tmp_path)
    assert sha
    assert state == "read", detail
    assert epics["eco.tooling"]["status"] == "active"


def test_collect_marks_missing_sources(tmp_path: Path) -> None:
    manifest = '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    _repo(tmp_path / "a", {"TODO.md": "- [ ] x @owner:TBD @id:x\n"})
    inp = collect(
        tmp_path,
        manifest,
        ("file", None),
        None,
        False,
        lambda _: (1, "", "offline"),
        "h",
        "2026-09-29T12:00:00Z",
    )
    assert {t.repo: t.state for t in inp.todos} == {"a": "read", UMBRELLA: "error"}
    assert (inp.gh_state, inp.roadmap_state, inp.epics_state) == ("error",) * 3
    assert inp.manifest_text == manifest


def test_collect_history_reversed_tags_and_human_merge(tmp_path: Path) -> None:
    manifest = (
        '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
        '[cores.b]\nrepo_url = "git@github.com:own/b.git"\ngit_dir = "b"\n'
    )
    _repo(
        tmp_path / "a",
        {
            "TODO.md": "- [ ] x @blocked_by:todo://b/gone @owner:TBD @id:x\n",
            "CLAUDE.md": "## Git\n- Мерж: человек\n",
        },
    )
    up = tmp_path / "b-up"
    _repo(tmp_path / "b", {"TODO.md": "- [ ] g @owner:TBD @id:gone\n"})
    (up / "TODO.md").write_text("- [ ] other @owner:TBD @id:other\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qam",
            "drop",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path / "b"), "fetch", "-q"], check=True)
    inp = collect(
        tmp_path,
        manifest,
        ("file", None),
        None,
        False,
        lambda _: (1, "", "offline"),
        "h",
        "2026-09-29T12:00:00Z",
    )
    assert inp.wait_since["todo://a/x|todo://b/gone"].endswith("Z")
    assert inp.history["todo://b/gone"]
    assert inp.human_merge_repos == ["a"]
    assert inp.authority_prefixes and inp.aux_state == "read"


def test_manifest_is_read_after_umbrella_fetch(tmp_path: Path) -> None:
    old = '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    _repo(tmp_path / UMBRELLA, {"workspace-manifest.toml": old})
    up = tmp_path / (UMBRELLA + "-up")
    new = old + '[cores.b]\nrepo_url = "git@github.com:own/b.git"\ngit_dir = "b"\n'
    (up / "workspace-manifest.toml").write_text(new, encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qam",
            "add b",
        ],
        check=True,
    )
    assert read_manifest(tmp_path, do_fetch=False)[0] == old
    text, origin, errors = read_manifest(tmp_path, do_fetch=True)
    assert (text, origin[0], errors) == (new, "origin", [])
    subprocess.run(["rm", "-rf", str(up)], check=True)
    text, _, errors = read_manifest(tmp_path, do_fetch=True)
    assert text == new and errors and "fetch" in errors[0]  # деградация, не отказ
