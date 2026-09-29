"""closure_gate: [x] пункта плана требует файл закрытия (спека §7.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from governance import closure_gate as g

CH2 = "---\nschema: 2\ncode: {code}\nplan_item: todo://repo/{item}\n---\n"


def make(tmp_path: Path, *, done: bool, closure: str | None, item="oracle", ws="ws-a", code="ENC") -> Path:
    repo = tmp_path / "repo"
    (repo / f"workstreams/{ws}/spec").mkdir(parents=True, exist_ok=True)
    (repo / f"workstreams/{ws}/spec/00-charter.md").write_text(CH2.format(code=code, item=item))
    mark = "x" if done else " "
    (repo / "TODO.md").write_text(f"- [{mark}] пункт @id:{item}\n")
    if closure is not None:
        (repo / f"workstreams/{ws}/spec/90-acceptance-closure.md").write_text(f"---\n{closure}\n---\n")
    return repo


NA_SR = "closure: not-applicable\nnot_applicable_reason: spec-runner-version\nspec_runner_version: 4.2.0\nhost: mac"


@pytest.mark.parametrize("closure,is_vendored,red", [
    (None, False, True),
    ("closure: blocked", False, True),
    ("closure: traced\nhuman_pending: 0", False, False),
    ("closure: not-applicable\nnot_applicable_reason: schema-1", True, False),
    (NA_SR, False, False),
    (NA_SR, True, True),
])
def test_gate_table(tmp_path, closure, is_vendored, red):
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=closure), is_vendored=is_vendored)
    assert bool(errors) is red


def test_na_version_names_version_and_host_in_error_and_warning(tmp_path):
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=NA_SR), is_vendored=True)
    assert any("4.2.0" in e and "mac" in e for e in errors)
    _, warns = g.gate_findings(make(tmp_path, done=True, closure=NA_SR), is_vendored=False)
    assert any("4.2.0" in w and "mac" in w for w in warns)


def test_language_is_green_with_visible_warning(tmp_path):
    na = "closure: not-applicable\nnot_applicable_reason: language"
    errors, warns = g.gate_findings(make(tmp_path, done=True, closure=na), is_vendored=True)
    assert errors == [] and any("language" in w for w in warns)


def test_gate_reads_a_real_rendered_closure(tmp_path):
    from governance import criteria_close
    repo = make(tmp_path, done=True, closure=None)
    text = criteria_close.render_closure(
        "spec-runner-version", ws_id="ws-a", code=None, bundle_pin="p" * 40,
        product_sha="s" * 40, response_sha=None, spec_runner_version=None, host="mac")
    (repo / "workstreams/ws-a/spec/90-acceptance-closure.md").write_text(text)
    errors, warns = g.gate_findings(repo, is_vendored=False)
    assert errors == [] and warns


def test_open_item_is_not_checked(tmp_path):
    errors, _ = g.gate_findings(make(tmp_path, done=False, closure=None), is_vendored=False)
    assert errors == []


def test_warnings_visible_for_human_pending(tmp_path):
    _, warns = g.gate_findings(make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 2"),
                               is_vendored=False)
    assert any("human" in w for w in warns)


def test_two_charters_same_item_both_checked(tmp_path):
    repo = make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 0")
    make(tmp_path, done=True, closure=None, ws="ws-b", code="ABC")
    errors, _ = g.gate_findings(repo, is_vendored=False)
    assert any("ws-b" in e for e in errors)
