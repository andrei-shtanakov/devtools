"""Режим gate-check S4/S8 и провайдер статуса узла (О4, О14, §12 п. 2–3; Р-10; b1)."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_status, act_trust  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _State:
    repo_slug = "owner/p"
    ws_id = "ws-7"
    target_dir = "/t"
    bundle_dir = "spec"
    run_id = "r"
    base_ref = "master"


def _deps(binding) -> act_status.Deps:
    # журнал отвечает отказом с собственной причиной: отключённая проверка выше него
    # даёт ДРУГУЮ причину, а не исключение (b4, мутационная изоляция)
    return act_status.Deps(
        readers=None,
        binding=binding,
        journal=lambda readers, b: Unavailable("журнал стенда"),
        creds=(),
        gh=None,
        now=None,
        sleep=None,
        settings=None,
    )


def _live(mode: str) -> act_status.LivePolicy:
    return act_status.LivePolicy(mode, "trust", "j", None, None)


def _real_probe(monkeypatch) -> None:
    """Снять подмену `tests/conftest.py`: проверяется настоящий `live_policy`."""
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: act_status.live_policy(state, deps),
    )


def test_no_binding_is_unavailable_not_legacy(tmp_path: Path, monkeypatch) -> None:
    """B1-5: потеря привязки — не «до миграции»: стоп, файл провайдера не пишется."""
    _real_probe(monkeypatch)
    got = act_status.gate_run(
        _State(),
        object(),
        (),
        candidate=True,
        out=tmp_path / "ns.json",
        deps=_deps(None),
    )
    assert isinstance(got, Unavailable) and not (tmp_path / "ns.json").exists()
    assert got.reason.startswith("нет привязки корня доверия")
    assert (
        "/etc/df/trust.toml" in got.reason
        and "режим продукта не установлен" in got.reason
    )


def test_unreadable_binding_stops(tmp_path: Path, monkeypatch) -> None:
    _real_probe(monkeypatch)
    got = act_status.gate_run(
        _State(),
        object(),
        (),
        candidate=True,
        out=tmp_path / "ns.json",
        deps=_deps(Unavailable("perm")),
    )
    assert isinstance(got, Unavailable)


@pytest.mark.parametrize("mode", [act_trust.PRE_MIGRATION, "merge"])
def test_pre_migration_and_merge_are_legacy(
    tmp_path: Path, monkeypatch, mode: str
) -> None:
    monkeypatch.setattr(act_status, "mode_probe", lambda state, deps=None: _live(mode))

    class _Ops:  # если проверка режима отключена — дайджест «не прочитан», не исключение
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            return 1, "no digest"

    got = act_status.gate_run(
        _State(),
        _Ops(),
        (),
        candidate=False,
        out=tmp_path / "ns.json",
        deps=_deps(object()),
    )
    assert got == ("legacy", None)


def test_snapshot_digest_failure_stops(tmp_path: Path, monkeypatch) -> None:
    """b4: `gate-check --print-snapshot-digest` упал — стоп шага, провайдер не пишется."""
    monkeypatch.setattr(
        act_status, "mode_probe", lambda state, deps=None: _live("review")
    )

    class _Ops:
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            return 2, "boom"

    got = act_status.gate_run(
        _State(),
        _Ops(),
        (),
        candidate=True,
        out=tmp_path / "ns.json",
        deps=_deps(object()),
    )
    assert isinstance(got, Unavailable) and not (tmp_path / "ns.json").exists()
    assert got.reason == "gate-check --print-snapshot-digest: код 2: boom"


def test_mode_unavailable_stops(tmp_path: Path, monkeypatch) -> None:
    """Удалённая запись / откат review → merge — стоп шага, не legacy (B1-5)."""
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda state, deps=None: Unavailable("обратное переключение"),
    )
    got = act_status.gate_run(
        _State(),
        object(),
        (),
        candidate=True,
        out=tmp_path / "ns.json",
        deps=_deps(object()),
    )
    assert isinstance(got, Unavailable)


def test_review_mode_writes_provider_bound_to_digest(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        act_status, "mode_probe", lambda state, deps=None: _live("review")
    )
    monkeypatch.setattr(
        act_status,
        "_gate_statuses",
        lambda state, ops, dag, deps, live: {"requirements": "approved"},
    )

    class _Ops:
        def gate_snapshot_digest(self, target_dir, bundle_dir, *, candidate):
            assert candidate is True
            return 0, "sha256:" + "a" * 64

    out = tmp_path / "ns.json"
    got = act_status.gate_run(
        _State(), _Ops(), (), candidate=True, out=out, deps=_deps(object())
    )
    assert got == ("review", str(out))
    assert json.loads(out.read_text()) == {
        "snapshot_digest": "sha256:" + "a" * 64,
        "nodes": {"requirements": "approved"},
    }


def test_cli_accepts_provider_file_and_reports_not_approved(tmp_path: Path) -> None:
    """Живой CLI пиненого steward: файл провайдера нашей формы принят; неодобренный
    узел — GC-ACT-NOT-APPROVED, без провайдера — GC-ACT-PROVIDER-MISSING."""
    from tests.governance_fixtures.bundles import make_bundle, make_profile

    exe = ROOT / ".venv" / "bin" / "gate-check"
    if not exe.exists():
        pytest.skip("gate-check не установлен в .venv")
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    digest = subprocess.run(
        [str(exe), "--print-snapshot-digest", "--candidate", str(bundle)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    ns = tmp_path / "ns.json"
    ns.write_text(
        json.dumps(
            {
                "snapshot_digest": digest,
                "nodes": {"requirements": "not_approved:нет конверта"},
            }
        )
    )
    run = [
        str(exe),
        "--candidate",
        str(bundle),
        "--profile",
        str(profile),
        "--act-mode",
        "review",
    ]
    with_provider = subprocess.run(
        [*run, "--node-status", str(ns)], capture_output=True, text=True, check=False
    )
    assert with_provider.returncode == 1
    assert "GC-ACT-NOT-APPROVED" in with_provider.stdout + with_provider.stderr
    without = subprocess.run(run, capture_output=True, text=True, check=False)
    assert "GC-ACT-PROVIDER-MISSING" in without.stdout + without.stderr


NEW_OPS = {
    "first_parent_changes",
    "pr_body_fact",
    "gate_snapshot_digest",
    "snapshot_open",
    "snapshot_reader",
    "snapshot_close",
}
HISTORY = {"statuses_at_base", "load_history", "Decider"}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _called(node: ast.AST) -> str:
    func = node.func if isinstance(node, ast.Call) else None
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _calls_here(stmt: ast.stmt) -> list[ast.Call]:
    """Вызовы в выражениях самого оператора — без вложенных функций и лямбд."""
    found: list[ast.Call] = []
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            found.append(node)
        stack.extend(
            c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPES)
        )
    return found


def _probe_line(fn: ast.FunctionDef) -> int | None:
    """Строка БЕЗУСЛОВНОГО `mode_probe`: оператор верхнего уровня тела функции
    (присваивание или выражение), а не ветка `if`/цикла/`try`/лямбды."""
    for stmt in fn.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)) and any(
            _called(c) == "mode_probe" for c in _calls_here(stmt)
        ):
            return stmt.lineno
    return None


def _surface_offenders(root: Path) -> list[str]:
    """Новые методы `Ops` части B — только из `governance/act_*`; чтение истории вне
    `act_*` — только ПОСЛЕ безусловного `mode_probe` той же функции."""
    offenders: list[str] = []
    for path in root.glob("governance/**/*.py"):
        if path.name.startswith("act_"):
            continue
        rel = path.relative_to(root)
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, ast.FunctionDef):
                continue
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
            new = sorted({_called(c) for c in calls} & NEW_OPS)
            if new:
                offenders.append(f"{rel}:{fn.lineno} {fn.name}: {new}")
            history = [c.lineno for c in calls if _called(c) in HISTORY]
            if not history:
                continue
            probe = _probe_line(fn)
            if probe is None or min(history) <= probe:
                offenders.append(
                    f"{rel}:{fn.lineno} {fn.name}: история не после "
                    "безусловного mode_probe"
                )
    return offenders


def test_new_ops_surface_reached_only_behind_mode_probe() -> None:
    """B1-8 (класс B1-7): стабы `Ops` старых тестов не знают новых методов — значит,
    их не должен звать ни один путь, который тесты вне `act_*` проходят с
    `tests/conftest.py` (`mode_probe` → `PRE_MIGRATION`). Новый вызов вне `act_*`
    или чтение истории в обход `mode_probe` падает здесь, а не `AttributeError` в
    чужом тесте."""
    assert _surface_offenders(ROOT) == []
