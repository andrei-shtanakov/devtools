"""charter_guard: charter схемы 2 и реестр кодов (спека §1.1–1.2)."""

from __future__ import annotations

import pytest

from governance import charter_guard as cg

CH2 = "---\nschema: 2\ncode: ENC\nplan_item: todo://devtools/oracle\n---\n# Charter\n"
CH1 = "---\nspec_stage: charter\n---\n# Charter\n"
REG = '[ENC]\nworkstream = "ws-a"\napproved = 2026-09-21\n'


def test_schema1_charter_has_no_findings():
    ch = cg.read_charter(CH1)
    assert ch.schema == 1
    assert (
        cg.charter_findings(
            ch, ws_id="ws-a", registry={}, todo_ids=set(), repo="devtools"
        )
        == []
    )


def test_schema2_requires_code_and_plan_item():
    ch = cg.read_charter("---\nschema: 2\n---\n")
    out = cg.charter_findings(
        ch, ws_id="ws-a", registry={}, todo_ids=set(), repo="devtools"
    )
    assert any("code" in f for f in out) and any("plan_item" in f for f in out)


@pytest.mark.parametrize("code", ["E", "ENCODERS", "enc", "EN1"])
def test_bad_code_shape(code):
    ch = cg.Charter(schema=2, code=code, plan_item="todo://devtools/x")
    out = cg.charter_findings(
        ch,
        ws_id="ws-a",
        registry={code: {"workstream": "ws-a"}},
        todo_ids={"x"},
        repo="devtools",
    )
    assert any("CODE" in f or "code" in f for f in out)


def test_code_outside_registry():
    ch = cg.read_charter(CH2)
    out = cg.charter_findings(
        ch, ws_id="ws-a", registry={}, todo_ids={"oracle"}, repo="devtools"
    )
    assert any("не зарегистрирован" in f for f in out)


def test_code_must_be_registered_to_this_workstream():
    ch = cg.read_charter(CH2)
    reg = cg.load_registry('[ENC]\nworkstream = "other"\napproved = 2026-09-21\n')
    out = cg.charter_findings(
        ch, ws_id="ws-a", registry=reg, todo_ids={"oracle"}, repo="devtools"
    )
    assert any("other" in f for f in out)


def test_plan_item_must_exist_in_own_repo_todo():
    ch = cg.read_charter(CH2)
    reg = cg.load_registry(REG)
    assert (
        cg.charter_findings(
            ch, ws_id="ws-a", registry=reg, todo_ids={"oracle"}, repo="devtools"
        )
        == []
    )
    out = cg.charter_findings(
        ch, ws_id="ws-a", registry=reg, todo_ids=set(), repo="devtools"
    )
    assert any("oracle" in f for f in out)
    ch_foreign = cg.Charter(schema=2, code="ENC", plan_item="todo://spec-runner/oracle")
    out = cg.charter_findings(
        ch_foreign, ws_id="ws-a", registry=reg, todo_ids={"oracle"}, repo="devtools"
    )
    assert any("spec-runner" in f for f in out)


def test_registry_only_grows():
    base = cg.load_registry(REG)
    assert cg.registry_findings(base, cg.load_registry("")) != []  # удаление
    moved = cg.load_registry('[ENC]\nworkstream = "ws-b"\napproved = 2026-09-21\n')
    assert cg.registry_findings(base, moved) != []  # переназначение
    grown = cg.load_registry(
        REG + '[ABC]\nworkstream = "ws-c"\napproved = 2026-09-29\n'
    )
    assert cg.registry_findings(base, grown) == []


def test_duplicate_key_is_a_parse_error():
    with pytest.raises(ValueError):
        cg.load_registry(REG + REG)


def test_code_is_immutable():
    base = cg.read_charter(CH2)
    head = cg.read_charter(CH2.replace("ENC", "ENX"))
    assert cg.code_change_findings(base, head) != []
    assert cg.code_change_findings(None, head) == []
    assert cg.code_change_findings(base, base) == []


def test_stamp_and_register_roundtrip():
    stamped = cg.stamp_charter(CH1, code="ENC", plan_item="todo://devtools/oracle")
    ch = cg.read_charter(stamped)
    assert (ch.schema, ch.code, ch.plan_item) == (2, "ENC", "todo://devtools/oracle")
    reg_text = cg.register_code("", code="ENC", ws_id="ws-a", approved="2026-09-29")
    assert cg.load_registry(reg_text)["ENC"]["workstream"] == "ws-a"
    with pytest.raises(ValueError):
        cg.register_code(reg_text, code="ENC", ws_id="ws-b", approved="2026-09-29")


def _git(repo, *args):
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def test_repo_findings_registry_removal_against_base(tmp_path):
    repo = tmp_path / "devtools"
    (repo / "workstreams/ws-a/spec").mkdir(parents=True)
    (repo / "workstreams/ws-a/spec/00-charter.md").write_text(CH2)
    (repo / "workstreams/codes.toml").write_text(REG)
    (repo / "TODO.md").write_text("- [ ] пункт @id:oracle\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    assert cg.repo_findings(repo, "HEAD") == []
    (repo / "workstreams/codes.toml").write_text("")
    out = cg.repo_findings(repo, "HEAD")
    assert any("удалён" in f for f in out) and any(
        "не зарегистрирован" in f for f in out
    )
    assert cg.main(["--repo", str(repo), "--base", "HEAD"]) == 1
