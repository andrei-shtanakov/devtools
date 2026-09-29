"""criteria_close: срез 1 на настоящем git (ревью среза 1: C2, I1, I2, I5, I6)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from governance import charter_guard as cg
from governance import criteria_close as cc
from governance import criteria_contract as ctr
from governance.frontmatter import split_frontmatter

MIN = ctr.MinVersion("4.3.0")
REQ = "#### FR-01: A\n**Priority**: Must\n"
BEH = ("#### BEH-01: a\n`traces: [FR-01]`\n"
       "- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/test_a.py`\n")
ACC = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
CH2 = "---\nschema: 2\ncode: ENC\nplan_item: todo://alpha/oracle\n---\n# C\n"
CH1 = "---\nspec_stage: charter\n---\n# C\n"


def test_not_applicable_order():
    ch1 = cg.Charter(1, None, None)
    ch2 = cg.Charter(2, "ENC", "todo://devtools/x")
    assert cc.decide_not_applicable(ch1, None, "9.9.9", MIN, is_vendored=True) == "schema-1"
    exunit = type("P", (), {"name": "exunit"})()
    assert cc.decide_not_applicable(ch2, exunit, "9.9.9", MIN, is_vendored=True) == "language"
    assert cc.decide_not_applicable(ch2, None, "9.9.9", MIN, is_vendored=False) == "spec-runner-version"
    assert cc.decide_not_applicable(ch2, None, "4.2.0", MIN, is_vendored=True) == "spec-runner-version"
    assert cc.decide_not_applicable(ch2, None, "4.3.0", MIN, is_vendored=True) is None


def test_closure_file_frontmatter_records_version_and_host():
    text = cc.render_closure("spec-runner-version", ws_id="ws", code=None, bundle_pin="p" * 40,
                             product_sha="s" * 40, response_sha=None,
                             spec_runner_version="4.2.0", host="pr0sto.net")
    meta, _ = split_frontmatter(text)
    assert meta["closure"] == "not-applicable" and meta["not_applicable_reason"] == "spec-runner-version"
    assert meta["spec_runner_version"] == "4.2.0" and meta["host"] == "pr0sto.net"
    assert meta["code"] is None and meta["response_sha256"] is None


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _ops(verify=(0, "")):
    from tests.test_governance_runner import FakeOps

    class _Ops(FakeOps):
        def create_pr(self, target_dir, repo_slug, branch, title, body, label, *, draft=False):
            self.calls.append(("create_pr", branch, label))
            number = 100 + len(self.existing_prs)
            self.existing_prs[branch] = number
            return number

        def close_pr(self, repo_slug, pr, comment):
            self.calls.append(("close_pr", pr))
            return True

        def criteria_verify(self, target_dir, request_path):
            self.calls.append(("criteria_verify", request_path))
            return verify

    return _Ops()


def _env(tmp_path, monkeypatch, charter=CH2, dirty=False):
    from governance import run_state as rs

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    target = tmp_path / "alpha"
    subprocess.run(["git", "clone", "-q", str(origin), str(target)], check=True, capture_output=True)
    bundle = target / "workstreams/ws/spec"
    bundle.mkdir(parents=True)
    for name, text in (("00-charter.md", charter), ("10-requirements.md", REQ),
                       ("15-behaviour-spec.md", BEH), ("25-acceptance.md", ACC)):
        (bundle / name).write_text(text)
    (target / "pkg").mkdir()
    (target / "pkg/m.py").write_text("def f():\n    return 1\n")
    (target / "tests").mkdir()
    (target / "tests/test_a.py").write_text("def test_a():\n    # ENC:BEH-01\n    assert 1\n")
    (target / "uv.lock").write_text("lock\n")
    _git(target, "checkout", "-q", "-b", "master")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "bundle+product")
    _git(target, "push", "-q", "origin", "master")
    pin = _git(target, "rev-parse", "HEAD")
    if dirty:
        (target / "pkg/m.py").write_text("def f():\n    return 2\n")
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="ws",
        target_dir=str(target), bundle_dir="workstreams/ws/spec",
        profile="profiles/team-exp.yaml", run_id="run-1", authoring="waves",
    )
    state.status = "completed"
    state.base_ref = "master"
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(cc.run_state, "load", lambda rid: state)
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: pin)
    return state, target, pin


def _oracle_on(monkeypatch):
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: "4.3.0")
    monkeypatch.setattr(cc.criteria_contract, "vendored", lambda *a: True)
    monkeypatch.setattr(cc.criteria_contract, "read_min_version", lambda *a: MIN)


def _response(target, pin, status="traced", **over):
    sel = {"node_id": "tests/test_a.py::test_a",
           "definition": {"file": "tests/test_a.py", "qualname": "test_a"},
           "runs": [{"phase": "call", "outcome": "passed"}] * 2,
           "product_lines": [{"file": "pkg/m.py", "line": 2}], "subprocess": False}
    beh = {"id": "ENC:BEH-01", "status": status, "selectors": [sel] if status == "traced" else []}
    if status != "traced":
        beh["reason"] = "no-test"
    resp = {"protocol": 1, "owner_repo": "alpha", "workstream": "ws", "code": "ENC",
            "bundle_pin": pin, "product_sha": pin, "product_roots": ["pkg"],
            "environment": {"lock_sha256": hashlib.sha256(b"lock\n").hexdigest(),
                            "python": "3.12", "pytest_plugins": []},
            "content_sha256": cc._content_sha(Path(target), ["pkg", "tests"]), "beh": [beh]}
    resp.update(over)
    return resp


def _closure_on_origin(target, ops):
    branch = next(c[1] for c in ops.calls if c[0] == "create_pr")
    return _git(target, "show", f"origin/{branch}:workstreams/ws/spec/90-acceptance-closure.md") \
        if not _git(target, "fetch", "-q", "origin", branch) else None


def test_schema1_publishes_not_applicable_without_touching_checkout(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    assert cc.run("run-1", ops) == 0
    assert _git(target, "rev-parse", "--abbrev-ref", "HEAD") == "master"  # чекаут не тронут
    assert not (target / "workstreams/ws/spec/90-acceptance-closure.md").exists()
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["not_applicable_reason"] == "schema-1"
    assert any(c[0] == "review" for c in ops.calls) and ops.merged


def test_traced_measurement_published(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "traced" and meta["product_sha"] == pin


def test_dirty_tree_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, dirty=True)
    _oracle_on(monkeypatch)
    ops = _ops()
    assert cc.run("run-1", ops) == 2
    assert not any(c[0] == "criteria_verify" for c in ops.calls)


def test_head_not_product_sha_refused(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    (target / "x.txt").write_text("x")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "local")
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops, product_sha=pin) == 2
    assert not any(c[0] == "criteria_verify" for c in ops.calls)


def test_product_sha_not_on_default_refused(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    _git(target, "checkout", "-q", "-b", "side")
    (target / "x.txt").write_text("x")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "side")
    side = _git(target, "rev-parse", "HEAD")
    ops = _ops((0, json.dumps(_response(target, side))))
    assert cc.run("run-1", ops, product_sha=side) == 2
    assert not any(c[0] == "criteria_verify" for c in ops.calls)


def test_bundle_read_at_pin_not_worktree(tmp_path, monkeypatch):
    """C2: локальная правка бандла после пина не видна измерению — но и дерево
    грязное, так что закрытие отказывает; закоммиченная правка не на пине
    тоже не читается: граф строится из git-объектов пина."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    (target / "workstreams/ws/spec/15-behaviour-spec.md").write_text("")
    _git(target, "commit", "-qam", "после пина")  # и дерево, и HEAD уже другие
    nodes = cc._bundle_at_pin(state, pin)
    assert nodes["15-behaviour-spec.md"] == BEH


