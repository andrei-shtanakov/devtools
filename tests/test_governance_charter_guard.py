"""charter_guard: charter схемы 2 — сам реестр кодов (спека §1.1–1.2, rev 10)."""

from __future__ import annotations

import subprocess

import pytest

from governance import charter_guard as cg

CH2 = "---\nschema: 2\ncode: ENC\nplan_item: todo://devtools/oracle\n---\n# Charter\n"
CH1 = "---\nspec_stage: charter\n---\n# Charter\n"


def ch2(code="ENC", item="oracle"):
    return (
        f"---\nschema: 2\ncode: {code}\nplan_item: todo://devtools/{item}\n---\n# C\n"
    )


def test_schema1_charter_has_no_findings():
    ch = cg.read_charter(CH1)
    assert ch.schema == 1
    assert cg.charter_findings(ch, ws_id="ws-a", todo_ids=set(), repo="devtools") == []


def test_no_frontmatter_is_schema1_malformed_is_not():
    assert cg.read_charter("# Charter\n\nтело\n").schema == 1
    broken = cg.read_charter("---\nschema: 2\ncode: [unclosed\n---\n# C\n")
    assert broken.malformed
    out = cg.charter_findings(broken, ws_id="ws-a", todo_ids=set(), repo="devtools")
    assert any("frontmatter" in f for f in out)


def test_schema2_requires_code_and_plan_item():
    ch = cg.read_charter("---\nschema: 2\n---\n")
    out = cg.charter_findings(ch, ws_id="ws-a", todo_ids=set(), repo="devtools")
    assert any("code" in f for f in out) and any("plan_item" in f for f in out)


@pytest.mark.parametrize("code", ["E", "ENCODERS", "enc", "EN1"])
def test_bad_code_shape(code):
    ch = cg.Charter(schema=2, code=code, plan_item="todo://devtools/x")
    out = cg.charter_findings(ch, ws_id="ws-a", todo_ids={"x"}, repo="devtools")
    assert any("CODE" in f for f in out)


def test_plan_item_must_exist_in_own_repo_todo():
    ch = cg.read_charter(CH2)
    assert (
        cg.charter_findings(ch, ws_id="ws-a", todo_ids={"oracle"}, repo="devtools")
        == []
    )
    out = cg.charter_findings(ch, ws_id="ws-a", todo_ids=set(), repo="devtools")
    assert any("oracle" in f for f in out)
    foreign = cg.Charter(2, "ENC", "todo://spec-runner/oracle")
    out = cg.charter_findings(
        foreign, ws_id="ws-a", todo_ids={"oracle"}, repo="devtools"
    )
    assert any("spec-runner" in f for f in out)


def test_collision_violator_is_the_later_merged():
    charters = {"ws-a": cg.read_charter(ch2()), "ws-b": cg.read_charter(ch2())}
    out = cg.collision_findings(charters, order={"ws-a": 0, "ws-b": 1})
    assert len(out) == 1 and "ws-b" in out[0] and "ws-a" in out[0]


def test_collision_with_unmerged_charter_blames_the_unmerged():
    charters = {"ws-a": cg.read_charter(ch2()), "ws-new": cg.read_charter(ch2())}
    out = cg.collision_findings(charters, order={"ws-a": 0})
    assert len(out) == 1 and out[0].startswith("ws-new")


def test_deleting_or_moving_a_schema2_charter_is_refused():
    base = {"workstreams/ws-a/spec/00-charter.md": cg.read_charter(CH2)}
    assert cg.deletion_findings(base, {}) != []
    moved = {"workstreams/ws-renamed/spec/00-charter.md": cg.read_charter(CH2)}
    assert cg.deletion_findings(base, moved) != []
    assert cg.deletion_findings(base, base) == []
    base1 = {"workstreams/ws-a/spec/00-charter.md": cg.read_charter(CH1)}
    assert cg.deletion_findings(base1, {}) == []  # схема 1 — не надгробие


def test_code_is_immutable_except_as_collision_violator():
    base, head = cg.read_charter(ch2("ENC")), cg.read_charter(ch2("ENX"))
    assert cg.code_change_findings(base, head, violator_in_base=False) != []
    assert cg.code_change_findings(base, head, violator_in_base=True) == []
    assert cg.code_change_findings(None, head, violator_in_base=False) == []
    assert cg.code_change_findings(base, base, violator_in_base=False) == []


def test_stamp_idempotent_and_refuses_malformed():
    stamped = cg.stamp_charter(CH1, code="ENC", plan_item="todo://devtools/oracle")
    assert cg.read_charter(stamped) == cg.Charter(2, "ENC", "todo://devtools/oracle")
    assert (
        cg.stamp_charter(stamped, code="ENC", plan_item="todo://devtools/oracle")
        == stamped
    )
    plain = cg.stamp_charter("# C\n\nтело\n", code="ENC", plan_item="todo://devtools/x")
    assert cg.read_charter(plain).code == "ENC" and "тело" in plain
    with pytest.raises(ValueError):
        cg.stamp_charter(
            "---\ncode: [unclosed\n---\n", code="ENC", plan_item="todo://devtools/x"
        )


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _write(repo, ws, text):
    d = repo / f"workstreams/{ws}/spec"
    d.mkdir(parents=True, exist_ok=True)
    (d / "00-charter.md").write_text(text)


