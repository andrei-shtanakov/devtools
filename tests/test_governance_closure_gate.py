"""closure_gate: [x] пункта плана требует файл закрытия (спека §7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import closure_gate as g

CH2 = "---\nschema: 2\ncode: {code}\nplan_item: todo://repo/{item}\n---\n"


def gate(repo: Path, **kw):
    """Гейт от имени репо `repo` (slug `o/repo`), если slug не задан явно."""
    kw.setdefault("slug", "o/repo")
    return g.gate_findings(repo, **kw)


def make(
    tmp_path: Path,
    *,
    done: bool,
    closure: str | None,
    item="oracle",
    ws="ws-a",
    code="ENC",
) -> Path:
    repo = tmp_path / "repo"
    (repo / f"workstreams/{ws}/spec").mkdir(parents=True, exist_ok=True)
    (repo / f"workstreams/{ws}/spec/00-charter.md").write_text(
        CH2.format(code=code, item=item)
    )
    mark = "x" if done else " "
    (repo / "TODO.md").write_text(f"- [{mark}] пункт @id:{item}\n")
    if closure is not None:
        (repo / f"workstreams/{ws}/spec/90-acceptance-closure.md").write_text(
            f"---\nworkstream: {ws}\ncode: {code}\n{closure}\n---\n"
        )
    return repo


NA_SR = (
    "closure: not-applicable\nnot_applicable_reason: spec-runner-version\n"
    "spec_runner_version: 4.2.0\nhost: mac"
)


@pytest.mark.parametrize(
    "closure,red",
    [
        (None, True),
        ("closure: blocked", True),
        ("closure: traced\nhuman_pending: 0", True),
        ("closure: not-applicable\nnot_applicable_reason: schema-1", True),
        (NA_SR, True),
    ],
)
def test_gate_table(tmp_path, closure, red):
    errors, _ = gate(make(tmp_path, done=True, closure=closure))
    assert bool(errors) is red


def test_na_version_names_version_and_host_in_error_and_warning(tmp_path):
    errors, _ = gate(make(tmp_path, done=True, closure=NA_SR))
    assert any("4.2.0" in e and "mac" in e for e in errors)


def test_language_is_green_with_visible_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: False)
    na = "closure: not-applicable\nnot_applicable_reason: language"
    errors, warns = gate(make(tmp_path, done=True, closure=na))
    assert errors == [] and any("language" in w for w in warns)


def test_gate_reads_a_real_rendered_closure(tmp_path):
    from governance import criteria_close

    repo = make(tmp_path, done=True, closure=None)
    text = criteria_close.render_closure(
        "spec-runner-version",
        ws_id="ws-a",
        code=None,
        bundle_pin="p" * 40,
        product_sha="s" * 40,
        response_sha=None,
        spec_runner_version=None,
        host="mac",
    )
    (repo / "workstreams/ws-a/spec/90-acceptance-closure.md").write_text(text)
    errors, _ = gate(repo)
    assert errors  # n/a spec-runner-version — всегда перегнать


def test_open_item_is_not_checked(tmp_path):
    errors, _ = gate(make(tmp_path, done=False, closure=None))
    assert errors == []


def test_slice1_closure_without_graph_is_red(tmp_path):
    """Непрочитанный граф не означает «ручных критериев нет»."""
    errors, _ = gate(
        make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 2"),
    )
    assert any("граф бандла на пине не прочитан" in e for e in errors)


def test_two_charters_same_item_both_checked(tmp_path):
    repo = make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 0")
    make(tmp_path, done=True, closure=None, ws="ws-b", code="ABC")
    errors, _ = gate(repo)
    assert any("ws-b" in e for e in errors)


@pytest.mark.parametrize(
    "line",
    [
        "- [X] пункт @id:oracle",
        "* [x] пункт @id:oracle",
        "-  [x] два пробела @id:oracle",
        "-\t[x] таб @id:oracle",
        "- [x] cf. `@id:other` @id:oracle",
        "- [x] пункт @id:other @id:oracle",
    ],
)
def test_done_forms_match_plan_fields(tmp_path, line):
    """M8-5: гейт видит `[x]` ровно там, где `plan_fields` — closed."""
    from plan_fields.parser import parse_todo

    repo = make(tmp_path, done=True, closure=None)
    (repo / "TODO.md").write_text(line + "\n")
    closed = {
        n["id"]
        for n in parse_todo(line + "\n", repo="repo")["nodes"]
        if n["declared_status"] == "closed"
    }
    assert g._closed_ids(repo, "repo") == closed
    errors, _ = gate(repo)
    assert bool(errors) is ("oracle" in closed)


def test_na_spec_runner_version_always_red(tmp_path):
    errors, _ = gate(make(tmp_path, done=True, closure=NA_SR))
    assert errors


@pytest.mark.parametrize(
    ("current", "need", "red"),
    [
        (True, False, False),
        (True, True, True),
        (True, None, True),
        (False, None, True),
        (None, None, True),
    ],
)
def test_language_na_checks_graph(tmp_path, monkeypatch, current, need, red):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: current)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: need)
    na = "closure: not-applicable\nnot_applicable_reason: language\nbundle_pin: p"
    errors, _ = gate(make(tmp_path, done=True, closure=na))
    assert bool(errors) is red


@pytest.mark.parametrize("reason", ["foo", "null", "''"])
def test_unknown_na_reason_is_red(tmp_path, reason):
    na = f"closure: not-applicable\nnot_applicable_reason: {reason}"
    errors, _ = gate(make(tmp_path, done=True, closure=na))
    assert errors


def test_slice1_test_only_closure_green_with_warning_by_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: False)
    errors, warns = gate(
        make(tmp_path, done=True, closure="closure: traced\nbundle_pin: p"),
    )
    assert errors == [] and any("без штампа" in w for w in warns)


def test_slice1_closure_with_human_criteria_by_graph_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: True)
    errors, _ = gate(
        make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 0"),
    )
    assert any("подпись человека не получена" in e for e in errors)


def test_slice1_closure_for_stale_bundle_is_red(tmp_path, monkeypatch):
    """R4-B1 в старом формате: пин не на текущий бандл — красный."""
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: False)
    errors, _ = gate(
        make(tmp_path, done=True, closure="closure: traced\nbundle_pin: p"),
    )
    assert any("не для текущего бандла" in e for e in errors)


@pytest.mark.parametrize(
    ("closure", "red"),
    [
        ("closure: traced\nstatus: proposed", True),
        ("closure: traced\nstatus: weird", True),
    ],
)
def test_traced_non_accepted_status_is_red(tmp_path, closure, red):
    errors, _ = gate(make(tmp_path, done=True, closure=closure))
    assert bool(errors) is red


def test_proposed_status_error_names_remedy(tmp_path):
    """Review PR #545: status: proposed красит oracle-gates на самом PR
    предложения — ошибка обязана назвать выход (снять [x] до штампа)."""
    errors, _ = gate(
        make(tmp_path, done=True, closure="closure: traced\nstatus: proposed"),
    )
    assert any("снимите [x]" in e for e in errors)


def test_unknown_declared_status_is_done_fail_closed(tmp_path, monkeypatch):
    """Review PR #545: declared_status неизвестного значения — не open,
    значит done, и гейт его проверяет (unknown-as-green был бы дырой)."""
    monkeypatch.setattr(
        g,
        "parse_todo",
        lambda text, *, repo: {"nodes": [{"id": "oracle", "declared_status": "weird"}]},
    )
    repo = make(tmp_path, done=True, closure=None)
    assert "oracle" in g._closed_ids(repo, "repo")
    errors, _ = gate(repo)
    assert errors


def test_accepted_delegates_to_provenance(tmp_path, monkeypatch):
    seen = {}

    def fake(repo, spec_dir, text, *, slug, forge, **kw):
        seen.update(spec_dir=spec_dir, slug=slug, forge=forge)
        return ["нет акта"]

    monkeypatch.setattr(g.acceptance_provenance, "stamp_findings", fake)
    errors, _ = gate(
        make(tmp_path, done=True, closure="closure: traced\nstatus: accepted"),
        slug="o/repo",
        forge="F",
    )
    assert any("нет акта" in e for e in errors)
    assert seen == {"spec_dir": "workstreams/ws-a/spec", "slug": "o/repo", "forge": "F"}


@pytest.mark.parametrize(
    "text",
    ["просто текст без frontmatter\n", "---\nclosure: [unclosed\n---\n"],
    ids=["no-frontmatter", "bad-yaml"],
)
def test_unparsable_closure_is_named_finding(tmp_path, text, capsys):
    """#481 M-2: битый frontmatter закрытия — названная находка и exit 1,
    а не трейсбек из `main`."""
    repo = make(tmp_path, done=True, closure=None)
    (repo / "workstreams/ws-a/spec/90-acceptance-closure.md").write_text(text)
    errors, _ = gate(repo)
    assert any("ws-a: frontmatter закрытия не разбирается" in e for e in errors)
    assert g.main(["--repo", str(repo), "--repo-slug", "o/repo"]) == 1
    assert "frontmatter закрытия не разбирается" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("charter", "needle"),
    [
        ("---\nschema: 2\ncode: [unclosed\n---\n", "frontmatter charter"),
        ("---\nschema: 3\ncode: ENC\nplan_item: todo://repo/oracle\n---\n", "3"),
        ("---\nschema: two\n---\n", "schema"),
        # терм. ревью #554: нестроковый plan_item — находка, не TypeError
        ("---\nschema: 2\ncode: ENC\nplan_item: 123\n---\n", "plan_item 123"),
        ("---\nschema: 2\ncode: ENC\n---\n", "требует plan_item"),
        ("---\nschema: 2\ncode: ENC\nplan_item: repo#5\n---\n", "не todo://"),
    ],
    ids=[
        "malformed",
        "schema-3",
        "schema-non-int",
        "plan-item-int",
        "plan-item-absent",
        "plan-item-not-todo",
    ],
)
def test_unreadable_charter_is_red_in_gate_itself(tmp_path, charter, needle):
    """#481 M-3: charter, чей пункт плана не установить, — красный в самом
    гейте, а не тихий пропуск в расчёте на charter_guard."""
    repo = make(tmp_path, done=True, closure=None)
    (repo / "workstreams/ws-a/spec/00-charter.md").write_text(charter)
    errors, _ = gate(repo)
    assert any(e.startswith("ws-a:") and needle in e for e in errors), errors


def test_schema1_charter_is_not_gated(tmp_path):
    repo = make(tmp_path, done=True, closure=None)
    (repo / "workstreams/ws-a/spec/00-charter.md").write_text("# Charter\n")
    assert gate(repo) == ([], [])


def test_foreign_repo_item_is_not_gated_here(tmp_path):
    """#481 M-4a: `todo://other/oracle` — пункт чужого TODO; одноимённый
    `[x]` здесь не его пункт (сам charter красит charter_guard)."""
    repo = make(tmp_path, done=True, closure=None)
    (repo / "workstreams/ws-a/spec/00-charter.md").write_text(
        "---\nschema: 2\ncode: ENC\nplan_item: todo://other/oracle\n---\n"
    )
    assert gate(repo) == ([], [])


def test_unknown_repo_identity_is_red(tmp_path):
    """Slug не установлен — имя каталога не угадывается: находка."""
    errors, _ = gate(make(tmp_path, done=False, closure=None), slug=None)
    assert any("репо не опознан" in e for e in errors)


GREEN_SLICE1 = "closure: traced\nbundle_pin: p"
GREEN_LANG = "closure: not-applicable\nnot_applicable_reason: language\nbundle_pin: p"


@pytest.mark.parametrize("closure", [GREEN_SLICE1, GREEN_LANG], ids=["slice1", "lang"])
@pytest.mark.parametrize(
    ("field", "own", "value"),
    [("workstream", "ws-a", "ws-other"), ("code", "ENC", "XYZ")],
)
def test_closure_bound_to_its_charter(
    tmp_path, monkeypatch, closure, field, own, value
):
    """#481 M-4b: скопированное закрытие с совпавшим пином не зеленит чужой
    воркстрим — `workstream`/`code` закрытия обязаны совпасть с charter."""
    monkeypatch.setattr(g.acceptance_provenance, "pin_current", lambda *a: True)
    monkeypatch.setattr(g.acceptance_provenance, "human_needed", lambda *a: False)
    repo = make(tmp_path, done=True, closure=closure)
    assert gate(repo)[0] == []  # базовая половина: своё закрытие — зелёное
    path = repo / "workstreams/ws-a/spec/90-acceptance-closure.md"
    path.write_text(path.read_text().replace(f"{field}: {own}", f"{field}: {value}"))
    errors, warns = gate(repo)
    assert any(e.startswith("ws-a:") and value in e for e in errors), errors
    assert warns == []
