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
from governance import criteria_product as cp
from governance import run_state
from governance.frontmatter import split_frontmatter

MIN = ctr.MinVersion("4.3.0")
REQ = "#### FR-01: A\n**Priority**: Must\n"
BEH = (
    "#### BEH-01: a\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/test_a.py`\n"
)
ACC = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n"
CH2 = "---\nschema: 2\ncode: ENC\nplan_item: todo://alpha/oracle\n---\n# C\n"
CH1 = "---\nspec_stage: charter\n---\n# C\n"
PYPROJECT = '[project]\nname = "alpha"\nversion = "0"\n'
PRODUCT_M = "def f():\n    return 1\n"  # тело функции — строка 2
TEST_A = "def test_a():\n    # ENC:BEH-01\n    assert 1\n"


def test_not_applicable_order():
    ch1 = cg.Charter(1, None, None)
    ch2 = cg.Charter(2, "ENC", "todo://devtools/x")
    assert (
        cc.decide_not_applicable(ch1, None, "9.9.9", MIN, is_vendored=True)
        == "schema-1"
    )
    exunit = type("P", (), {"name": "exunit"})()
    assert (
        cc.decide_not_applicable(ch2, exunit, "9.9.9", MIN, is_vendored=True)
        == "language"
    )
    assert (
        cc.decide_not_applicable(ch2, None, "9.9.9", MIN, is_vendored=False)
        == "spec-runner-version"
    )
    assert (
        cc.decide_not_applicable(ch2, None, "4.2.0", MIN, is_vendored=True)
        == "spec-runner-version"
    )
    assert cc.decide_not_applicable(ch2, None, "4.3.0", MIN, is_vendored=True) is None


def test_closure_file_frontmatter_records_version_and_host():
    text = cc.render_closure(
        "spec-runner-version",
        ws_id="ws",
        code=None,
        bundle_pin="p" * 40,
        product_sha="s" * 40,
        response_sha=None,
        spec_runner_version="4.2.0",
        host="pr0sto.net",
    )
    meta, _ = split_frontmatter(text)
    assert (
        meta["closure"] == "not-applicable"
        and meta["not_applicable_reason"] == "spec-runner-version"
    )
    assert meta["spec_runner_version"] == "4.2.0" and meta["host"] == "pr0sto.net"
    assert meta["code"] is None and meta["response_sha256"] is None


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ops(verify=(0, "")):
    from tests.test_governance_runner import FakeOps

    class _Ops(FakeOps):
        def create_pr(
            self, target_dir, repo_slug, branch, title, body, label, *, draft=False
        ):
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


def _env(
    tmp_path, monkeypatch, charter=CH2, dirty=False, extra: dict[str, str] | None = None
):
    from governance import run_state as rs

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True
    )
    target = tmp_path / "alpha"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(target)],
        check=True,
        capture_output=True,
    )
    bundle = target / "workstreams/ws/spec"
    bundle.mkdir(parents=True)
    for name, text in (
        ("00-charter.md", charter),
        ("10-requirements.md", REQ),
        ("15-behaviour-spec.md", BEH),
        ("25-acceptance.md", ACC),
    ):
        (bundle / name).write_text(text)
    (target / "pkg").mkdir()
    (target / "pkg/m.py").write_text(PRODUCT_M)
    (target / "tests").mkdir()
    (target / "tests/test_a.py").write_text(TEST_A)
    (target / "uv.lock").write_text("lock\n")
    (target / "pyproject.toml").write_text(PYPROJECT)
    (target / "spec-runner.config.yaml").write_text(
        "criteria:\n  product_roots: [pkg]\n"
    )
    for rel, text in (extra or {}).items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(target, "checkout", "-q", "-b", "master")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "bundle+product")
    _git(target, "push", "-q", "origin", "master")
    pin = _git(target, "rev-parse", "HEAD")
    if dirty:
        (target / "pkg/m.py").write_text("def f():\n    return 2\n")
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="ws",
        target_dir=str(target),
        bundle_dir="workstreams/ws/spec",
        profile="profiles/team-exp.yaml",
        run_id="run-1",
        authoring="waves",
    )
    state.status = "completed"
    state.base_ref = "master"
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(cc.run_state, "load", lambda rid: state)
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: pin)
    return state, target, pin


