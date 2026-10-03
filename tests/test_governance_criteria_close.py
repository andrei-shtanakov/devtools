"""criteria_close: срез 1 на настоящем git (ревью среза 1: C2, I1, I2, I5, I6)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from governance import approval_facts as af
from governance import charter_guard as cg
from governance import criteria_accept, run_state
from governance import criteria_close as cc
from governance import criteria_contract as ctr
from governance import criteria_product as cp
from governance.facts import Fact, Outcome
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


SNAP = af.PolicySnapshot(
    "owner/approval-policy",
    "main",
    "policy/approvers.env",
    "a" * 40,
    frozenset({"owner-human"}),
    af.policy_fingerprint({"owner-human"}),
)


def _forge_merge(ops, pr, login):
    """Мерж PR форджей: --no-ff ветки PR в master origin из отдельного клона."""
    rec = ops.forge_prs[pr]
    target = Path(rec["dir"])
    clone = target.parent / "forge"
    if not clone.exists():
        origin = _git(target, "remote", "get-url", "origin")
        subprocess.run(
            ["git", "clone", "-q", origin, str(clone)], check=True, capture_output=True
        )
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "checkout", "-q", "-B", "master", "origin/master")
    head = _git(clone, "rev-parse", f"origin/{rec['branch']}")
    _git(clone, "merge", "-q", "--no-ff", "-m", f"Merge PR #{pr}", head)
    _git(clone, "push", "-q", "origin", "master")
    rec["facts"] = {
        "state": "MERGED",
        "headRefOid": head,
        "baseRefName": "master",
        "mergedBy": {"login": login},
        "mergedAt": "2026-10-02T00:00:00Z",
        "mergeCommit": {"oid": _git(clone, "rev-parse", "HEAD")},
    }


def _ops(verify=(0, "")):
    from tests.test_governance_runner import FakeOps

    class _Ops(FakeOps):
        def create_pr(
            self, target_dir, repo_slug, branch, title, body, label, *, draft=False
        ):
            self.calls.append(("create_pr", branch, label))
            head = _git(target_dir, "ls-remote", "origin", f"refs/heads/{branch}")
            _git(target_dir, "fetch", "-q", "origin", "master")
            if self.is_ancestor(target_dir, head.split()[0], "origin/master"):
                # как gh: ветка уже влита в base — PR создать нельзя
                raise subprocess.CalledProcessError(
                    1, ["gh", "pr", "create"], stderr="No commits between"
                )
            number = 100 + len(self.existing_prs)
            self.existing_prs[branch] = number
            self.forge_prs[number] = {
                "dir": target_dir,
                "branch": branch,
                "facts": {
                    "state": "OPEN",
                    "headRefOid": head.split()[0],
                    "baseRefName": "master",
                },
            }
            return number

        def find_pr(self, repo_slug, branch, *, any_state=False):
            """Как `gh pr list --state open|all`: закрытый/влитый PR находится
            только с `any_state`; сбой запроса — RuntimeError, как RealOps."""
            self.calls.append(("find_pr", branch))
            if self.find_pr_error is not None:
                raise RuntimeError(self.find_pr_error)
            pr = self.existing_prs.get(branch)
            if pr is None or pr not in self.forge_prs or any_state:
                return pr
            return pr if self.forge_prs[pr]["facts"]["state"] == "OPEN" else None

        def close_pr(self, repo_slug, pr, comment):
            self.calls.append(("close_pr", pr))
            if pr in self.forge_prs:
                self.forge_prs[pr]["facts"]["state"] = "CLOSED"
            return True

        def criteria_verify(self, target_dir, request_path):
            self.calls.append(("criteria_verify", request_path))
            return self.verify

        def merge(self, repo_name, pr, sha, base=None):
            self.calls.append(("merge", pr, sha))
            if not self.merge_ok:
                return self.merge_code
            self.merged.append((pr, sha))
            _forge_merge(self, pr, "ai-prosto")
            return 0

        def pr_facts(self, repo_slug, pr):
            self.calls.append(("pr_facts", pr))
            if self.pr_facts_error:
                raise RuntimeError(self.pr_facts_error)
            return dict(self.forge_prs[pr]["facts"])

        def agent_login(self):
            return "ai-prosto"

        def prs_by_head_prefix(self, repo_slug, branch_prefix):
            return list(self.approval_prs)

        def is_ancestor(self, target_dir, sha, ref):
            rc = subprocess.run(
                ["git", "-C", target_dir, "merge-base", "--is-ancestor", sha, ref],
                capture_output=True,
                check=False,
            ).returncode
            return {0: True, 1: False}.get(rc)

    ops = _Ops()
    ops.verify = verify
    ops.forge_prs = {}
    ops.approval_prs = []
    ops.pr_facts_error = None
    return ops


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


def _oracle_on(monkeypatch, snapshot=None):
    monkeypatch.setattr(cc.task_bridge, "spec_runner_version", lambda: "4.5.0")
    monkeypatch.setattr(cc.criteria_contract, "vendored", lambda *a: True)
    monkeypatch.setattr(
        cc.criteria_contract, "read_min_version", lambda *a: ctr.MinVersion("4.5.0")
    )
    fact = snapshot or Fact(Outcome.FOUND, SNAP, "stub")
    monkeypatch.setattr(
        cc.approval_facts, "policy_snapshot", lambda ops, *, pinned_sha: fact
    )


def _land(target):
    """Чекаут оператора догоняет origin/master (форджа влила закрытие/штамп)."""
    _git(target, "pull", "-q", "--ff-only", "origin", "master")
    return _git(target, "rev-parse", "HEAD")


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


def test_echoless_exit3_is_refused_not_published(tmp_path, monkeypatch):
    """Final review I-2: ответ-ошибка без ретрая (exit 3) без эхо `request`
    — отказ шага (2), ничего не публикуется. Producer опускает эхо только
    на `request-invalid` (exit 2, retryable); на exit 3 эхо всегда есть —
    требовать его здесь ничего не стоит честному производителю, а чужой/
    устаревший ответ без эхо не может сжечь G6 публикацией blocked под
    деревянным ключом."""
    _, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"},
    }
    ops = _ops((3, json.dumps(err)))
    assert cc.run("run-1", ops) == 2
    assert cc._load("run-1")["measured"] == {}
    assert not any(c[0] == "create_pr" for c in ops.calls)


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


def test_unparseable_outside_py_without_token_measures_clean(tmp_path, monkeypatch):
    """Final review I-1: синтаксическая ошибка в вне-продуктовом .py без
    токена BEH не прерывает измерение (раньше — необработанный SyntaxError
    из owners()/ast.parse) — closure traced, как если бы файла не было."""
    _, target, pin = _env(
        tmp_path, monkeypatch, extra={"tests/extra/test_bad.py": "def f(:\n    pass\n"}
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "traced"


def test_unparseable_outside_py_with_token_blocks_closure(tmp_path, monkeypatch):
    """Final review I-1: токен BEH в неразбираемом .py — владелец, которого
    pytest собрать не мог бы; BEH-01 (Must) не traced, закрытие blocked —
    а не необработанный SyntaxError."""
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={"tests/extra/test_bad.py": "def f(:\n    # ENC:BEH-01\n    pass\n"},
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "blocked"


@pytest.mark.parametrize(
    "owner", ["tests/extra/test_b.py", "integration/b_test.py", "other/conftest.py"]
)
def test_test_named_owner_outside_test_files_blocks(tmp_path, monkeypatch, owner):
    """Ревью #532: кандидаты во владельцы — по шаблонам pytest (test_*.py,
    *_test.py, conftest.py) вне test_files ответа — владелец вне testpaths
    по-прежнему блокирует."""
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={owner: "def test_b():\n    # ENC:BEH-01\n    assert 1\n"},
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "blocked"


def test_non_test_named_tracked_py_with_token_does_not_block(tmp_path, monkeypatch):
    """Ревью #532: отслеживаемый .py не по шаблонам pytest и вне test_files/
    исключений (как вендоренные фикстуры владения devtools с ENC-токенами)
    — не кандидат во владельцы; закрытие traced."""
    fixture = (
        Path(__file__).resolve().parents[1]
        / "contracts/criteria-closure/v1/fixtures/ownership"
        / "19_module_helper_with_token.py"
    ).read_text()
    assert "ENC:BEH-01" in fixture
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={"contracts/fx/19_module_helper_with_token.py": fixture},
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, ops))
    assert meta["closure"] == "traced"


def test_token_under_skipped_path_still_blocks(tmp_path, monkeypatch):
    """Ревью #532: .py под skipped/ignored-путём ответа — кандидат во
    владельцы независимо от имени файла; токен там блокирует."""
    _, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={"legacy/helper.py": "def h():\n    # ENC:BEH-01\n    pass\n"},
    )
    _oracle_on(monkeypatch)
    tree = cp.Tree(Path(target), pin)
    decl = cp.read_declaration(tree)
    lock_sha = hashlib.sha256((Path(target) / "uv.lock").read_bytes()).hexdigest()
    resp = _response(
        target,
        pin,
        collection_excluded=[{"how": "skipped", "path": "legacy", "reason": "x"}],
        content_sha256=cp.content_sha256(
            tree, decl, lock_sha, ["tests/test_a.py"], ["legacy"]
        ),
    )
    ops = _ops((0, json.dumps(resp)))
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
    _land(target)  # форджа влила закрытие; повтор на той же верхушке
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
    _land(target)  # форджа влила закрытие; повтор на той же верхушке
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
    merged = _land(target)

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
    _land(target)  # форджа влила закрытие; повтор на той же верхушке
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
    _land(target)

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


def test_noop_outside_py_edit_after_merged_closure_is_g6(tmp_path, monkeypatch):
    """Ревью #532: ключ измерения — только `<pin>:v1:<content_sha256>`.
    No-op правка непродуктовой .py (scripts/tool.py без токена) сдвигает
    outside_py и может перевызвать spec-runner, но пост-проверка G6 видит
    тот же ключ — 6, без нового закрытия/PR."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0

    new_sha = _commit(target, "scripts/tool.py", "x = 1\n")
    _git(target, "push", "-q", "origin", "master")
    ops2 = _ops((0, json.dumps(_response(target, new_sha, bundle_pin=pin))))
    assert cc.run("run-1", ops2, product_sha=new_sha) == 6
    assert not any(c[0] in ("create_pr", "review") for c in ops2.calls)
    assert not ops2.merged
    # повторный прогон на том же содержимом не платит измерение заново:
    # пост-проверка сохранила вход P2, пред-проверка упирается в G6 (#532)
    ops3 = _ops((0, json.dumps(_response(target, new_sha, bundle_pin=pin))))
    assert cc.run("run-1", ops3, product_sha=new_sha) == 6
    assert not any(c[0] == "criteria_verify" for c in ops3.calls)


