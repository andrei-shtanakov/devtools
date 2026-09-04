"""Offline tests for the plan-item worker.

Nothing here calls a harness. What is tested is what DECIDES: the gate that
turns `--mode execute` into a sandbox, the refusal that must say why, the
invariant that a model cannot report work the sandbox forbade, and the id
sanitiser that keeps a result inside `out/`.
"""

from __future__ import annotations

from pathlib import Path

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
    """dry-run проходит те же гейты, что боевой прогон: превью, которое зеленеет
    там, где реальный запуск откажет, — враньё."""
    import json as _json
    import subprocess
    checkout = tmp_path / "devtools"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps(
        {"node_id": "todo://devtools/x", "checkout": str(checkout),
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
    assert str(checkout) in out, "оператор видит, в каком дереве пойдёт правка"
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


def test_id_grammar_refuses_a_value_that_is_not_a_string():
    """`--pack` is operator-supplied JSON: a numeric `item.id` is ordinary input,
    and `fullmatch` raised TypeError past every guard in the file."""
    for bad in (42, None, ["x"], {"id": "x"}):
        try:
            tw.require_id(bad)
        except tw.WorkerError:
            continue
        raise AssertionError(f"принят нестроковый id: {bad!r}")


def test_a_pack_with_a_numeric_id_exits_two(tmp_path, capsys):
    import json as _json
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps(
        {"node_id": "todo://devtools/1", "item": {"repo": "devtools", "id": 1},
         "completeness": {"grade": "rich", "reason": "r", "execute_allowed": True,
                          "unknown_sources": [], "note": None}}), encoding="utf-8")
    assert tw.main(["--pack", str(pack), "--output-root", str(tmp_path)]) == 2
    assert "недопустимый идентификатор" in capsys.readouterr().err


# ─────────── регрессии первого круга ревью PR #126 ───────────


def test_the_run_happens_in_the_target_repo_not_the_callers_cwd(tmp_path):
    """`subprocess.run` inherits devtools' cwd: without this an `execute` run for
    a neighbour's item would have edited devtools' own tree."""
    checkout = tmp_path / "maestro"
    (checkout / ".git").mkdir(parents=True)
    assert tw.require_checkout({"checkout": str(checkout)}) == checkout


def test_an_unknown_checkout_refuses_instead_of_using_the_caller_cwd():
    for pack in ({}, {"checkout": None}, {"checkout": ""}):
        try:
            tw.require_checkout(pack)
        except tw.WorkerError as exc:
            assert "негде" in str(exc)
            continue
        raise AssertionError("неизвестный чекаут должен быть отказом")


def test_a_checkout_that_is_not_a_repo_is_refused(tmp_path):
    (tmp_path / "not-a-repo").mkdir()
    try:
        tw.require_checkout({"checkout": str(tmp_path / "not-a-repo")})
    except tw.WorkerError as exc:
        assert "не похож на git-репо" in str(exc)
        return
    raise AssertionError("каталог без .git — не чекаут")


def test_repo_spelling_is_normalised_by_the_contract_not_refused_here():
    """`todo_context` resolves `Maestro` deliberately; refusing it here would
    undo that and read as "нет такого пункта" for an item that is right there."""
    import inspect
    source = inspect.getsource(tw.load_pack)
    assert "require_id(repo)" not in source, "сырое имя репо не санитайзится"


def test_a_non_object_harness_result_is_an_answer_not_a_traceback(tmp_path,
                                                                  monkeypatch):
    """The author made `--pack` degrade honestly; the harness envelope is the
    same kind of untrusted input."""
    import subprocess as sp

    class _Done:
        returncode = 0

    def fake_run(cmd, cwd=None):
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text("[1, 2]")
        return _Done()

    monkeypatch.setattr(sp, "run", fake_run)
    monkeypatch.setattr(tw.subprocess, "run", fake_run)
    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    try:
        tw.run_harness("prompt", execute=False, cwd=checkout)
    except tw.HarnessError as exc:
        assert "не объект" in str(exc)
        return
    raise AssertionError("список вместо объекта — не структурированный ответ")


# ─────────── регрессии второго круга ревью PR #126 ───────────


def test_prompt_carries_the_repo_rules_it_claims_to_carry():
    """The prompt named «the repo's own rules» while `render` deliberately keeps
    their text in `--json` — so an execute run edited a neighbour's tree without
    ever seeing its scope fence."""
    rules = [{"path": "CLAUDE.md", "bytes": 400, "truncated": False,
              "text": "READ-ONLY к соседним репо. Прямые коммиты в master запрещены."}]
    prompt = tw.build_prompt("# todo://maestro/x", execute=True, rules=rules)
    assert "READ-ONLY к соседним репо" in prompt
    assert "CLAUDE.md" in prompt


def test_prompt_says_plainly_when_there_are_no_rules_to_carry():
    """Проверяется РАЗЛИЧИТЕЛЬ, а не литерал: прежняя версия ассертила строку,
    которой в модуле уже не было, и покраснеть не могла ни при какой правке."""
    fence = "scope fence"
    with_rules = tw.build_prompt("# x", execute=True, rules=[
        {"path": "CLAUDE.md", "bytes": 10, "truncated": False, "text": "правило"}])
    without = tw.build_prompt("# x", execute=True, rules=[])
    assert fence in with_rules, "тест обязан ловить и наличие тоже"
    assert fence not in without, "пустой список правил ничего не обещает"
    assert "CLAUDE.md" not in without


def test_prompt_marks_a_truncated_fence_as_truncated():
    rules = [{"path": "CLAUDE.md", "bytes": 34000, "truncated": True,
              "text": "первые байты"}]
    prompt = tw.build_prompt("# x", execute=True, rules=rules)
    assert "обрезан" in prompt, "агент должен знать, что видит не весь fence"


def test_require_pack_checks_the_types_of_the_fields_it_uses():
    """A hand-edited pack can carry the right keys with the wrong types; the
    guard checked presence only, so the traceback just moved one line down."""
    for pack in (
        {"node_id": "n", "item": {"repo": "r", "id": "i"}, "completeness": "rich"},
        {"node_id": "n", "item": {"repo": "r", "id": "i"}, "completeness": [1],
         "checkout": "/tmp/x"},
        {"node_id": "n", "item": {"repo": "r", "id": "i"}, "completeness": {},
         "checkout": 42},
    ):
        try:
            tw.require_pack(pack)
        except tw.WorkerError:
            continue
        raise AssertionError(f"принят пак с неверными типами: {pack!r}")


def test_require_pack_accepts_the_shape_todo_context_produces():
    pack = {"node_id": "todo://devtools/x", "checkout": "/tmp/devtools",
            "item": {"repo": "devtools", "id": "x"},
            "completeness": {"grade": "rich", "execute_allowed": True}}
    assert tw.require_pack(pack) is pack


def test_execute_refuses_a_dirty_target_tree(tmp_path):
    """The worker's edits and the operator's uncommitted ones become
    indistinguishable, and `changed_files` stops being checkable — the same
    lesson `accept-pr` already paid for."""
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    (tmp_path / "tracked.txt").write_text("b\n", encoding="utf-8")
    try:
        tw.require_clean_tree(tmp_path)
    except tw.WorkerError as exc:
        assert "грязное" in str(exc)
        assert "tracked.txt" in str(exc), "оператор должен видеть, что именно грязно"
        return
    raise AssertionError("грязное дерево должно быть отказом до вызова харнесса")


def test_a_clean_tree_passes(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@e",
                    "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    assert tw.require_clean_tree(tmp_path) is None


def test_plan_mode_does_not_care_about_a_dirty_tree(tmp_path, capsys):
    """`plan` runs read-only: a dirty tree cannot be confused with its work."""
    import json as _json
    import subprocess
    checkout = tmp_path / "repo"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "dirty.txt").write_text("x\n", encoding="utf-8")
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps(
        {"node_id": "todo://devtools/x", "checkout": str(checkout),
         "item": {"node_id": "todo://devtools/x", "repo": "devtools", "id": "x",
                  "title": "t", "status": "open", "epic": None, "defect": None,
                  "owner": None, "trigger": None, "section": None,
                  "path": "TODO.md", "line": 1, "source_line": None, "tags": {}},
         "body": {"text": None, "lines": 0}, "epic": None,
         "graph": {"blocked_by": [], "blocks": [], "unresolved_refs": [],
                   "diagnostics": [], "unread_repos": [], "legacy_waits": []},
         "docs": {"named": [], "mentions": []}, "rules": [], "origin_issue": None,
         "sources": [{"source": "item", "state": "read", "detail": None}],
         "completeness": {"grade": "rich", "reason": "r", "execute_allowed": True,
                          "unknown_sources": [], "note": None}}), encoding="utf-8")
    assert tw.main(["--pack", str(pack), "--dry-run",
                    "--output-root", str(tmp_path)]) == 0