def _oracle_on(monkeypatch):
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: "4.5.0")
    monkeypatch.setattr(cc.criteria_contract, "vendored", lambda *a: True)
    monkeypatch.setattr(
        cc.criteria_contract, "read_min_version", lambda *a: ctr.MinVersion("4.5.0")
    )


def _request(target, bundle_pin, product_sha=None):
    state = run_state.load("run-1")
    return {
        "protocol": 1,
        "owner_repo": state.repo_slug,
        "workstream": state.ws_id,
        "code": "ENC",
        "bundle_pin": bundle_pin,
        "product_sha": product_sha if product_sha is not None else bundle_pin,
        "test_criteria": [{"id": "ENC:BEH-01", "verify_task": False}],
    }


def _run(line):
    return {
        "result": "complete",
        "collected": ["tests/test_a.py::test_a"],
        "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
        "outcome": "passed",
        "product_lines": [{"file": "pkg/m.py", "lines": [line]}],
        "product_line_count": 1,
        "process_operations": [],
    }


def _response(target, pin, status="traced", bundle_pin=None, **over):
    """Ответ v1 (эталон `answer.json`) на реальных байтах фикстуры `_env`:
    корни/файлы — `criteria_product.resolve_roots`, content_sha256 — тот же
    пересчёт, что сделает `_measure` (§6.1). `pin` — product_sha, по которому
    читается дерево; `bundle_pin` — отдельно, когда продукт ушёл вперёд
    одобренного бандла (по умолчанию совпадает с `pin`, как в `_env`)."""
    root = Path(target)
    tree = cp.Tree(root, pin)
    decl = cp.read_declaration(tree)
    files = cp.resolve_roots(tree, decl.roots)
    lock_sha = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    if status == "traced":
        test_files = ["tests/test_a.py"]
        definition = {"file": "tests/test_a.py", "qualname": "test_a", "line": 1}
        item = {"node_id": "tests/test_a.py::test_a", "definition": definition}
        sel = {**item, "status": "traced", "runs": [_run(2), _run(2)]}
        beh = {"id": "ENC:BEH-01", "status": "traced", "selectors": [sel]}
        test_items = [item]
    else:
        test_files, test_items = [], []
        beh = {
            "id": "ENC:BEH-01",
            "status": "unconfirmed",
            "reason": "no-test",
            "selectors": [],
        }
    content = cp.content_sha256(tree, decl, lock_sha, test_files, [])
    resp = {
        "protocol": 1,
        "request": _request(target, bundle_pin if bundle_pin is not None else pin, pin),
        "spec_runner_version": "4.5.0",
        "product_roots": {"declared": list(decl.roots), "files": list(files)},
        "test_files": test_files,
        "test_items": test_items,
        "collection_excluded": [],
        "environment": {
            "lock_sha256": lock_sha,
            "python": "3.12",
            "pytest_plugins": [],
            "groups": None,
            "extras": [],
        },
        "content_sha256": content,
        "beh": [beh],
    }
    resp.update(over)
    return resp


def _closure_on_origin(target, ops):
    branch = next(c[1] for c in ops.calls if c[0] == "create_pr")
    return (
        _git(
            target,
            "show",
            f"origin/{branch}:workstreams/ws/spec/90-acceptance-closure.md",
        )
        if not _git(target, "fetch", "-q", "origin", branch)
        else None
    )


def test_schema1_publishes_not_applicable_without_touching_checkout(
    tmp_path, monkeypatch
):
    _state, target, _pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    assert cc.run("run-1", ops) == 0
    assert (
        _git(target, "rev-parse", "--abbrev-ref", "HEAD") == "master"
    )  # чекаут не тронут
    assert not (target / "workstreams/ws/spec/90-acceptance-closure.md").exists()
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["not_applicable_reason"] == "schema-1"
    assert any(c[0] == "review" for c in ops.calls) and ops.merged