_OUTSIDE_KEY = (
    "Изменились файлы вне ключа измерения: tests/helpers.py; ключ остался прежним"
)


def test_fixed_helper_outside_key_is_explicit_refusal(tmp_path, monkeypatch, capsys):
    """Решение владельца (criteria-close-key-outside-helpers): хелпер тестов
    вне test_files исправлен, ответ вернул уже измеренный ключ — прежнее
    закрытие остаётся, новый результат не публикуется, код 6. Диагностика
    называет изменившиеся файлы вне ключа и не утверждает причинность."""
    _state, target, pin = _env(
        tmp_path, monkeypatch, extra={"tests/helpers.py": "OK = False\n"}
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    (key,) = cc._load("run-1")["measured"]
    before = dict(cc._entry("run-1", key))
    capsys.readouterr()

    fixed = _commit(target, "tests/helpers.py", "OK = True\n")
    _git(target, "push", "-q", "origin", "master")
    ops2 = _ops((0, json.dumps(_response(target, fixed, "unconfirmed", pin))))
    assert cc.run("run-1", ops2, product_sha=fixed) == 6
    out = capsys.readouterr().out
    assert _OUTSIDE_KEY in out and "Новый результат не опубликован" in out
    assert "решающее" not in out and "доработайте продукт" not in out
    assert "--new-attempt" not in out
    # перечисление не выдаёт себя за полное: не-.py данные тестов не видны (#540)
    assert "не-.py данные тестов не отслеживаются" in out
    assert not any(c[0] in ("create_pr", "review") for c in ops2.calls)
    assert cc._entry("run-1", key) == before


def test_fixed_helper_refusal_from_another_machine(tmp_path, monkeypatch, capsys):
    """Тот же отказ на машине без локального состояния: прежний product_sha —
    из frontmatter закрытия на default-ветке."""
    _state, target, pin = _env(
        tmp_path, monkeypatch, extra={"tests/helpers.py": "OK = False\n"}
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 0
    _land(target)
    capsys.readouterr()

    fixed = _commit(target, "tests/helpers.py", "OK = True\n")
    _git(target, "push", "-q", "origin", "master")
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path / "other-machine")
    ops2 = _ops((0, json.dumps(_response(target, fixed, "unconfirmed", pin))))
    assert cc.run("run-1", ops2, product_sha=fixed) == 6
    out = capsys.readouterr().out
    assert _OUTSIDE_KEY in out and "Новый результат не опубликован" in out
    assert not any(c[0] in ("create_pr", "review") for c in ops2.calls)


def test_post_check_unpublished_key_publishes_first_result(tmp_path, monkeypatch):
    """Ревью #532: пост-проверка нашла ключ, измеренный, но ещё не смерженный
    (ревью упало) — публикуется ПЕРВЫЙ результат (текст на P1), второй не
    пишется и запись ключа не затирается."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    (key,) = cc._load("run-1")["measured"]
    first = cc._entry("run-1", key)["text"]

    new_sha = _commit(target, "scripts/tool.py", "x = 1\n")
    _git(target, "push", "-q", "origin", "master")
    ops.verify = (0, json.dumps(_response(target, new_sha, bundle_pin=pin)))
    ops.review_exit = 0
    assert cc.run("run-1", ops, product_sha=new_sha) == 0
    assert list(cc._load("run-1")["measured"]) == [key]
    assert cc._entry("run-1", key)["text"] == first and ops.merged


def test_revert_of_outside_py_edit_keeps_measured_entry(tmp_path, monkeypatch):
    """Ревью #532 (major): P1 измерен и смержен → scripts/tool.py добавлен →
    удалён (дерево = P1). Ни один шаг не публикует второй результат того же
    содержимого и не затирает запись K1 (pr/branch/merged)."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    (key,) = cc._load("run-1")["measured"]
    before = dict(cc._entry("run-1", key))
    assert before.get("merged") and before.get("pr") and before.get("branch")

    added = _commit(target, "scripts/tool.py", "x = 1\n")
    _git(target, "push", "-q", "origin", "master")
    ops2 = _ops((0, json.dumps(_response(target, added, bundle_pin=pin))))
    assert cc.run("run-1", ops2, product_sha=added) == 6

    _git(target, "rm", "-q", "scripts/tool.py")
    _git(target, "commit", "-qm", "revert tool")
    _git(target, "push", "-q", "origin", "master")
    reverted = _git(target, "rev-parse", "HEAD")
    ops3 = _ops((0, json.dumps(_response(target, reverted, bundle_pin=pin))))
    assert cc.run("run-1", ops3, product_sha=reverted) == 6

    for o in (ops2, ops3):
        assert not any(c[0] in ("create_pr", "review") for c in o.calls)
        assert not o.merged
    assert list(cc._load("run-1")["measured"]) == [key]
    assert cc._entry("run-1", key) == before


def test_foreign_echo_on_retryable_error_response_is_refused(tmp_path, monkeypatch):
    """I-1 на ветке retryable (exit 2): чужой echo — отказ шага до ветвления
    на retryable/blocking, ключ не пишется (симметрично exit-3 тесту)."""
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    foreign_req = _request(target, pin)
    foreign_req["product_sha"] = "f" * 40
    err = {
        "protocol": 1,
        "spec_runner_version": "4.5.0",
        "request": foreign_req,
        "error": {"kind": "clone-failed", "retryable": True, "detail": "x"},
    }
    ops = _ops((2, json.dumps(err)))
    assert cc.run("run-1", ops) == 2
    assert cc._load("run-1")["measured"] == {}


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


def test_owners_missing_blob_is_step_refusal(tmp_path, monkeypatch):
    """Final review m-2: `Tree.blob()` возвращает `None` для пути, который
    `ls-tree` только что перечислил среди отслеживаемых .py (сбой
    git-объекта, не «файла нет») — отказ шага, не пустой источник, который
    тихо теряет владельца токена."""
    _, target, pin = _env(
        tmp_path, monkeypatch, extra={"tests/extra/test_x.py": "x = 1\n"}
    )
    _oracle_on(monkeypatch)
    real_blob = cp.Tree.blob

    def flaky_blob(self, path):
        if path == "tests/extra/test_x.py":
            return None
        return real_blob(self, path)

    monkeypatch.setattr(cp.Tree, "blob", flaky_blob)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 2
    assert cc._load("run-1")["measured"] == {}
    assert not any(c[0] == "create_pr" for c in ops.calls)


def test_product_syntax_devtools_cannot_parse_is_named_refusal(
    tmp_path, monkeypatch, capsys
):
    """Final review m-4: продукт с синтаксисом, который Python devtools не
    разбирает, — именованный отказ («не разбирается текущим Python»), а не
    размытое «статус ≠ пересчёту» или необработанное исключение."""
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    (target / "pkg/m.py").write_text("def f(\n")
    _git(target, "commit", "-qam", "break product syntax")
    _git(target, "push", "-q", "origin", "master")
    new_sha = _git(target, "rev-parse", "HEAD")
    ops = _ops((0, json.dumps(_response(target, new_sha, bundle_pin=pin))))
    assert cc.run("run-1", ops, product_sha=new_sha) == 2
    assert "не разбирается текущим Python" in capsys.readouterr().out


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
    assert (
        len([c for c in ops.calls if c[0] == "create_pr" and "-stamp" not in c[1]]) == 2
    )


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
    ops2.forge_prs = ops.forge_prs
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
    _land(target)  # форджа могла влить PR: коммит поверх живой верхушки
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


def test_new_measurement_config_file_buys_outside_py_digest_change(
    tmp_path, monkeypatch
):
    """Final review m-1: добавление pytest.ini, которого не было на старом
    product_sha, обязано сдвинуть `_outside_py_digest` — иначе этот способ
    починить «owner outside testpaths» не покупает перемер (нет изменения
    ни в content_sha256, который видит только уже существующий inipath
    через test_files ответа, ни в .py-слепке)."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    root = Path(target)
    files = ("pkg/m.py",)
    before = cc._outside_py_digest(cp.Tree(root, pin), files)

    new_sha = _commit(target, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    after = cc._outside_py_digest(cp.Tree(root, new_sha), files)

    assert before != after


def test_new_pytest_ini_buys_remeasure_not_g6(tmp_path, monkeypatch):
    """Final review m-1, через весь путь измерения: закрытие уже измерено
    и смержено; добавление pytest.ini, которого не было, не отвечает G6
    (6) — вызывает spec-runner заново. Честный ответ несёт новый inipath в
    test_files (эталон answer.json), поэтому content_sha256 и ключ другие —
    пост-проверка G6 (ревью #532) не отказывает."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0

    new_sha = _commit(target, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    _git(target, "push", "-q", "origin", "master")
    tree = cp.Tree(Path(target), new_sha)
    lock_sha = hashlib.sha256((Path(target) / "uv.lock").read_bytes()).hexdigest()
    test_files = ["pytest.ini", "tests/test_a.py"]
    resp = _response(
        target,
        new_sha,
        bundle_pin=pin,
        test_files=test_files,
        content_sha256=cp.content_sha256(
            tree, cp.read_declaration(tree), lock_sha, test_files, []
        ),
    )
    ops2 = _ops((0, json.dumps(resp)))
    assert cc.run("run-1", ops2, product_sha=new_sha) == 0
    assert any(c[0] == "criteria_verify" for c in ops2.calls)


def test_create_pr_failure_is_a_step_refusal_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    """Живая приёмка (polygon): нет метки criteria-close → `gh pr create`
    падает CalledProcessError. Это отказ шага (2) с диагностикой."""
    import subprocess as sp

    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))

    def boom(*a, **k):
        raise sp.CalledProcessError(
            1,
            ["gh", "pr", "create"],
            stderr="could not add label: 'criteria-close' not found",
        )

    ops.create_pr = boom
    assert cc.run("run-1", ops) == 2
    out = capsys.readouterr().out
    assert "gh pr create" in out and "could not add label" in out


def test_create_pr_without_url_is_a_step_refusal(tmp_path, monkeypatch, capsys):
    """Ревью #537: `gh` вышел 0 без URL PR — RuntimeError из RealOps тоже
    отказ шага (2), а не трейсбек."""
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))

    def no_url(*a, **k):
        raise RuntimeError("create_pr: no PR URL in 'warning: ...'")

    ops.create_pr = no_url
    assert cc.run("run-1", ops) == 2
    assert "no PR URL" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("prs", "expected"),
    [
        ([], False),
        ([{"state": "MERGED"}, {"state": "CLOSED"}], False),
        ([{"state": "MERGED"}, {"state": "OPEN"}], True),
        ([{"state": None}], None),
    ],
)
def test_approval_pr_open(prs, expected):
    class O:  # noqa: E742
        def prs_by_head_prefix(self, slug, prefix):
            assert prefix == "spec/ws-approve-"
            return prs

    class S:
        ws_id = "ws"
        repo_slug = "o/r"

    assert cc._approval_pr_open(O(), S()) is expected


