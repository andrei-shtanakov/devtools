"""I-3 интеграционного ревью части B: сбой вызова `RealOps` — факт не установлен.

Класс (второй экземпляр после C1 Task 16): потребитель ловит у́же, чем бросает
производитель. `RealOps.pr_facts` идёт `check=True` и поднимает
`CalledProcessError`; любой `subprocess.run` поднимает `OSError`, если бинаря нет.
Механизм — одно множество `act_time.OPS_FAILURES`, и статическая проверка ниже
требует его (или `Exception`) у каждого `try` вокруг вызова `ops.*` в модулях акта.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("steward")

from governance import act_barrier, act_status, approval_facts  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance.act_time import OPS_FAILURES, Unavailable  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SLUG = "o/p"
WS = "ws-1"
CPE = subprocess.CalledProcessError(1, ["gh", "pr", "view"], stderr="network")
FAILURES = [CPE, OSError("gh: not found"), subprocess.TimeoutExpired("gh", 5)]


def _raise(exc: BaseException):
    def call(*_a, **_k):
        raise exc

    return call


def _outcome(call):
    """Результат вызова либо поднятое им исключение — двойник утверждает о нём,
    поэтому мутант (узкий `except`) краснит утверждение, а не прогон."""
    try:
        return call()
    except BaseException as exc:  # noqa: BLE001 — предмет утверждения
        return exc


def test_ops_failures_cover_realops_producers() -> None:
    for kind in (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        assert kind in OPS_FAILURES


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_pr_head_failure_is_unavailable(exc: BaseException) -> None:
    ops = SimpleNamespace(pr_facts=_raise(exc))
    state = SimpleNamespace(repo_slug=SLUG, target_dir="/nonexistent")
    got = _outcome(lambda: act_status._pr_head(ops, state, 7))  # type: ignore[arg-type]
    assert isinstance(got, Unavailable) and "голова PR #7" in got.reason


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_pr_for_merge_failure_is_unavailable(exc: BaseException) -> None:
    ops = SimpleNamespace(prs_containing_commit=_raise(exc))
    got = _outcome(lambda: act_status.pr_for_merge(ops, SLUG, "m" * 40))  # type: ignore[arg-type]
    assert isinstance(got, Unavailable) and "PR merge-коммита" in got.reason


def _probe(ops: object) -> act_status.LegacyProbe:
    state = SimpleNamespace(repo_slug=SLUG, ws_id=WS, target_dir="/x", ops={})
    return act_status.LegacyProbe(state, ops, "master", lambda *_: None, ())  # type: ignore[arg-type]


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_legacy_merged_candidate_pr_facts_failure_unavailable(
    exc: BaseException,
) -> None:
    ops = SimpleNamespace(find_pr=lambda *_a, **_k: 7, pr_facts=_raise(exc))
    probe = _probe(ops)
    got = _outcome(
        lambda: probe._merged_candidate("cand", "human", "2026-11-01T00:00:00Z")
    )
    assert isinstance(got, str) and got.startswith("unavailable:candidate cand")


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_legacy_finalize_branch_failure_unavailable(exc: BaseException) -> None:
    ops = SimpleNamespace(prs_containing_commit=_raise(exc))
    probe = _probe(ops)
    got = _outcome(lambda: probe._finalize_branch("c" * 40))
    assert isinstance(got, str) and got.startswith("unavailable:PR коммита подписи")


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_read_pr_failure_is_unavailable(exc: BaseException) -> None:
    ops = SimpleNamespace(pr_facts=_raise(exc))
    fact = _outcome(lambda: approval_facts.read_pr(ops, SLUG, 7))  # type: ignore[arg-type]
    assert fact.value is None and "запрос не удался" in fact.detail


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_finalize_identity_find_pr_failure_unresolved(exc: BaseException) -> None:
    ops = SimpleNamespace(find_pr=_raise(exc))
    state = SimpleNamespace(repo_slug=SLUG)
    op = {"finalize_branch": "approve/x-final"}
    err = _outcome(lambda: an._ensure_finalize_identity(state, ops, "k", op))  # type: ignore[arg-type]
    assert isinstance(err, RuntimeError) and "факт не установлен" in str(err)


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_await_checks_pr_facts_failure_unresolved(exc: BaseException) -> None:
    ops = SimpleNamespace(pr_facts=_raise(exc))
    state = SimpleNamespace(repo_slug=SLUG)
    err = _outcome(lambda: an._await_required_checks(state, ops, 7, "h" * 40))  # type: ignore[arg-type]
    assert isinstance(err, RuntimeError) and "факт не установлен" in str(err)


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
def test_commit_paths_failure_unresolved(exc: BaseException) -> None:
    ops = SimpleNamespace(commit_paths=_raise(exc))
    err = _outcome(lambda: an._commit_paths(ops, "/x", ["a"], "msg"))  # type: ignore[arg-type]
    assert isinstance(err, RuntimeError) and "факт не установлен" in str(err)


def test_barrier_pr_facts_failure_is_a_gap() -> None:
    ops = SimpleNamespace(
        prs_by_head_prefix=lambda *_: [
            {"number": 7, "head": {"ref": "spec/ws-1-approve-1-0-1"}, "state": "OPEN"}
        ],
        pr_facts=_raise(CPE),
    )
    report = act_barrier.live_review_requests({SLUG: ops})
    assert not report.complete and report.gaps


# --- механизм: каждый try вокруг ops.* ловит OPS_FAILURES ------------------------

MODULES = sorted(
    [*ROOT.glob("governance/act_*.py"), ROOT / "governance/approve_node.py"]
)
# вызовы, которые сами не бросают из-за подпроцесса: чистые аксессоры леджера
_NOT_OPS = {"get", "items", "keys", "values", "setdefault", "pop"}


def _ops_calls(node: ast.AST) -> list[ast.Call]:
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
            continue
        owner = sub.func.value
        name = (
            owner.id
            if isinstance(owner, ast.Name)
            else owner.attr
            if isinstance(owner, ast.Attribute)
            else None
        )
        if name in {"ops", "self.ops"} or (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "ops"
        ):
            if sub.func.attr not in _NOT_OPS:
                found.append(sub)
    return found


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    kind = handler.type
    items = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    names = set()
    for item in items:
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
        elif isinstance(item, ast.Starred) and isinstance(item.value, ast.Name):
            names.add(item.value.id)
    return names


def _try_sites() -> list[tuple[str, int, set[str]]]:
    sites = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not node.handlers:
                continue  # try/finally без обработчиков ничего не глотает
            body = ast.Module(body=node.body, type_ignores=[])
            if not _ops_calls(body):
                continue
            names: set[str] = set()
            for handler in node.handlers:
                names |= _handler_names(handler)
            sites.append((path.name, node.lineno, names))
    return sites


def test_every_try_around_ops_catches_ops_failures() -> None:
    sites = _try_sites()
    assert sites  # сам прибор что-то видит
    narrow = [
        (f, line, sorted(n))
        for f, line, n in sites
        if not n & {"OPS_FAILURES", "Exception", "BaseException"}
    ]
    assert narrow == []


def _direct_pr_facts() -> list[str]:
    """`ops.pr_facts(` вне `try`, ловящего OPS_FAILURES: в act_status, act_node,
    approve_node — только через `approval_facts.read_pr`/`an._pr_facts`."""
    bad = []
    for name in ("act_status.py", "act_node.py", "approve_node.py", "act_premerge.py"):
        tree = ast.parse((ROOT / "governance" / name).read_text(encoding="utf-8"))
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(
                _handler_names(h) & {"OPS_FAILURES", "Exception"} for h in node.handlers
            ):
                for stmt in node.body:
                    guarded |= {id(c) for c in ast.walk(stmt)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pr_facts"
                and id(node) not in guarded
            ):
                bad.append(f"{name}:{node.lineno}")
    return bad


def test_no_unguarded_pr_facts_in_act_consumers() -> None:
    assert _direct_pr_facts() == []


def test_mechanism_detects_a_narrow_handler(tmp_path: Path) -> None:
    """Отрицательный двойник прибора: узкий обработчик виден."""
    src = "def f(ops):\n    try:\n        ops.pr_facts(1)\n    except RuntimeError:\n        pass\n"
    tree = ast.parse(src)
    tr = next(n for n in ast.walk(tree) if isinstance(n, ast.Try))
    assert _ops_calls(ast.Module(body=tr.body, type_ignores=[]))
    assert _handler_names(tr.handlers[0]) == {"RuntimeError"}