def test_request_carries_owner_slug(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    cc.run("run-1", ops)
    sent = json.loads((cc.STATE_ROOT / "run-1" / "request.json").read_text())
    assert sent["owner_repo"] == "owner/alpha"


def test_traced_measurement_published(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "traced" and meta["product_sha"] == pin


def test_retryable_error_is_step_failure_without_key(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": _request(target, pin),
        "error": {"kind": "clone-failed", "retryable": True, "detail": "x"},
    }
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2
    assert cc._load("run-1")["measured"] == {}
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2  # повтор не упирается в G6


def test_retryable_error_then_honest_answer_measures(tmp_path, monkeypatch):
    """M-2(iii): exit 2 ничего не сжигает — честный exit 0 следом измеряет."""
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": _request(target, pin),
        "error": {"kind": "clone-failed", "retryable": True, "detail": "x"},
    }
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2
    ops2 = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops2) == 0
    assert any(c[0] == "criteria_verify" for c in ops2.calls)


def test_blocking_error_publishes_blocked(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": _request(target, pin),
        "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"},
    }
    ops = _ops((3, json.dumps(err)))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "blocked"


def test_exit_code_contradicting_kind_is_refused(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": _request(target, pin),
        "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"},
    }
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2


def test_foreign_echo_on_error_response_is_refused(tmp_path, monkeypatch):
    """§5.3: эхо `request` не доверяется и в ветке ошибки — чужой
    product_sha/code — отказ шага, а не blocked; ключ не пишется, PR не
    создаётся, и G6 для настоящего содержимого этим не сжигается: честный
    ответ позже всё ещё измеряет (review I-1)."""
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    foreign_req = _request(target, pin)
    foreign_req["product_sha"] = "f" * 40
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": foreign_req,
        "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"},
    }
    ops = _ops((3, json.dumps(err)))
    assert cc.run("run-1", ops) == 2
    assert cc._load("run-1")["measured"] == {}
    assert not any(c[0] == "create_pr" for c in ops.calls)

    ops2 = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops2) == 0
    assert any(c[0] == "criteria_verify" for c in ops2.calls)


def test_owner_missing_from_test_files_blocks_through_close(tmp_path, monkeypatch):
    """Интеграция пробела 1: BEH-01 traced по tests/test_a.py, но второй
    владелец токена лежит в tests/extra/test_b.py, которого нет ни в
    test_files, ни в test_items (вне testpaths) — закрытие blocked (Must)."""
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={
            "tests/extra/test_b.py": "def test_b():\n    # ENC:BEH-01\n    assert 1\n"
        },
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "blocked"


def test_notes_reach_report_rows(tmp_path, monkeypatch):
    """M-2(ii): понижение traced→unconfirmed из-за несобранного владельца
    (excluded_owner_behs) оставляет след в `## Отчёт` опубликованного файла,
    не только в возвращаемом статусе."""
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={
            "tests/extra/test_b.py": "def test_b():\n    # ENC:BEH-01\n    assert 1\n"
        },
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    text = _closure_on_origin(target, ops)
    assert "tests/extra/test_b.py" in text and "BEH-01" in text


def test_same_content_second_measure_is_g6(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    calls = sum(c[0] == "criteria_verify" for c in ops.calls)
    assert cc.run("run-1", ops) == 6
    assert sum(c[0] == "criteria_verify" for c in ops.calls) == calls  # G6 без вызова


def test_same_content_new_response_refused(tmp_path, monkeypatch):
    """I6/G6: тот же пин и содержимое (здесь — unconfirmed-замер) — новый
    вызов не публикует второй результат, возвращает 6 без перемера."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    calls = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    assert cc.run("run-1", ops) == 6
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls


def test_same_content_on_another_machine_refused(tmp_path, monkeypatch):
    """I-4: защита от переброса видна в git — frontmatter закрытия на default
    несёт `measured_inputs` рядом с `content_key`; чистый out/ (другая
    машина, product_sha ушёл вперёд мерж-коммитом закрытия) пересчитывает
    content_sha256 на новом product_sha и находит совпадение без вызова
    spec-runner. Контрпример: продукт после merge изменился — пересчёт
    расходится, измерение идёт заново (verify вызывается)."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    branch = next(c[1] for c in ops.calls if c[0] == "create_pr")
    _git(target, "fetch", "-q", "origin", branch)
    _git(target, "merge", "-q", "--ff-only", f"origin/{branch}")
    _git(target, "push", "-q", "origin", "master")
    merged = _git(target, "rev-parse", "HEAD")

    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "other-machine")
    ops2 = _ops((0, json.dumps(_response(target, merged, bundle_pin=pin))))
    assert cc.run("run-1", ops2, product_sha=merged) == 6
    assert not any(c[0] == "criteria_verify" for c in ops2.calls)

    changed = _commit(target, "pkg/m.py", "def f():\n    return 99\n")
    _git(target, "push", "-q", "origin", "master")
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "other-machine-2")
    ops3 = _ops((0, json.dumps(_response(target, changed, bundle_pin=pin))))
    assert cc.run("run-1", ops3, product_sha=changed) == 0
    assert any(c[0] == "criteria_verify" for c in ops3.calls)