def test_approval_pr_open_unavailable_on_error():
    class O:  # noqa: E742
        def prs_by_head_prefix(self, slug, prefix):
            raise RuntimeError("gh api rc=1")

    class S:
        ws_id = "ws"
        repo_slug = "o/r"

    assert cc._approval_pr_open(O(), S()) is None


TRACED = "---\nclosure: traced\nproduct_sha: abc\nhuman_criteria: {n}\n---\nтело\n"


def test_render_writes_human_criteria():
    from governance.criteria_check import Outcome as O

    text = cc.render_closure(
        O("traced", {"BEH-01": "traced"}, {"AC-01": "traced"}),
        ws_id="ws",
        code="ENC",
        bundle_pin="p",
        product_sha="s",
        response_sha="r",
        spec_runner_version="4.5.0",
        host="h",
        human_criteria=2,
    )
    assert split_frontmatter(text)[0]["human_criteria"] == 2


@pytest.mark.parametrize(
    "fake_result",
    [
        subprocess.CompletedProcess(("rev-parse", "HEAD"), 1, "", "боится"),
        subprocess.CompletedProcess(("rev-parse", "HEAD"), 0, "", ""),
    ],
    ids=["nonzero-rc", "empty-stdout"],
)
def test_push_closure_rejects_unresolved_head(tmp_path, monkeypatch, fake_result):
    """Minor 1: rev-parse после push не вернул sha — отказ шага, не пустая
    голова (деферред T4)."""
    state, _target, _pin = _env(tmp_path, monkeypatch)
    real_git = cc._git

    def fake_git(repo, *args):
        if args == ("rev-parse", "HEAD"):
            return fake_result
        return real_git(repo, *args)

    monkeypatch.setattr(cc, "_git", fake_git)
    with pytest.raises(cc.CloseError):
        cc._materialize(state, "criteria-close/x", "текст\n", "traced")


