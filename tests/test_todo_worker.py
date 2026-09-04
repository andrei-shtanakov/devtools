"""Offline tests for the plan-item worker.

Nothing here calls a harness. What is tested is what DECIDES: the gate that
turns `--mode execute` into a sandbox, the refusal that must say why, the
invariant that a model cannot report work the sandbox forbade, and the id
sanitiser that keeps a result inside `out/`.
"""

from __future__ import annotations

import todo_worker as tw


def _pack(grade: str = "rich", execute: bool = True, **extra) -> dict:
    pack = {
        "node_id": "todo://devtools/x",
        "item": {"repo": "devtools", "id": "x", "title": "t"},
        "completeness": {
            "grade": grade,
            "reason": "a written requirement was found",
            "execute_allowed": execute,
            "unknown_sources": [],
            "note": None,
        },
    }
    pack["completeness"].update(extra)
    return pack


# ─────────────────────────── the gate ───────────────────────────


def test_execute_needs_both_the_mode_and_the_pack():
    assert tw.effective_execute("execute", _pack()) is True
    assert tw.effective_execute("plan", _pack()) is False, "режим тоже обязателен"


def test_execute_is_refused_on_every_grade_below_rich():
    for grade in ("thin", "bare"):
        pack = _pack(grade=grade, execute=False)
        assert tw.effective_execute("execute", pack) is False, grade


def test_gate_reads_the_pack_not_the_grade_string():
    """`execute_allowed` is the pack's own verdict; a grade that says `rich`
    while the flag says no must not be second-guessed here."""
    assert tw.effective_execute("execute", _pack(grade="rich", execute=False)) is False


def test_refusal_names_the_reason_and_the_unread_sources():
    """The operator did not assemble this context, so «нельзя» without «почему»
    would send them to read the pack by hand — issue_worker's `--internal` they
    type themselves, this they do not."""
    pack = _pack(grade="thin", execute=False,
                 reason="only the epic's goal — no written requirement",
                 unknown_sources=["docs", "origin_issue"])
    text = tw.refusal(pack)
    assert "thin" in text
    assert "only the epic's goal" in text
    assert "docs" in text and "origin_issue" in text


def test_refusal_is_silent_about_sources_when_everything_was_read():
    text = tw.refusal(_pack(grade="bare", execute=False, reason="nothing"))
    assert "не прочитан" not in text


# ─────────────────── the model cannot outrun the sandbox ───────────────────


def test_plan_mode_strips_changed_files_the_model_claims():
    """`plan` runs the harness read-only, so a claim of changed files is false
    by construction. issue_worker pins `decision` for the same reason."""
    result = {"outcome": "done", "summary": "s", "next_step": "n",
              "changed_files": ["a.py", "b.py"], "todo_line_update": ""}
    fixed = tw.enforce_mode(dict(result), execute=False)
    assert fixed["changed_files"] == []
    assert fixed["outcome"] == "needs_human", "ложный отчёт о правках — не done"


def test_execute_mode_keeps_the_reported_files():
    result = {"outcome": "done", "summary": "s", "next_step": "n",
              "changed_files": ["a.py"], "todo_line_update": ""}
    fixed = tw.enforce_mode(dict(result), execute=True)
    assert fixed["changed_files"] == ["a.py"]
    assert fixed["outcome"] == "done"


def test_plan_mode_leaves_an_honest_result_alone():
    result = {"outcome": "blocked", "summary": "s", "next_step": "n",
              "changed_files": [], "todo_line_update": ""}
    assert tw.enforce_mode(dict(result), execute=False) == result


# ─────────────────────────── paths and ids ───────────────────────────


def test_id_grammar_refuses_a_path_that_escapes_the_output_root():
    """`parse_uri` accepts `(.+)`, so an id is attacker-shaped input as far as
    `result_path` is concerned."""
    for bad in ("../../etc/passwd", "a/b", "..", "", "Upper", "sp ace", "-lead"):
        try:
            tw.require_id(bad)
        except tw.WorkerError:
            continue
        raise AssertionError(f"принят недопустимый id: {bad!r}")


def test_id_grammar_accepts_what_the_contract_allows():
    for good in ("x", "todo-context-pack", "rd-007", "eco.plan-fields", "a1"):
        assert tw.require_id(good) == good