def test_g6_pre_check_sees_new_test_file_same_machine(tmp_path, monkeypatch):
    """I-2: после blocked/unconfirmed-закрытия стандартное лекарство —
    новый тестовый файл с токеном — не должно отказываться как «уже
    измерено»; неизменное содержимое по-прежнему держит G6."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    calls_before = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    assert cc.run("run-1", ops) == 6  # неизменное содержимое — G6 держит
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls_before

    new_sha = _commit(
        target,
        "tests/test_new.py",
        "def test_new():\n    # ENC:BEH-01\n    assert 1\n",
    )
    _git(target, "push", "-q", "origin", "master")
    ops2 = _ops((0, json.dumps(_response(target, new_sha, bundle_pin=pin))))
    assert cc.run("run-1", ops2, product_sha=new_sha) == 0
    assert any(c[0] == "criteria_verify" for c in ops2.calls)


def test_g6_pre_check_sees_new_test_file_cross_machine(tmp_path, monkeypatch):
    """I-2, другая машина: тот же новый тестовый файл после merge закрытия
    на default — пред-проверка по `measured_inputs` тоже не должна его
    прятать за совпавшим `content_key`."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    branch = next(c[1] for c in ops.calls if c[0] == "create_pr")
    _git(target, "fetch", "-q", "origin", branch)
    _git(target, "merge", "-q", "--ff-only", f"origin/{branch}")
    _git(target, "push", "-q", "origin", "master")

    new_sha = _commit(
        target,
        "tests/test_new.py",
        "def test_new():\n    # ENC:BEH-01\n    assert 1\n",
    )
    _git(target, "push", "-q", "origin", "master")

    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "other-machine")
    ops2 = _ops((0, json.dumps(_response(target, new_sha, bundle_pin=pin))))
    assert cc.run("run-1", ops2, product_sha=new_sha) == 0
    assert any(c[0] == "criteria_verify" for c in ops2.calls)


@pytest.mark.parametrize(
    "bad_inputs",
    [
        "not-a-dict",
        {"test_files": ["tests/test_a.py"]},  # нет excluded/outside_py
        {"test_files": "not-a-list", "excluded": [], "outside_py": "x"},
        {"test_files": [], "excluded": [], "outside_py": 7},  # outside_py не str
    ],
)
def test_malformed_remote_measured_inputs_drops_candidate_not_crash(
    tmp_path, monkeypatch, bad_inputs
):
    """M-1: битый `measured_inputs` на origin (не словарь/не хватает ключей/
    неверные типы) отбрасывает кандидата, а не роняет пред-проверку
    KeyError/TypeError — измерение идёт заново."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    bad_meta = {
        "bundle_pin": pin,
        "product_roots": ["pkg"],
        "measured_inputs": bad_inputs,
        "content_key": f"{pin}:v1:deadbeef",
    }
    monkeypatch.setattr(cc, "_remote_closure", lambda state: bad_meta)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    assert any(c[0] == "criteria_verify" for c in ops.calls)


def test_validate_answer_refusal_records_nothing(tmp_path, monkeypatch):
    """M-2(i): отказ validate_answer (здесь — подделанный content_sha256) —
    отказ шага, ничего не записывается в measured, PR не создаётся."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    resp = _response(target, pin)
    resp["content_sha256"] = "0" * 64
    ops = _ops((0, json.dumps(resp)))
    assert cc.run("run-1", ops) == 2
    assert cc._load("run-1")["measured"] == {}
    assert not any(c[0] == "create_pr" for c in ops.calls)


def test_dirty_tree_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, dirty=True)
    _oracle_on(monkeypatch)
    ops = _ops()
    assert cc.run("run-1", ops) == 2
    assert not any(c[0] == "criteria_verify" for c in ops.calls)


def test_head_not_product_sha_refused(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    (target / "x.txt").write_text("x")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "local")
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops, product_sha=pin) == 2
    assert not any(c[0] == "criteria_verify" for c in ops.calls)