def test_advance_refuses_unresolved_proposal_head(tmp_path, monkeypatch):
    """Minor 1: отказ при нечитаемой голове предложения — ничего не
    записано, PR не открыт (Global Constraint: незаписанная голова → отказ
    шага, не публикация)."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    real_git = cc._git

    def fake_git(repo, *args):
        # только rev-parse HEAD в temp-worktree публикации (criteria-close-*),
        # не в чекауте оператора (используется _verify_product).
        if args == ("rev-parse", "HEAD") and "criteria-close-" in str(repo):
            return subprocess.CompletedProcess(args, 1, "", "боится")
        return real_git(repo, *args)

    monkeypatch.setattr(cc, "_git", fake_git)
    assert cc.run("run-1", ops) == 2
    (key,) = cc._load("run-1")["measured"]
    p = cc._entry("run-1", key)["proposal"]
    assert not p.get("head")
    assert not any(c[0] == "create_pr" for c in ops.calls)


def test_adopt_refuses_merge_commit_tip(tmp_path, monkeypatch):
    """Minor 2: голова-мерж не усыновляется, даже если первый-родительский
    дифф — ровно файл закрытия (спека §7.2a п.1: один свой коммит, деферред
    T4)."""
    state, target, pin = _env(tmp_path, monkeypatch)
    rel = "workstreams/ws/spec/90-acceptance-closure.md"
    text = "текст\n"
    (target / "other.txt").write_text("x\n")
    _git(target, "add", "other.txt")
    _git(target, "commit", "-qm", "advance master")
    _git(target, "push", "-q", "origin", "master")
    m2 = _git(target, "rev-parse", "HEAD")
    _git(target, "checkout", "-q", pin)
    _git(target, "checkout", "-q", "-b", "side")
    (target / rel).write_text(text)
    _git(target, "add", rel)
    _git(target, "commit", "-qm", "closure")
    tree_a = _git(target, "rev-parse", "HEAD^{tree}")
    evil = _git(target, "commit-tree", tree_a, "-p", pin, "-p", m2, "-m", "evil merge")
    _git(target, "push", "-q", "origin", f"{evil}:refs/heads/criteria-close/x")
    _git(target, "checkout", "-q", "master")
    with pytest.raises(cc.CloseError):
        cc._materialize(state, "criteria-close/x", text, "traced")


def test_materialize_pushes_own_single_file_commit(tmp_path, monkeypatch):
    state, target, pin = _env(tmp_path, monkeypatch)
    head = cc._materialize(state, "criteria-close/x", "текст\n", "traced")
    _git(target, "fetch", "-q", "origin", "criteria-close/x")
    rel = "workstreams/ws/spec/90-acceptance-closure.md"
    assert _git(target, "diff", "--name-only", f"{head}^", head) == rel
    assert _git(target, "rev-parse", f"{head}^") == pin
    assert cc._materialize(state, "criteria-close/x", "текст\n", "traced") == head


@pytest.mark.parametrize("extra_file", [True, False])
def test_adopt_refuses_foreign_shape(tmp_path, monkeypatch, extra_file):
    """Ревью пары M2: усыновляется только наш коммит формы §7.2a п.1."""
    state, target, _pin = _env(tmp_path, monkeypatch)
    rel = "workstreams/ws/spec/90-acceptance-closure.md"
    _git(target, "checkout", "-q", "-b", "foreign")
    (target / rel).write_text("текст\n" if extra_file else "другой\n")
    if extra_file:
        (target / "pkg/m.py").write_text("def f():\n    return 7\n")
    _git(target, "add", "-A")
    _git(target, "commit", "-qm", "foreign")
    _git(target, "push", "-q", "origin", "HEAD:refs/heads/criteria-close/x")
    _git(target, "checkout", "-q", "master")
    with pytest.raises(cc.CloseError):
        cc._materialize(state, "criteria-close/x", "текст\n", "traced")


def test_propose_records_snapshot_and_human_flag(tmp_path, monkeypatch):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    p = cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=True)
    meta, _ = split_frontmatter(p["text"])
    assert meta["status"] == "proposed" and meta["policy_source"] == SNAP.source
    assert p["human"] is True and p["head"] is None and p["base"] == "master"
    assert p["policy"]["accounts"] == ["owner-human"]
    assert p["sha256"] == criteria_accept.text_sha256(p["text"])
    _oracle_on(monkeypatch, snapshot=Fact(Outcome.UNAVAILABLE, None, "сеть"))
    assert (
        cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False) == p
    )


def test_propose_drops_inherited_slice1_pr(tmp_path, monkeypatch):
    """Ревью круга 3 R3-m1: PR среза 1 в записи ключа — не PR предложения."""
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    cc._record("run-1", "k", closure="traced", text="t", pr=7, attested=True)
    p = cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=True)
    e = cc._entry("run-1", "k")
    assert e["pr"] is None and e["attested"] is False and e["closed"] is False
    assert e["slice1_pr"] == 7 and p["branch"].endswith("-proposal")


def test_propose_flag_from_argument_not_from_text(tmp_path, monkeypatch):
    """Ревью круга 2 B-M1: текст без human_criteria (запись до 2a) не
    открывает test-only путь — флаг даёт граф."""
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    legacy = "---\nclosure: traced\nproduct_sha: abc\n---\nтело\n"
    assert cc._propose(state, _ops(), "run-1", "k", legacy, human=True)["human"] is True


@pytest.mark.parametrize(
    "kind",
    [
        af.POLICY_REFUSAL_ENV,
        af.POLICY_REFUSAL_SOURCE,
        af.POLICY_REFUSAL_ABSENT,
        af.POLICY_REFUSAL_EMPTY,
    ],
)
def test_propose_refuses_forbidden_policy(tmp_path, monkeypatch, kind):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    refusal = Fact(Outcome.FORBIDDEN, af.PolicyRefusal(kind, "нет"), "нет")
    _oracle_on(monkeypatch, snapshot=refusal)
    with pytest.raises(cc.CloseError) as exc:
        cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False)
    waiting = kind in (af.POLICY_REFUSAL_ABSENT, af.POLICY_REFUSAL_EMPTY)
    assert ("wait: policy" in str(exc.value)) is waiting
    assert "proposal" not in (cc._entry("run-1", "k") or {})


def test_propose_unavailable_policy_is_step_refusal(tmp_path, monkeypatch):
    state, _target, _pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch, snapshot=Fact(Outcome.UNAVAILABLE, None, "сеть"))
    with pytest.raises(cc.CloseError, match="не установлен"):
        cc._propose(state, _ops(), "run-1", "k", TRACED.format(n=0), human=False)


def test_policy_config_refusal(monkeypatch):
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV, raising=False)
    assert cc._policy_config_refusal() is None
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "x")
    assert "выставлена" in cc._policy_config_refusal()
    monkeypatch.delenv(af.APPROVER_ALLOWLIST_ENV)

    def broken():
        raise RuntimeError("битый source.env")

    monkeypatch.setattr(cc.approval_facts, "policy_source", broken)
    assert "битый" in cc._policy_config_refusal()


BEH_HUMAN = BEH + (
    "#### BEH-02: оператор читает отчёт\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_HUMAN = ACC + (
    "#### AC-02: отчёт · verification: manual\ntraces: [FR-01]\nscenarios: [BEH-02]\n"
)
REQ_SHOULD = "#### FR-01: A\n**Priority**: Should\n"
BEH_MIXED = BEH + (
    "#### BEH-02: ручной\n`traces: [FR-01]`\n"
    "- **checked_by**: `status: planned` `kind: manual` `owner: qa`\n"
)
ACC_MIXED = (
    "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01, BEH-02]\n"
)
SPEC = "workstreams/ws/spec"


def _env_human(tmp_path, monkeypatch):
    return _env(
        tmp_path,
        monkeypatch,
        extra={
            f"{SPEC}/15-behaviour-spec.md": BEH_HUMAN,
            f"{SPEC}/25-acceptance.md": ACC_HUMAN,
        },
    )


def _proposal_pr(ops):
    return min(ops.forge_prs)


def _key():
    (key,) = cc._load("run-1")["measured"]
    return key


def test_human_criterion_waits_for_human_merge(tmp_path, monkeypatch, capsys):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    p = cc._entry("run-1", _key())["proposal"]
    assert (
        "create_pr",
        p["branch"],
        "criteria-close,human-merge-required",
    ) in ops.calls
    assert p["head"] == ops.forge_prs[_proposal_pr(ops)]["facts"]["headRefOid"]
    assert any(c[0] == "review" for c in ops.calls) and not ops.merged
    assert "человеком" in capsys.readouterr().out


def test_human_flag_by_graph_not_ac_status(tmp_path, monkeypatch):
    """Ревью пары M1: Should-AC unconfirmed + ручной BEH — всё равно человек."""
    _state, target, pin = _env(
        tmp_path,
        monkeypatch,
        extra={
            f"{SPEC}/10-requirements.md": REQ_SHOULD,
            f"{SPEC}/15-behaviour-spec.md": BEH_MIXED,
            f"{SPEC}/25-acceptance.md": ACC_MIXED,
        },
    )
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin, status="unconfirmed"))))
    assert cc.run("run-1", ops) == 4
    assert not ops.merged


def test_human_flag_ignores_missing_field_in_text(tmp_path, monkeypatch):
    """Ревью круга 2 B-M1: текст без human_criteria (как у записи среза 1) —
    флаг всё равно из графа: выход 4, агент не мержит."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    real = cc.render_closure

    def legacy(*a, **k):
        k.pop("human_criteria", None)
        return real(*a, **k)

    monkeypatch.setattr(cc, "render_closure", legacy)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    assert not ops.merged


