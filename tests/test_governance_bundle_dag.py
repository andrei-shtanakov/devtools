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


# --- BEH-07/BEH-17 (devtools#173): правило имён узлов — один источник ----


def test_bundle_composition_ignores_stray_files(tmp_path: Path) -> None:
    """`bundle_composition` — единственный обход по известным именам:
    посторонний `.md` не входит в результат, а каталог с именем узла не
    считается узлом (обход строгий — запись обязана быть файлом)."""
    bundle = _bundle(tmp_path, [f for f, _ in DAG])
    (bundle / "README.md").write_text("x", encoding="utf-8")
    (bundle / "40-notes.md").write_text("x", encoding="utf-8")
    (bundle / "30-decomposition.md").unlink()
    (bundle / "30-decomposition.md").mkdir()
    assert bundle_dag.bundle_composition(bundle) == {
        f for f, _ in DAG if f != "30-decomposition.md"
    }


def test_new_node_name_moves_both_composition_boundaries(
    tmp_path: Path, monkeypatch
) -> None:
    """BEH-07/FR-04: имя узла, добавленное в единственный источник
    (`BUNDLE_DAG`), признаётся сразу на обеих наблюдаемых границах одного
    прогона — проверкой состава (`check_bundle_composition`) и выводом
    состава для §I8 (`_previous_dag`), — причём вторую функцию для этого
    никто не правил. Проверено на двух каталогах, с посторонним `README.md`
    и без него: третий критерий приёмки FR-04 — унификация правила не
    заводит зависимости от постороннего файла там, где её не было."""
    from governance import task_bridge as tb

    extra_dag = DAG + (("35-extra.md", ("decomposition",)),)
    monkeypatch.setattr(bundle_dag, "BUNDLE_DAG", extra_dag)

    class _FakeState:
        ws_id = "WS-x"

    class _FakeOps:
        def show_file(self, target_dir: str, ref: str, path: str) -> str:
            return "---\nspec_stage: tasks\ntraces_to:\n- extra\n---\n\nb\n"

    for with_readme in (False, True):
        bundle = tmp_path / f"spec-{with_readme}"
        bundle.mkdir()
        for fname, _ in extra_dag:
            (bundle / fname).write_text("x", encoding="utf-8")
        if with_readme:
            (bundle / "README.md").write_text("x", encoding="utf-8")

        # Граница 1: проверка состава признаёт узел, не отказывает «лишним».
        assert bundle_dag.check_bundle_composition(
            str(tmp_path), bundle.name, extra_dag,
        ) == bundle_dag.wave_count(extra_dag) - 1

        # Граница 2: вывод состава для §I8 признаёт тот же узел.
        dag, source, reason = tb._previous_dag(
            _FakeState(), _FakeOps(), {}, str(tmp_path), bundle.name,
            "base-sha",
        )
        assert (dag, source, reason) == (extra_dag, "derived_from_spec", "")


def test_extra_and_missing_node_still_refuse_with_and_without_stray_file(
    tmp_path: Path,
) -> None:
    """BEH-17/NFR-03: лишний узел DAG и недостающий узел отказывают тем же
    сообщением, что и до правки, — с посторонним файлом в каталоге и без
    него. Фильтрация по известным именам не смягчает проверку состава."""
    check = bundle_dag.check_bundle_composition

    # Лишний узел: заявлено LEGACY5 (5), в каталоге лежит полный DAG (6) —
    # acceptance лишний.
    for with_stray in (False, True):
        d = tmp_path / f"extra-{with_stray}"
        d.mkdir()
        for fname, _ in DAG:
            (d / fname).write_text("x", encoding="utf-8")
        if with_stray:
            (d / "README.md").write_text("x", encoding="utf-8")
        with pytest.raises(RuntimeError, match="не совпадает с заявленным"):
            check(str(tmp_path), d.name, bundle_dag.BUNDLE_DAG_LEGACY5)

    # Недостающий узел: заявлен полный DAG (6), в каталоге нет decomposition.
    for with_stray in (False, True):
        d = tmp_path / f"missing-{with_stray}"
        d.mkdir()
        for fname, _ in DAG[:-1]:
            (d / fname).write_text("x", encoding="utf-8")
        if with_stray:
            (d / "README.md").write_text("x", encoding="utf-8")
        with pytest.raises(RuntimeError, match="не совпадает с заявленным"):
            check(str(tmp_path), d.name, DAG)