def test_product_sha_not_on_default_refused(tmp_path, monkeypatch):
    _state, target, _pin = _env(tmp_path, monkeypatch)
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
    _state, target, pin = _env(tmp_path, monkeypatch)
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
    _state, target, pin = _env(tmp_path, monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: None)
    assert cc.run("run-1", ops) == 2  # n/a опубликован PR, ревью упало
    _oracle_on(monkeypatch)
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert any(c[0] == "close_pr" for c in ops.calls)
    assert len([c for c in ops.calls if c[0] == "create_pr"]) == 2


def test_response_level_not_applicable_is_step_failure(tmp_path, monkeypatch):
    """В v1 not_applicable не выпускается (дизайн §4) — получение такого
    ответа нарушает протокол: отказ шага, не закрытие not-applicable."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    na = {
        "protocol": 1,
        "request": _request(target, pin),
        "spec_runner_version": "4.5.0",
        "not_applicable": {"reason": "language"},
    }
    assert cc.run("run-1", _ops((0, json.dumps(na)))) == 2


def test_malformed_charter_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, charter="---\nschema: [2\n---\n")
    assert cc.run("run-1", _ops()) == 2


def test_run_refuses_incomplete_run(tmp_path, monkeypatch):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    state.status = "waiting_human_merge"
    assert cc.run("run-1", _ops()) == 2


@pytest.fixture(autouse=True)
def _no_real_verify(monkeypatch):
    """Страховка: реальный spec-runner в тестах не зовётся."""
    monkeypatch.setattr(
        cc.RealOps,
        "criteria_verify",
        lambda *a: (_ for _ in ()).throw(AssertionError("real verify")),
    )


def test_response_level_error_publish_failure_is_retried_not_burned(
    tmp_path, monkeypatch
):
    """I-3: ответ-ошибка (exit 3, blocked) — ключ по product_sha/дереву, не
    по stdout; сбой публикации → повтор публикует тот же текст, не 6."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": _request(target, pin),
        "error": {"kind": "collection-error", "retryable": False, "detail": "x"},
    }
    ops = _ops((3, json.dumps(err)))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    ops.review_exit = 0
    err["error"]["detail"] = "x — другой текст того же содержимого"
    ops2 = _ops((3, json.dumps(err)))
    ops2.existing_prs = ops.existing_prs
    assert cc.run("run-1", ops2) == 0
    assert not any(c[0] == "criteria_verify" for c in ops2.calls)  # не перемер


def test_republish_after_pr_closed_adopts_existing_branch(tmp_path, monkeypatch):
    """Ревью #482: PR закрытия закрыт (или create_pr упал после push) — повтор
    не пушит новый коммит в ту же ветку (non-fast-forward навсегда), а
    переиспользует существующую ветку и создаёт PR заново."""
    _state, _target, _pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    ops.existing_prs.clear()  # PR закрыт оператором: find_pr открытых — пусто
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert ops.merged


def _commit(target, path, text):
    (Path(target) / path).parent.mkdir(parents=True, exist_ok=True)
    (Path(target) / path).write_text(text)
    _git(target, "add", path)
    _git(target, "commit", "-qm", f"edit {path}")
    return _git(target, "rev-parse", "HEAD")


def test_error_key_sees_config_files_at_product_sha(tmp_path, monkeypatch):
    """devtools#515: ответ-ошибку чинят правкой не-.py (lock, pyproject, конфиг
    критериев) — ключ обязан измениться, иначе перемерить нельзя (G6)."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    root = Path(target)
    keys = [cc._tree_key(pin, root, pin)]
    for path in ("uv.lock", "pyproject.toml", "spec-runner.config.yaml"):
        sha = _commit(target, path, f"{path} v2\n")
        keys.append(cc._tree_key(pin, root, sha))
    assert len(set(keys)) == len(keys)
    # README решения об измерении не меняет: второго шанса флаки-тесту нет
    sha = _commit(target, "README.md", "docs\n")
    assert cc._tree_key(pin, root, sha) == keys[-1]


def test_error_key_reads_product_sha_not_worktree(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    root = Path(target)
    before = cc._tree_key(pin, root, pin)
    (root / "uv.lock").write_text("edited, not committed\n")
    assert cc._tree_key(pin, root, pin) == before
