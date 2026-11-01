"""Вызовы gate-check несут --act-mode (approval-facts v3 §7.2, О14)."""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pytest
import yaml

from governance import ops as ops_mod
from governance.ops import Ops, RealOps

ROOT = Path(__file__).resolve().parent.parent


class _Done:
    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = "ok\n"
        self.stderr = ""


@pytest.fixture()
def argv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(cmd, **_kw):  # noqa: ANN001, ANN003 — подмена subprocess.run
        seen.append(list(cmd))
        return _Done()

    monkeypatch.setattr(ops_mod.subprocess, "run", run)
    return seen


@pytest.mark.parametrize("name", ["gate_check_s8", "gate_check_candidate"])
def test_act_mode_has_no_default(name: str) -> None:
    for owner in (Ops, RealOps):
        param = inspect.signature(getattr(owner, name)).parameters["act_mode"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


def test_s8_passes_act_mode(argv: list[list[str]]) -> None:
    RealOps().gate_check_s8("/t", "spec", "p.yaml", act_mode="legacy")
    assert argv[-1][-2:] == ["--act-mode", "legacy"]
    assert "--emit-verdicts" in argv[-1]


def test_candidate_passes_mode_and_provider(argv: list[list[str]]) -> None:
    RealOps().gate_check_candidate(
        "/t", "spec", "p.yaml", act_mode="review", node_status="/r/ns.json"
    )
    cmd = argv[-1]
    assert cmd[cmd.index("--act-mode") + 1] == "review"
    assert cmd[cmd.index("--node-status") + 1] == "/r/ns.json"
    assert "--candidate" in cmd


def test_snapshot_digest_needs_no_mode(argv: list[list[str]]) -> None:
    RealOps().gate_snapshot_digest("/t", "spec", candidate=True)
    assert argv[-1][1:] == ["--print-snapshot-digest", "--candidate", "spec"]
    assert "--act-mode" not in argv[-1]


def test_every_collect_bundle_call_names_act_mode() -> None:
    """Вызов steward-API без act_mode — TypeError на пине v0.2.0; ловим статически."""
    offenders: list[str] = []
    for path in [*ROOT.glob("governance/**/*.py"), *ROOT.glob("tests/**/*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", ""))
                == "collect_bundle"
                and not any(k.arg == "act_mode" for k in node.keywords)
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


GATE_METHODS = ("gate_check_s8", "gate_check_candidate")


def _gate_offenders(root: Path) -> list[str]:
    """Все места вызова и переопределения методов `Ops`, сменивших сигнатуру."""
    offenders: list[str] = []
    for path in [*root.glob("governance/**/*.py"), *root.glob("tests/**/*.py")]:
        rel = path.relative_to(root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in GATE_METHODS:
                kwonly = {a.arg for a in node.args.kwonlyargs}
                if (
                    not {"act_mode", "node_status"} <= kwonly
                    or node.args.kwarg is not None
                ):
                    offenders.append(f"{rel}:{node.lineno} def {node.name}")
                for inner in ast.walk(node):  # пересылка обязана нести оба аргумента
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == node.name
                        and not {"act_mode", "node_status"}
                        <= {k.arg for k in inner.keywords}
                    ):
                        offenders.append(f"{rel}:{inner.lineno} пересылка {node.name}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in GATE_METHODS
                and "act_mode" not in {k.arg for k in node.keywords}
            ):
                offenders.append(f"{rel}:{node.lineno} вызов {node.func.attr}")
    return sorted(set(offenders))


def test_every_gate_check_site_names_act_mode() -> None:
    """B1-7: все места вызова, переопределения и пересылки `gate_check_*` —
    статически, чтобы пропущенный стаб не всплыл `TypeError` в чужом тесте."""
    assert _gate_offenders(ROOT) == []


def test_gate_catalog_is_v3_with_act_gates() -> None:
    data = yaml.safe_load((ROOT / "profiles/gate-catalog.yaml").read_text("utf-8"))
    assert data["version"] == 3
    acts = {"GC-ACT-MODE-CONFLICT", "GC-ACT-PROVIDER-MISSING", "GC-ACT-NOT-APPROVED"}
    assert acts <= set(data["gates"])
    assert all(data["gates"][g]["status"] == "active" for g in acts)


def test_cli_refuses_without_act_mode(tmp_path: Path) -> None:
    """Живой CLI пиненого steward: без --act-mode — код 2 (О14)."""
    pytest.importorskip("steward")
    exe = ROOT / ".venv" / "bin" / "gate-check"
    if not exe.exists():
        pytest.skip("gate-check не установлен в .venv")
    done = subprocess.run(
        [str(exe), str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 2
    assert "--act-mode is required" in done.stdout + done.stderr