def test_repo_findings_order_from_first_parent_history(tmp_path):
    repo = tmp_path / "devtools"
    repo.mkdir()
    (repo / "TODO.md").write_text("- [ ] a @id:oracle\n- [ ] b @id:other\n")
    _git(repo, "init", "-q", "-b", "master")
    _write(repo, "ws-a", ch2())
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "ws-a")
    assert cg.repo_findings(repo, "HEAD", name="devtools") == []
    _write(repo, "ws-b", ch2(item="other"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "ws-b")
    out = cg.repo_findings(repo, "HEAD~1", name="devtools")
    assert len(out) == 1 and out[0].startswith("ws-b")
    assert cg.main(["--repo", str(repo), "--base", "HEAD~1"]) == 1


def test_repo_findings_refuses_deleted_charter(tmp_path):
    repo = tmp_path / "devtools"
    repo.mkdir()
    (repo / "TODO.md").write_text("- [ ] a @id:oracle\n")
    _git(repo, "init", "-q", "-b", "master")
    _write(repo, "ws-a", ch2())
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "ws-a")
    _git(repo, "rm", "-q", "-r", "workstreams/ws-a")
    _git(repo, "commit", "-qm", "rm")
    out = cg.repo_findings(repo, "HEAD~1", name="devtools")
    assert any("удал" in f or "перенес" in f for f in out)


@pytest.mark.parametrize("base", ["0" * 40, "deadbeef" * 5, ""])
def test_unresolvable_base_is_a_finding_not_silence(tmp_path, base):
    """Ревью #484: база, которую не прочитать, — стоп, а не «в базе нет
    charter'ов» (иначе удаление надгробия и смена кода проходят зелёными)."""
    repo = tmp_path / "devtools"
    repo.mkdir()
    (repo / "TODO.md").write_text("- [ ] a @id:oracle\n")
    _git(repo, "init", "-q", "-b", "master")
    _write(repo, "ws-a", ch2())
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "ws-a")
    out = cg.repo_findings(repo, base, name="devtools")
    assert any("баз" in f for f in out)
    assert cg.main(["--repo", str(repo), "--base", base]) == 1


def test_plan_item_change_on_schema2_is_finding():
    base = cg.Charter(2, "ENC", "todo://alpha/x")
    assert cg.plan_item_change_findings(
        "ws", base, cg.Charter(2, "ENC", "todo://alpha/y")
    )
    assert cg.plan_item_change_findings("ws", base, base) == []
    assert cg.plan_item_change_findings("ws", None, base) == []  # новый charter
    assert cg.plan_item_change_findings("ws", cg.Charter(1, None, None), base) == []


def _identity_repo(tmp_path, dirname, origin):
    repo = tmp_path / dirname
    repo.mkdir()
    (repo / "TODO.md").write_text("- [ ] a @id:oracle\n")
    _git(repo, "init", "-q", "-b", "master")
    if origin:
        _git(repo, "remote", "add", "origin", origin)
    _write(repo, "ws-a", ch2())
    return repo


@pytest.mark.parametrize(
    "origin",
    ["git@github.com:o/devtools.git", "https://github.com/o/devtools"],
)
def test_repo_identity_is_origin_slug_not_directory(tmp_path, monkeypatch, origin):
    """#481 M-5: worktree `devtools-oracle-slice1` и неканонический каталог —
    не «чужой репо»: имя репо — из origin, а не из имени каталога."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    repo = _identity_repo(tmp_path, "devtools-oracle-slice1", origin)
    assert cg.repo_identity(repo, None) == "devtools"
    assert cg.main(["--repo", str(repo)]) == 0


def test_repo_identity_explicit_slug_and_env_win(tmp_path, monkeypatch):
    repo = _identity_repo(tmp_path, "x", "https://github.com/o/devtools.git")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/alpha")
    assert cg.repo_identity(repo, None) == "alpha"
    assert cg.repo_identity(repo, "o/beta") == "beta"


def test_unknown_repo_identity_is_finding_not_directory_guess(
    tmp_path, monkeypatch, capsys
):
    """Без origin/GITHUB_REPOSITORY/--repo-slug имя каталога не угадывается:
    находка (fail-closed), а с явным --repo-slug — зелёный."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    repo = _identity_repo(tmp_path, "devtools", None)
    assert cg.repo_identity(repo, None) is None
    assert cg.main(["--repo", str(repo)]) == 1
    assert "репо не опознан" in capsys.readouterr().out
    assert cg.main(["--repo", str(repo), "--repo-slug", "o/devtools"]) == 0
    ch = cg.read_charter(CH2)
    out = cg.charter_findings(ch, ws_id="ws-a", todo_ids={"oracle"}, repo=None)
    assert any("репо не опознан" in f for f in out)
