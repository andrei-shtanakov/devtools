"""`bundle_dag`: уровни DAG, префикс состава и волновой режим проверки состава.

Спека sequential-node-approval S1/S4а: волны 1-based соответствуют уровням
DAG 0-based; в волновом режиме base обязан быть ПРЕФИКСОМ DAG по уровням
(пустой при отсутствии каталога), «дыра» в уровнях — отказ.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import bundle_dag

DAG = bundle_dag.BUNDLE_DAG


def test_levels_match_dag_topology() -> None:
    assert bundle_dag.levels(DAG) == {
        "charter": 0, "requirements": 1, "behaviour-spec": 2,
        "design": 3, "acceptance": 3, "decomposition": 4,
    }
    assert bundle_dag.wave_count(DAG) == 5


def test_dag_upto_is_a_level_prefix() -> None:
    assert [f for f, _ in bundle_dag.dag_upto(DAG, 1)] == [
        "00-charter.md", "10-requirements.md",
    ]
    assert bundle_dag.dag_upto(DAG, -1) == ()
    assert bundle_dag.dag_upto(DAG, 4) == DAG


def _bundle(tmp_path: Path, names: list[str]) -> Path:
    b = tmp_path / "spec"
    b.mkdir(exist_ok=True)
    for n in names:
        (b / n).write_text("---\nnode: x\n---\n", encoding="utf-8")
    return b


def test_waves_mode_accepts_level_prefix_and_missing_dir(tmp_path: Path) -> None:
    check = bundle_dag.check_bundle_composition
    assert check(str(tmp_path), "spec", DAG, mode="waves") == -1
    _bundle(tmp_path, ["00-charter.md", "10-requirements.md"])
    assert check(str(tmp_path), "spec", DAG, mode="waves") == 1


def test_waves_mode_refuses_a_hole_in_levels(tmp_path: Path) -> None:
    _bundle(tmp_path, ["00-charter.md", "15-behaviour-spec.md"])
    with pytest.raises(RuntimeError, match="префикс"):
        bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG, mode="waves")


def test_full_mode_is_unchanged(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="каталога бандла"):
        bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG)
    _bundle(tmp_path, [f for f, _ in DAG])
    assert bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG) == 4
