"""criteria_close: срез 1 — not-applicable, повтор ключа, файл закрытия, публикация."""
from __future__ import annotations

from pathlib import Path

from governance import charter_guard as cg
from governance import criteria_close as cc
from governance import criteria_contract as ctr
from governance.frontmatter import split_frontmatter

MIN = ctr.MinVersion("4.3.0")


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
    assert "closure: not-applicable" in text and "not_applicable_reason: spec-runner-version" in text
    meta, _ = split_frontmatter(text)  # разбирается — нет голых «-»
    assert meta["spec_runner_version"] == "4.2.0" and meta["host"] == "pr0sto.net"
    assert meta["code"] is None and meta["response_sha256"] is None
    assert "Оракул не применим" in text


def test_remeasure_same_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path)
    cc._record_measured("run-1", "PIN:CONTENT", "blocked")
    assert cc._already_measured("run-1", "PIN:CONTENT") == "blocked"
    assert cc._already_measured("run-1", "PIN:OTHER") is None


def _ops(verify=(0, "")):
    from tests.test_governance_runner import FakeOps

    class _Ops(FakeOps):
        def create_pr(self, target_dir, repo_slug, branch, title, body, label, *, draft=False):
            self.calls.append(("create_pr", branch, label))
            number = 100 + len(self.existing_prs)
            self.existing_prs[branch] = number
            return number

        def criteria_verify(self, target_dir, request_path):
            self.calls.append(("criteria_verify", request_path))
            return verify

    return _Ops()


def _completed_state(tmp_path, charter_text: str):
    from governance import run_state as rs

    target = tmp_path / "alpha"
    bundle = target / "workstreams/ws/spec"
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(charter_text)
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="ws",
        target_dir=str(target), bundle_dir="workstreams/ws/spec",
        profile="profiles/team-exp.yaml", run_id="run-1", authoring="waves",
    )
    state.status = "completed"
    return state


def test_run_publishes_not_applicable_closure_for_schema1(tmp_path, monkeypatch):
    state = _completed_state(tmp_path, "---\nspec_stage: charter\n---\n# C\n")
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(cc.run_state, "load", lambda rid: state)
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: "p" * 40)
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: None)
    ops = _ops()
    rc = cc.run("run-1", ops, product_sha="s" * 40)
    assert rc == 0
    assert ops.existing_prs  # PR создан (FakeOps.create_pr пишет existing_prs)
    assert any("90-acceptance-closure.md" in p for p in ops.committed[-1][1])
    assert any(c[0] == "review" for c in ops.calls)  # scope-аттестация
    assert ops.merged  # агентский мерж
    closure = (tmp_path / "alpha/workstreams/ws/spec/90-acceptance-closure.md").read_text()
    assert "not_applicable_reason: schema-1" in closure


def test_run_refuses_incomplete_run(tmp_path, monkeypatch):
    state = _completed_state(tmp_path, "---\nspec_stage: charter\n---\n")
    state.status = "waiting_human_merge"
    monkeypatch.setattr(cc.run_state, "load", lambda rid: state)
    assert cc.run("run-1", _ops()) == 2


REQ = "#### FR-01: A\n**Priority**: Must\n"
BEH = ("#### BEH-01: a\n`traces: [FR-01]`\n"
       "- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/test_a.py`\n")
ACC = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
CH2 = "---\nschema: 2\ncode: ENC\nplan_item: todo://alpha/oracle\n---\n# C\n"


def _measured_env(tmp_path, monkeypatch, status="traced"):
    import hashlib
    import json

    state = _completed_state(tmp_path, CH2)
    root = Path(state.target_dir)
    bundle = root / state.bundle_dir
    (bundle / "10-requirements.md").write_text(REQ)
    (bundle / "15-behaviour-spec.md").write_text(BEH)
    (bundle / "25-acceptance.md").write_text(ACC)
    (root / "pkg").mkdir()
    (root / "pkg/m.py").write_text("def f():\n    return 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_a.py").write_text("def test_a():\n    # ENC:BEH-01\n    assert 1\n")
    (root / "uv.lock").write_text("lock\n")
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "state")
    sel = {"node_id": "tests/test_a.py::test_a",
           "definition": {"file": "tests/test_a.py", "qualname": "test_a"},
           "runs": [{"phase": "call", "outcome": "passed"}] * 2,
           "product_lines": [{"file": "pkg/m.py", "line": 2}], "subprocess": False}
    beh = {"id": "ENC:BEH-01", "status": status, "selectors": [sel] if status == "traced" else []}
    if status != "traced":
        beh["reason"] = "no-test"
    response = {"protocol": 1, "owner_repo": "alpha", "workstream": "ws", "code": "ENC",
                "bundle_pin": "p" * 40, "product_sha": "s" * 40, "product_roots": ["pkg"],
                "environment": {"lock_sha256": hashlib.sha256(b"lock\n").hexdigest(),
                                "python": "3.12", "pytest_plugins": []},
                "content_sha256": cc._content_sha(root, ["pkg", "tests"]), "beh": [beh]}
    ops = _ops((0, json.dumps(response)))
    charter = cg.read_charter(CH2)
    return state, ops, charter, bundle, response


def test_measure_traced_publishes_traced(tmp_path, monkeypatch):
    state, ops, charter, bundle, _ = _measured_env(tmp_path, monkeypatch)
    rc = cc._measure_and_publish(state, ops, charter, bundle, "p" * 40, "s" * 40, "4.3.0", "mac")
    assert rc == 0
    text = (bundle / "90-acceptance-closure.md").read_text()
    meta, _ = split_frontmatter(text)
    assert meta["closure"] == "traced" and meta["host"] == "mac"


def test_measure_must_unconfirmed_publishes_blocked(tmp_path, monkeypatch):
    state, ops, charter, bundle, _ = _measured_env(tmp_path, monkeypatch, status="unconfirmed")
    assert cc._measure_and_publish(state, ops, charter, bundle, "p" * 40, "s" * 40, "4.3.0", "mac") == 0
    meta, _ = split_frontmatter((bundle / "90-acceptance-closure.md").read_text())
    assert meta["closure"] == "blocked"


def test_measure_same_key_refused_without_calling_verify(tmp_path, monkeypatch):
    state, ops, charter, bundle, _ = _measured_env(tmp_path, monkeypatch, status="unconfirmed")
    cc._measure_and_publish(state, ops, charter, bundle, "p" * 40, "s" * 40, "4.3.0", "mac")
    calls_before = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    rc = cc._measure_and_publish(state, ops, charter, bundle, "p" * 40, "s" * 40, "4.3.0", "mac")
    assert rc == 6
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls_before


def test_measure_rejects_forged_content_sha(tmp_path, monkeypatch):
    import json

    state, ops, charter, bundle, response = _measured_env(tmp_path, monkeypatch)
    response["content_sha256"] = "0" * 64
    ops = _ops((0, json.dumps(response)))
    assert cc._measure_and_publish(state, ops, charter, bundle, "p" * 40, "s" * 40, "4.3.0", "mac") == 2