def test_node_deleted_on_tip_supersedes(tmp_path, monkeypatch):
    """Ревью круга 2 B-M3: узла нет на верхушке — superseded, не вечный отказ."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    pr = _proposal_pr(ops)
    _forge_merge(ops, pr, "owner-human")
    clone = Path(ops.forge_prs[pr]["dir"]).parent / "forge"
    _git(clone, "rm", "-q", f"{SPEC}/25-acceptance.md")
    _git(clone, "commit", "-qm", "drop node")
    _git(clone, "push", "-q", "origin", "master")
    assert cc.run("run-1", ops) == 5
    acc = cc._entry("run-1", _key())["acceptance"]
    assert acc["state"] == "superseded" and "bundle" in acc["reason"]


def test_human_merge_by_snapshot_account_records_merge(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    e = cc._entry("run-1", _key())
    assert e["merge"]["by"] == "owner-human" and e["merged"] is True


def test_resume_after_human_merge_needs_no_checkout(tmp_path, monkeypatch):
    """Review Focus 1: верхушка ушла вперёд, чекаут на старом product_sha."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    calls = sum(1 for c in ops.calls if c[0] == "criteria_verify")
    cc.run("run-1", ops)
    assert _git(target, "rev-parse", "HEAD") == pin
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == calls
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"


