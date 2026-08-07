"""plan-check fixture mode: вердикт чекера на синтетическом workspace.

Доказывает ОБЕ стороны детектора (урок fail-closed: зелёный гейт обязан
уметь краснеть): чистая фикстура даёт exit 0, фикстура с каноническим
блокером на уже закрытую работу — exit 1 с PF-BLOCKER-STALE.

Это НЕ проверка состояния флота: она доказывает работоспособность
инструмента и пина plan-fields. Скрипт запускается как subprocess под тем
же интерпретатором, что и pytest, — pinned-окружение uv с plan-fields
резолвится одинаково. Фикстура строится во временном каталоге, потому что
каталог `.git` (маркер чекаута для discovery) незакоммитим в git.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-plan-fields.py"


@pytest.fixture(scope="module")
def plan_check():
    spec = importlib.util.spec_from_file_location("plan_check_reporting", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plan checker from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plan_check_reporting"] = module
    spec.loader.exec_module(module)
    return module

MANIFEST = (
    'schema_version = "0.3.0"\n'
    '[cores.alpha]\ngit_dir = "alpha"\n'
    '[cores.beta]\ngit_dir = "beta"\n'
)
DEPENDENT = (
    "- [ ] dependent @owner:github:x "
    "@blocked_by:todo://alpha/base-item @id:dependent-item\n"
)


def _make_workspace(tmp: Path, alpha_todo: str) -> Path:
    ws = tmp / "ws"
    for repo in ("alpha", "beta"):
        (ws / repo / ".git").mkdir(parents=True)
    (ws / "workspace-manifest.toml").write_text(MANIFEST)
    (ws / "alpha" / "TODO.md").write_text(alpha_todo)
    (ws / "beta" / "TODO.md").write_text(DEPENDENT)
    return ws


def _run(ws: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(ws),
            "--manifest",
            str(ws / "workspace-manifest.toml"),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_fixture_clean_workspace_exits_0(tmp_path):
    ws = _make_workspace(
        tmp_path, "- [ ] base item @owner:github:x @id:base-item\n"
    )
    code, out = _run(ws)
    assert code == 0, out
    # с запятой и пробелом: голое "0 error(s)" матчилось бы и в "10 error(s)"
    assert ", 0 error(s)," in out


def test_fixture_stale_blocker_exits_1_with_pf_blocker_stale(tmp_path):
    ws = _make_workspace(
        tmp_path, "- [x] base item @owner:github:x @id:base-item\n"
    )
    code, out = _run(ws)
    assert code == 1, out
    assert "PF-BLOCKER-STALE" in out


def test_reporting_splits_ownership_movement_and_full_matrix(tmp_path):
    ws = tmp_path / "ws"
    for repo in ("alpha", "beta"):
        (ws / repo / ".git").mkdir(parents=True)
    (ws / "workspace-manifest.toml").write_text(MANIFEST)
    (ws / "alpha" / "TODO.md").write_text(
        "- [x] delivered @owner:github:x @id:delivered\n"
        "- [ ] target @owner:github:x @id:target\n"
        "- [ ] trigger @owner:github:x @trigger:\"release\" @id:trigger\n"
        "- [ ] repo task @owner:repo:alpha @id:repo-task\n"
        "- [ ] tbd wait @owner:TBD @blocked_by:todo://alpha/target @id:tbd\n"
        "- [ ] no owner @blocked_by:todo://alpha/delivered @id:no-owner\n"
        "- [ ] legacy owner @owner:tech-lead "
        "@blocked_by:todo://alpha/ghost @id:legacy\n"
        "- [ ] unknown repo @owner:repo:ghost @id:unknown-repo\n"
    )
    (ws / "beta" / "TODO.md").write_text("")

    code, out = _run(ws)
    assert code == 1, out  # stale-condition remains the canonical hard failure
    assert (
        "ownership: human-owned=2, repo-owned=1, TBD=1, missing=1, "
        "invalid-owner=1, unknown-repo-owner=1"
    ) in out
    assert (
        "movement: actionable=3, waiting-by-trigger=1, waiting-by-blocker=1, "
        "stale-condition=1, malformed-condition=1"
    ) in out
    matrix_lines = [line for line in out.splitlines() if "ownership×movement" in line]
    assert len(matrix_lines) == 6
    assert "missing: actionable=0" in out
    assert "stale-condition=1" in next(line for line in matrix_lines if "missing:" in line)


def test_reporting_reuses_the_graph_analysis(plan_check, monkeypatch):
    calls = {"parse_fleet": 0, "legacy": 0}
    real_parse_fleet = plan_check.parse_fleet
    real_legacy = plan_check.check_legacy_fleet

    def counted_parse_fleet(*args, **kwargs):
        calls["parse_fleet"] += 1
        return real_parse_fleet(*args, **kwargs)

    def counted_legacy(*args, **kwargs):
        calls["legacy"] += 1
        return real_legacy(*args, **kwargs)

    monkeypatch.setattr(plan_check, "parse_fleet", counted_parse_fleet)
    monkeypatch.setattr(plan_check, "check_legacy_fleet", counted_legacy)
    inputs = [
        plan_check.RepoInput(
            "demo", "- [ ] work @owner:github:x @trigger:\"go\" @id:work\n"
        )
    ]
    index = plan_check.ManifestIndex(frozenset({"demo"}), {})
    report = plan_check.Report()

    condition_codes = plan_check.resolve_graph(inputs, index, report)
    plan_check.check_reporting(inputs, index, condition_codes, report)

    assert calls == {"parse_fleet": 1, "legacy": 1}