def test_publish_failure_does_not_burn_result(tmp_path, monkeypatch):
    """I1: ревью упало после измерения — повтор публикует тот же текст без
    нового вызова spec-runner, а не отвечает «ключ измерен»."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    ops.review_exit = 0
    calls = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    assert cc.run("run-1", ops) == 0
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls
    assert ops.merged


def test_new_measurement_closes_stale_pr(tmp_path, monkeypatch):
    """I2: n/a-закрытие, потом оракул стал доступен — старый PR закрывается,
    новый несёт новое содержимое."""
    state, target, pin = _env(tmp_path, monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: None)
    assert cc.run("run-1", ops) == 2  # n/a опубликован PR, ревью упало
    _oracle_on(monkeypatch)
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert any(c[0] == "close_pr" for c in ops.calls)
    assert len([c for c in ops.calls if c[0] == "create_pr"]) == 2


def test_same_content_new_response_refused(tmp_path, monkeypatch):
    """I6/G6: тот же ключ (корни + тесты) — новый ответ не публикуется."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    # тот же пин и содержимое, результат опубликован: 6 без вызова (G6, ревью I-4)
    calls = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    assert cc.run("run-1", ops) == 6
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls


def test_unrelated_py_edit_does_not_reopen_key(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    root = Path(target)
    key_before = cc._key(pin, root, ["pkg"])
    (root / "scripts").mkdir()
    (root / "scripts/tool.py").write_text("x = 1\n")
    assert cc._key(pin, root, ["pkg"]) == key_before


def test_response_level_not_applicable_and_foreign_error(tmp_path, monkeypatch):
    """I5: not_applicable ответа — закрытие not-applicable; error с чужим
    эхом — отказ шага, не blocked."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    na = _response(target, pin)
    na.pop("beh")
    na["not_applicable"] = "language"
    ops = _ops((0, json.dumps(na)))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "not-applicable"

    state, target, pin = _env(tmp_path / "b", monkeypatch)
    _oracle_on(monkeypatch)
    err = _response(target, pin, bundle_pin="x" * 40)
    err.pop("beh")
    err["error"] = "collection"
    assert cc.run("run-1", _ops((3, json.dumps(err)))) == 2


def test_malformed_charter_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, charter="---\nschema: [2\n---\n")
    assert cc.run("run-1", _ops()) == 2


def test_run_refuses_incomplete_run(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    state.status = "waiting_human_merge"
    assert cc.run("run-1", _ops()) == 2


@pytest.fixture(autouse=True)
def _no_real_verify(monkeypatch):
    """Страховка: реальный spec-runner в тестах не зовётся."""
    monkeypatch.setattr(cc.RealOps, "criteria_verify",
                        lambda *a: (_ for _ in ()).throw(AssertionError("real verify")))



def test_package_local_tests_not_counted_as_product(tmp_path, monkeypatch):
    """I-2: тесты внутри продуктового корня (pkg/tests/...) — не продукт."""
    root = tmp_path / "r"
    (root / "pkg/tests").mkdir(parents=True)
    (root / "pkg/m.py").write_text("def f():\n    return 1\n")
    (root / "pkg/tests/test_m.py").write_text("def test_m():\n    assert 1\n")
    (root / "pkg/conftest.py").write_text("def fx():\n    return 1\n")
    lines = cc._function_lines(root, ["pkg"])
    assert "pkg/m.py" in lines
    assert "pkg/tests/test_m.py" not in lines and "pkg/conftest.py" not in lines


def test_response_level_error_publish_failure_is_retried_not_burned(tmp_path, monkeypatch):
    """I-3: ответ-ошибка без product_roots: ключ — по содержимому, не по stdout;
    сбой публикации → повтор публикует тот же текст, не 6."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = _response(target, pin)
    err.pop("beh")
    err.pop("product_roots")
    err["error"] = "collection"
    ops = _ops((3, json.dumps(err)))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    ops.review_exit = 0
    err["error"] = "collection-again"  # другой stdout того же содержимого
    ops2 = _ops((3, json.dumps(err)))
    ops2.existing_prs = ops.existing_prs
    assert cc.run("run-1", ops2) == 0
    assert not any(c[0] == "criteria_verify" for c in ops2.calls)  # не перемер


def test_same_content_on_another_machine_refused(tmp_path, monkeypatch):
    """I-4: защита от переброса видна в git — frontmatter закрытия на default
    несёт content_key; чистый out/ (другая машина) не даёт перемерить."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    # «слить» закрытие в default, как сделал бы мерж
    branch = next(c[1] for c in ops.calls if c[0] == "create_pr")
    _git(target, "fetch", "-q", "origin", branch)
    _git(target, "merge", "-q", "--ff-only", f"origin/{branch}")
    _git(target, "push", "-q", "origin", "master")
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "other-machine")
    ops2 = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops2, product_sha=_git(target, "rev-parse", "HEAD")) == 6
    assert not any(c[0] == "criteria_verify" for c in ops2.calls)



def test_republish_after_pr_closed_adopts_existing_branch(tmp_path, monkeypatch):
    """Ревью #482: PR закрытия закрыт (или create_pr упал после push) — повтор
    не пушит новый коммит в ту же ветку (non-fast-forward навсегда), а
    переиспользует существующую ветку и создаёт PR заново."""
    state, target, pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    ops.existing_prs.clear()  # PR закрыт оператором: find_pr открытых — пусто
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert ops.merged


def test_bad_roots_refused_before_hashing(tmp_path, monkeypatch):
    """Ревью #482: корни ответа проверяются до обхода файлов — «/» не
    обходит файловую систему, а даёт отказ шага."""
    state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    called = []
    monkeypatch.setattr(cc, "_content_sha", lambda *a: called.append(a) or "x")
    resp = _response(target, pin, product_roots=["/"])
    assert cc.run("run-1", _ops((0, json.dumps(resp)))) == 2
    assert not any("/" in a[1] for a in called if len(a) > 1)