def test_human_pr_still_open_keeps_waiting(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    assert cc.run("run-1", ops) == 4
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 1
    assert sum(1 for c in ops.calls if c[0] == "review") == 1  # аттестация записана


def test_human_criterion_merged_by_outsider_is_rejected(tmp_path, monkeypatch):
    """§8.4 п.5 тестом: на polygon третьей учётки нет."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "stranger")
    assert cc.run("run-1", ops) == 5
    assert cc._entry("run-1", _key())["acceptance"]["state"] == "rejected"
    assert not any("-stamp" in c[1] for c in ops.calls if c[0] == "create_pr")


@pytest.mark.parametrize("human", [True, False])
def test_closed_proposal_is_rejected_on_both_paths(
    tmp_path, monkeypatch, capsys, human
):
    """Review Focus 2 / ревью пары B1(б)."""
    env = _env_human if human else _env
    _state, target, pin = env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.merge_ok = False  # test-only: обвязка отказала, PR остался открытым
    assert cc.run("run-1", ops) == (4 if human else 2)
    ops.forge_prs[_proposal_pr(ops)]["facts"]["state"] = "CLOSED"
    assert cc.run("run-1", ops) == 5
    assert cc.run("run-1", ops) == 5
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 1
    assert "closed" in capsys.readouterr().out


def test_test_only_merged_by_human_goes_to_predicate(tmp_path, monkeypatch):
    """Ревью пары B1(а): на polygon нет review-kit — PR предложения мержит человек."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.merge_ok = False
    assert cc.run("run-1", ops) == 2
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"
    assert (
        sum(1 for c in ops.calls if c[0] == "create_pr" and "-stamp" not in c[1]) == 1
    )


def test_review_failure_on_human_path_is_retried_not_buried(tmp_path, monkeypatch):
    """Review Focus 3 / ревью пары B2."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    assert cc._entry("run-1", _key())["proposal"]["head"]  # своя голова уже записана
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    cc.run("run-1", ops)
    e = cc._entry("run-1", _key())
    assert e["merge"]["by"] == "owner-human"
    assert e.get("acceptance", {}).get("state") != "rejected"


def test_unreadable_pr_facts_is_step_refusal_then_resume(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    ops.pr_facts_error = "gh: network"
    assert cc.run("run-1", ops) == 2
    assert "acceptance" not in cc._entry("run-1", _key())
    ops.pr_facts_error = None
    cc.run("run-1", ops)
    assert cc._entry("run-1", _key())["merge"]["by"] == "owner-human"


def test_open_approval_pr_supersedes(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.approval_prs = [{"state": "OPEN", "number": 9}]
    assert cc.run("run-1", ops) == 5
    acc = cc._entry("run-1", _key())["acceptance"]
    assert acc["state"] == "superseded" and "approval" in acc["reason"]


def test_policy_env_refuses_command_in_any_phase(tmp_path, monkeypatch, capsys):
    """Ревью пары m1: FORBIDDEN env — отказ команды и после открытия PR."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    monkeypatch.setenv(af.APPROVER_ALLOWLIST_ENV, "x")
    assert cc.run("run-1", ops) == 2
    assert "отказ команды" in capsys.readouterr().out
    assert "merge" not in cc._entry("run-1", _key())


def test_slice1_pr_close_failure_is_step_refusal(tmp_path, monkeypatch):
    """R4-m3/R5 m-a: PR среза 1 не закрылся — отказ шага, повтор пробует снова."""
    state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    nodes = cc._bundle_at_pin(state, pin)
    charter = cc.charter_guard.read_charter(nodes["00-charter.md"])
    key, closure, text = cc._measure(
        state, ops, "run-1", charter, nodes, pin, pin, "4.5.0", "h"
    )
    ops.review_exit = 1
    assert cc._publish(state, ops, "run-1", key, text, closure) == 2
    old_pr = cc._entry("run-1", key)["pr"]
    ops.review_exit = 0
    ops.close_pr = lambda slug, pr, comment: False
    assert cc.run("run-1", ops) == 2
    assert not cc._entry("run-1", key).get("slice1_done")
    assert cc._entry("run-1", key)["pr"] is None  # предложение ещё без PR
    assert ops.forge_prs[old_pr]["facts"]["state"] == "OPEN"


def test_new_content_after_rejection_measures_again(tmp_path, monkeypatch):
    """Терминал — только на своём ключе (§7.2a п.5): прогон не запирается."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    ops.forge_prs[_proposal_pr(ops)]["facts"]["state"] = "CLOSED"
    assert cc.run("run-1", ops) == 5
    changed = _commit(target, "pkg/m.py", "def f():\n    return 99\n")
    _git(target, "push", "-q", "origin", "master")
    ops.verify = (0, json.dumps(_response(target, changed, bundle_pin=pin)))
    assert cc.run("run-1", ops, product_sha=changed) == 4
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == 2


CLOSURE_REL = "workstreams/ws/spec/90-acceptance-closure.md"


def _stamp_prs(ops):
    return [c for c in ops.calls if c[0] == "create_pr" and "-stamp" in c[1]]


def test_test_only_honest_run_is_accepted(tmp_path, monkeypatch):
    """§8.4 п.7: честный прогон → PR приёмки, мерж, stamp-PR → accepted."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    e = cc._entry("run-1", _key())
    assert e["acceptance"]["state"] == "accepted"
    _land(target)
    meta, _ = split_frontmatter((target / CLOSURE_REL).read_text())
    assert meta["status"] == "accepted" and meta["accepted_merge"] == e["merge"]["oid"]
    assert len(_stamp_prs(ops)) == 1 and _stamp_prs(ops)[0][2] == "criteria-close"
    stamp = e["stamp"]
    assert any(c == ("merge", stamp["pr"], stamp["head"]) for c in ops.calls)
    assert cc.run("run-1", ops) == 6


def test_human_path_accepted_after_human_merge(tmp_path, monkeypatch):
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    assert cc.run("run-1", ops) == 0
    assert cc._entry("run-1", _key())["acceptance"]["state"] == "accepted"


def test_rerun_after_human_acceptance_publishes_nothing(tmp_path, monkeypatch):
    """Review Focus 5 / ревью пары B1(в): не откат accepted → proposed."""
    _state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 4
    _forge_merge(ops, _proposal_pr(ops), "owner-human")
    assert cc.run("run-1", ops) == 0
    created = sum(1 for c in ops.calls if c[0] == "create_pr")
    _land(target)
    assert cc.run("run-1", ops) == 6
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == created


def test_stamp_pr_review_failure_resumes(tmp_path, monkeypatch):
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    ops.fail_stamp_review = True

    def review(repo, pr):
        ops.calls.append(("review", pr))
        stamp = "-stamp" in ops.forge_prs[pr]["branch"]
        return 1 if stamp and ops.fail_stamp_review else 0

    ops.review = review
    assert cc.run("run-1", ops) == 2
    ops.fail_stamp_review = False
    assert cc.run("run-1", ops) == 0
    assert len(_stamp_prs(ops)) == 1


def test_stamp_merged_by_human_is_adopted(tmp_path, monkeypatch):
    """На polygon нет review-kit: stamp-PR мержит человек — resume принимает."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))

    def review(repo, pr):
        ops.calls.append(("review", pr))
        return 1 if "-stamp" in ops.forge_prs[pr]["branch"] else 0

    ops.review = review
    assert cc.run("run-1", ops) == 2
    _forge_merge(ops, max(ops.forge_prs), "owner-human")
    assert cc.run("run-1", ops) == 0


def _tamper(ops, pr, how):
    clone = Path(ops.forge_prs[pr]["dir"]).parent / "forge"
    f = clone / CLOSURE_REL
    if how == "edit":
        f.write_text(f.read_text() + "правка\n")
        _git(clone, "commit", "-qam", "tamper")
    else:
        _git(clone, "rm", "-q", CLOSURE_REL)
        _git(clone, "commit", "-qm", "delete")
    _git(clone, "push", "-q", "origin", "master")


@pytest.mark.parametrize("old_merged_by_human", [False, True])
def test_slice1_unfinished_closure_migrates_without_remeasure(
    tmp_path, monkeypatch, old_merged_by_human
):
    """Решение владельца: опубликованное, не влитое агентом закрытие среза 1
    (PR открыт или влит человеком — R4-m4) → предложение 2a на своей ветке →
    подпись → штамп, без перемера."""
    state, target, pin = _env_human(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    nodes = cc._bundle_at_pin(state, pin)
    charter = cc.charter_guard.read_charter(nodes["00-charter.md"])
    key, closure, text = cc._measure(
        state, ops, "run-1", charter, nodes, pin, pin, "4.5.0", "h"
    )
    ops.review_exit = 1
    assert cc._publish(state, ops, "run-1", key, text, closure) == 2  # срез 1
    old_pr = cc._entry("run-1", key)["pr"]
    if old_merged_by_human:
        _forge_merge(ops, old_pr, "owner-human")
        _land(target)
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 4
    e = cc._entry("run-1", key)
    assert e["slice1_pr"] == old_pr
    assert (("close_pr", old_pr) in ops.calls) is not old_merged_by_human
    assert e["proposal"]["branch"].endswith("-proposal") and e["pr"] != old_pr
    _forge_merge(ops, e["pr"], "owner-human")
    assert cc.run("run-1", ops) == 0
    assert cc._entry("run-1", key)["acceptance"]["state"] == "accepted"
    assert sum(1 for c in ops.calls if c[0] == "criteria_verify") == 1


@pytest.mark.parametrize("how", ["edit", "delete"])
def test_tampered_or_deleted_stamp_reissues_stamp_pr(tmp_path, monkeypatch, how):
    """Review Focus 4 / ревью пары m4: иное содержимое или файла нет → новый stamp-PR."""
    _state, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    real_merge = ops.merge
    done = {"x": False}

    def merge(repo, pr, sha, base=None):
        code = real_merge(repo, pr, sha, base)
        if code == 0 and "-stamp" in ops.forge_prs[pr]["branch"] and not done["x"]:
            done["x"] = True
            _tamper(ops, pr, how)
        return code

    ops.merge = merge
    assert cc.run("run-1", ops) == 2
    assert cc.run("run-1", ops) == 0
    stamps = [c[1] for c in _stamp_prs(ops)]
    assert len(stamps) == 2 and stamps[1].endswith("-stamp-2")


# ---- #481: долг среза 1 ------------------------------------------------------


def test_schema1_without_finalize_identity_is_not_applicable(
    tmp_path, monkeypatch, capsys
):
    """M-1: прогон без идентичности результата (finalize) на charter'е схемы 1
    — `not-applicable: schema-1` с выходом 0, публиковать нечего (гейт схему 1
    не потребляет), а не отказ «нет идентичности результата»."""
    _env(tmp_path, monkeypatch, charter=CH1)
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: None)
    ops = _ops()
    assert cc.run("run-1", ops) == 0
    out = capsys.readouterr().out
    assert "not-applicable: schema-1" in out and "публиковать нечего" in out
    assert not any(c[0] in ("create_pr", "review", "merge") for c in ops.calls)


@pytest.mark.parametrize("charter", [CH2, "---\nschema: [2\n---\n"])
def test_schema2_without_finalize_identity_still_refused(
    tmp_path, monkeypatch, capsys, charter
):
    """M-1: схема 2 (и битый frontmatter — не «схема 1») без пина — отказ."""
    _env(tmp_path, monkeypatch, charter=charter)
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: None)
    assert cc.run("run-1", _ops()) == 2
    assert "нет идентичности результата" in capsys.readouterr().out


def test_unfetched_finalize_pin_is_fetched_before_reading_bundle(tmp_path, monkeypatch):
    """M-9: пин finalize, которого ещё нет в локальных объектах, — fetch до
    чтения бандла по пину, а не отказ «недоступен»."""
    _state, target, pin = _env(tmp_path, monkeypatch, charter=CH1)
    other = tmp_path / "other"
    origin = _git(target, "remote", "get-url", "origin")
    subprocess.run(
        ["git", "clone", "-q", origin, str(other)], check=True, capture_output=True
    )
    _commit(other, "workstreams/ws/spec/10-requirements.md", REQ + "\n")
    _git(other, "push", "-q", "origin", "master")
    finalize = _git(other, "rev-parse", "HEAD")
    monkeypatch.setattr(cc.runner, "_verified_result_sha", lambda s: finalize)
    ops = _ops()
    assert cc.run("run-1", ops, product_sha=pin) == 0
    assert ops.merged


def _forget_merged(run_id="run-1"):
    """Сбой между мержем и записью: `merged` в состоянии не записан."""
    data = cc._load(run_id)
    for e in data["measured"].values():
        e.pop("merged", None)
    cc._save(run_id, data)


def test_merged_but_unrecorded_closure_pr_is_recorded(tmp_path, monkeypatch, capsys):
    """M-7: PR закрытия влит, запись `merged` потеряна — повтор находит влитый
    PR (любого состояния) и записывает факт, а не упирается в «нет коммитов»."""
    _state, _target, pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    assert cc.run("run-1", ops) == 0
    _forget_merged()
    prs = sum(1 for c in ops.calls if c[0] == "create_pr")
    # тот же ключ: product_sha прежний (верхушку сдвинул сам мерж закрытия)
    assert cc.run("run-1", ops, product_sha=pin) == 0
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == prs
    (entry,) = cc._load("run-1")["measured"].values()
    assert entry["merged"] is True
    assert "уже влит" in capsys.readouterr().out


def test_closure_already_on_base_is_recorded_without_branch_or_pr(
    tmp_path, monkeypatch, capsys
):
    """M-7: ветка ключа удалена после мержа, PR не находится — тот же текст
    уже на origin/<base>: публикация завершена, а не `git commit` впустую."""
    _state, target, pin = _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    assert cc.run("run-1", ops) == 0
    _forget_merged()
    ops.existing_prs.clear()
    (entry,) = cc._load("run-1")["measured"].values()
    _git(target, "push", "-q", "origin", "--delete", entry["branch"])
    prs = sum(1 for c in ops.calls if c[0] == "create_pr")
    assert cc.run("run-1", ops, product_sha=pin) == 0
    assert sum(1 for c in ops.calls if c[0] == "create_pr") == prs
    (entry,) = cc._load("run-1")["measured"].values()
    assert entry["merged"] is True
    assert "уже в origin/master" in capsys.readouterr().out


def test_open_closure_pr_wins_over_closed_one_on_the_same_branch():
    """Ревью #556: у ветки ключа пара CLOSED+OPEN (человек закрыл PR,
    повтор открыл новый). Выбор не зависит от порядка листинга `--state
    all`: берётся открытый — иначе второй `create_pr` и вечный отказ шага."""

    class _Forge:
        def find_pr(self, repo_slug, branch, *, any_state=False):
            return 99 if any_state else 100  # листинг all — закрытый первым

    assert cc._find_closure_pr(_Forge(), "o/r", "b") == (100, "OPEN")


def test_reuse_of_measured_result_is_named(tmp_path, monkeypatch, capsys):
    """M-8: повтор публикации ранее измеренного результата назван явно —
    оператор отличает его от нового измерения."""
    _, target, pin = _env(tmp_path, monkeypatch)
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
    first = capsys.readouterr().out
    assert "повтор публикации" not in first and "новый результат" in first
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert "повтор публикации ранее измеренного результата" in capsys.readouterr().out


def test_reuse_of_not_applicable_result_is_named(tmp_path, monkeypatch, capsys):
    """M-8: то же на пути not-applicable."""
    _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    ops.review_exit = 1
    assert cc.run("run-1", ops) == 2
    assert "повтор публикации" not in capsys.readouterr().out
    ops.review_exit = 0
    assert cc.run("run-1", ops) == 0
    assert "повтор публикации ранее измеренного результата" in capsys.readouterr().out


def test_forge_runtime_error_is_step_refusal_not_traceback(
    tmp_path, monkeypatch, capsys
):
    """M-14: сбой запроса к фордже (RuntimeError из find_pr) — отказ шага (2)
    с причиной, а не трейсбек."""
    _env(tmp_path, monkeypatch, charter=CH1)
    ops = _ops()
    ops.find_pr_error = "find_pr: gh pr list rc=1: rate limited"
    assert cc.run("run-1", ops) == 2
    assert "rate limited" in capsys.readouterr().out


def test_off_protocol_verify_exit_names_diagnostic(tmp_path, monkeypatch, capsys):
    """M-14: код выхода spec-runner вне 0/2/3 — отказ шага с его диагностикой
    (RealOps отдаёт stderr), а не голый «код вне 0/2/3»."""
    _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    assert cc.run("run-1", _ops((1, "Traceback: boom"))) == 2
    out = capsys.readouterr().out
    assert "вне 0/2/3" in out and "Traceback: boom" in out