# ─────────── регрессии круга 1 ревью PR #127 ───────────


def _dirty_repo(tmp_path, files: int = 1):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for i in range(files):
        (tmp_path / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    return tmp_path


def _pack_file(tmp_path, checkout):
    import json as _json
    pack = tmp_path / "pack.json"
    pack.write_text(_json.dumps(
        {"node_id": "todo://devtools/x", "checkout": str(checkout),
         "item": {"node_id": "todo://devtools/x", "repo": "devtools", "id": "x",
                  "title": "t", "status": "open", "epic": None, "defect": None,
                  "owner": None, "trigger": None, "section": None,
                  "path": "TODO.md", "line": 1, "source_line": None, "tags": {}},
         "body": {"text": None, "lines": 0}, "epic": None,
         "graph": {"blocked_by": [], "blocks": [], "unresolved_refs": [],
                   "diagnostics": [], "unread_repos": [], "legacy_waits": []},
         "docs": {"named": [], "mentions": []}, "rules": [], "origin_issue": None,
         "sources": [{"source": "item", "state": "read", "detail": None}],
         "completeness": {"grade": "rich", "reason": "r", "execute_allowed": True,
                          "unknown_sources": [], "note": None}}), encoding="utf-8")
    return pack


def test_main_refuses_execute_over_a_dirty_tree(tmp_path, capsys):
    """The guard was tested as a function, never as a GATE: deleting its call
    from `main` left the suite green."""
    checkout = _dirty_repo(tmp_path / "repo")
    pack = _pack_file(tmp_path, checkout)
    code = tw.main(["--pack", str(pack), "--mode", "execute", "--dry-run",
                    "--output-root", str(tmp_path)])
    assert code == 2
    assert "грязное" in capsys.readouterr().err


def test_main_lets_plan_through_over_a_dirty_tree(tmp_path):
    checkout = _dirty_repo(tmp_path / "repo")
    pack = _pack_file(tmp_path, checkout)
    assert tw.main(["--pack", str(pack), "--dry-run",
                    "--output-root", str(tmp_path)]) == 0


def test_the_dirty_list_says_how_many_it_did_not_show(tmp_path):
    """Silently cutting at 10 is the very thing `todo_context` stopped doing."""
    checkout = _dirty_repo(tmp_path / "repo", files=14)
    try:
        tw.require_clean_tree(checkout)
    except tw.WorkerError as exc:
        text = str(exc)
        assert "ещё 4" in text, f"обрезка не названа: {text}"
        return
    raise AssertionError("грязное дерево должно быть отказом")


def test_require_pack_checks_the_elements_of_rules_not_just_the_list():
    """`rules` was validated as a list while its elements were not: a
    hand-edited `rules: ["..."]` reached `render_rules` and raised
    AttributeError outside every handler (Copilot, PR #127)."""
    pack = {"node_id": "n", "item": {"repo": "r", "id": "i"},
            "completeness": {}, "rules": ["CLAUDE.md"]}
    try:
        tw.require_pack(pack)
    except tw.WorkerError as exc:
        assert "rules" in str(exc)
        return
    raise AssertionError("строка вместо объекта правила должна быть отказом")


def test_require_pack_accepts_well_formed_rules():
    pack = {"node_id": "n", "item": {"repo": "r", "id": "i"}, "completeness": {},
            "rules": [{"path": "CLAUDE.md", "text": "x", "truncated": False}]}
    assert tw.require_pack(pack) is pack