def test_repo_is_sanitised_too(tmp_path):
    try:
        tw.require_id("../devtools")
    except tw.WorkerError:
        return
    raise AssertionError("репо в пути результата — тот же вход, что id")


def test_result_path_stays_under_the_output_root(tmp_path):
    path = tw.result_path(tmp_path, "devtools", "todo-context-pack")
    assert path == tmp_path / "todo" / "devtools" / "todo-context-pack" / "result.json"
    assert tmp_path in path.parents


# ─────────────────────────── the prompt ───────────────────────────


def test_prompt_carries_the_pack_and_forbids_publishing():
    prompt = tw.build_prompt("# todo://devtools/x\n\nтело пункта", execute=True)
    assert "тело пункта" in prompt
    for forbidden in ("commit", "push", "PR", "merge"):
        assert forbidden in prompt, f"запрет на {forbidden} не назван"


def test_plan_prompt_forbids_editing_at_all():
    prompt = tw.build_prompt("# todo://devtools/x", execute=False)
    assert "Read and analyze only" in prompt


# ─────────────────────────── exit codes ───────────────────────────


def test_refused_execute_exits_four_and_says_why(tmp_path, capsys):
    """Exit 4: 2 and 3 keep the meanings `issue_worker` gave them."""
    pack = tmp_path / "pack.json"
    pack.write_text(__import__("json").dumps(
        {"node_id": "todo://devtools/x",
         "item": {"repo": "devtools", "id": "x"},
         "completeness": {"grade": "thin", "reason": "only the epic's goal",
                          "execute_allowed": False,
                          "unknown_sources": ["origin_issue"], "note": None}}),
        encoding="utf-8")
    code = tw.main(["--pack", str(pack), "--mode", "execute",
                    "--output-root", str(tmp_path)])
    assert code == 4
    assert "execute запрещён" in capsys.readouterr().err


def test_bad_input_exits_two(tmp_path, capsys):
    assert tw.main(["--output-root", str(tmp_path)]) == 2
    assert "нужен --uri" in capsys.readouterr().err


def test_dry_run_shows_the_prompt_without_calling_the_harness(tmp_path, capsys):
    import json as _json
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps(
        {"node_id": "todo://devtools/x",
         "item": {"node_id": "todo://devtools/x",
                  "repo": "devtools", "id": "x", "title": "t", "status": "open",
                  "epic": None, "defect": None, "owner": None, "trigger": None,
                  "section": None, "path": "TODO.md", "line": 1,
                  "source_line": None, "tags": {}},
         "body": {"text": None, "lines": 0}, "epic": None,
         "graph": {"blocked_by": [], "blocks": [], "unresolved_refs": [],
                   "diagnostics": [], "unread_repos": [], "legacy_waits": []},
         "docs": {"named": [], "mentions": []}, "rules": [], "origin_issue": None,
         "sources": [{"source": "item", "state": "read", "detail": None}],
         "completeness": {"grade": "rich", "reason": "r", "execute_allowed": True,
                          "unknown_sources": [], "note": None}}), encoding="utf-8")
    code = tw.main(["--pack", str(pack), "--mode", "execute", "--dry-run",
                    "--output-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "sandbox workspace-write" in out
    assert "Do not commit" in out
    assert not (tmp_path / "todo").exists(), "dry-run ничего не пишет"


def test_a_truncated_pack_is_an_answer_not_a_traceback(tmp_path, capsys):
    """`--pack` names a file the operator supplies: a hand-edited or half-written
    one is ordinary input, and `render` would have raised a raw KeyError."""
    import json as _json
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps({"item": {"repo": "devtools", "id": "x"}}),
                    encoding="utf-8")
    assert tw.main(["--pack", str(pack), "--output-root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "pack неполон" in err and "node_id" in err


def test_a_pack_that_is_not_an_object_is_refused(tmp_path, capsys):
    pack = tmp_path / "pack.json"
    pack.write_text("[1, 2, 3]", encoding="utf-8")
    assert tw.main(["--pack", str(pack), "--output-root", str(tmp_path)]) == 2
    assert "не похож на context-pack" in capsys.readouterr().err
