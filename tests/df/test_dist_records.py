"""Tests for grouping native files and rule (в) problems by distribution
(Task 14-B inventory helper)."""

from __future__ import annotations

from pathlib import Path

from tests.df.dist_records import (
    NO_FILE,
    NO_RECORD,
    by_distribution,
    dist_wheels,
    reason_class,
    record_owners,
    vacuity_problems,
)


def _dist(site: Path, name: str, rows: list[str]) -> None:
    info = site / f"{name}.dist-info"
    info.mkdir()
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_record_owners_maps_listed_paths_to_their_distribution(
    tmp_path: Path,
) -> None:
    _dist(
        tmp_path,
        "pillow-11.0.0",
        [
            "PIL/_imaging.cpython-312-darwin.so,sha256=abc,10",
            "pillow.libs/./libjpeg.so.62,sha256=def,20",
            "../../../bin/tool,sha256=ghi,30",
            "pillow-11.0.0.dist-info/RECORD,,",
        ],
    )
    _dist(tmp_path, "pure_pkg-1.0", ["pure_pkg/__init__.py,,"])
    (tmp_path / "broken-0.1.dist-info").mkdir()  # no RECORD: skipped
    owners = record_owners(tmp_path)
    assert owners["PIL/_imaging.cpython-312-darwin.so"] == "pillow"
    assert owners["pillow.libs/libjpeg.so.62"] == "pillow"
    assert owners["../../../bin/tool"] == "pillow"
    assert owners["pure_pkg/__init__.py"] == "pure_pkg"
    assert "broken" not in owners.values()


def test_by_distribution_attributes_problems_to_the_longest_native() -> None:
    owners = {"a/x.so": "alpha", "a/x.so.1/y.so": "beta"}
    natives = ["a/x.so", "a/x.so.1/y.so", "loose/z.so"]
    problems = [
        "a/x.so: зависимость libfoo.so.1: не найдена в /usr/lib",
        "a/x.so: зависимость libbar.so.2: не найдена в /lib64",
        "a/x.so.1/y.so: путь поиска '$ORIGIN/../lib': вне релиза и системных каталогов",
        "loose/z.so: загрузчик /opt/ld.so вне системных каталогов",
        "обход релиза: Permission denied",
    ]
    grouped = by_distribution(natives, problems, owners)
    assert grouped["alpha"]["natives"] == ["a/x.so"]
    assert len(grouped["alpha"]["problems"]) == 2
    assert grouped["alpha"]["classes"] == ["зависимость *: не найдена в *"]
    assert grouped["beta"]["classes"] == [
        "путь поиска *: вне релиза и системных каталогов"
    ]
    assert grouped[NO_RECORD]["natives"] == ["loose/z.so"]
    assert grouped[NO_RECORD]["classes"] == ["загрузчик * вне системных каталогов"]
    assert grouped[NO_FILE] == {
        "natives": [],
        "problems": ["обход релиза: Permission denied"],
        "classes": ["обход релиза: Permission denied"],
    }


def test_by_distribution_keeps_clean_natives() -> None:
    grouped = by_distribution(["m/n.so"], [], {"m/n.so": "clean"})
    assert grouped == {"clean": {"natives": ["m/n.so"], "problems": [], "classes": []}}


def test_reason_class_masks_tokens() -> None:
    assert reason_class("@rpath/libz.1.dylib: нет в @loader_path/../lib") == (
        "*: нет в *"
    )


def test_dist_wheels_records_version_and_tags(tmp_path: Path) -> None:
    _dist(tmp_path, "rpds_py-0.27.1", [])
    wheel = "Wheel-Version: 1.0\nGenerator: maturin\nTag: cp312-cp312-{}\n"
    (tmp_path / "rpds_py-0.27.1.dist-info" / "WHEEL").write_text(
        wheel.format("manylinux_2_17_x86_64")
        + "Tag: cp312-cp312-manylinux2014_x86_64\n"
    )
    _dist(tmp_path, "nowheel-1.0", [])
    assert dist_wheels(tmp_path) == {
        "rpds_py": {
            "version": "0.27.1",
            "tags": [
                "cp312-cp312-manylinux_2_17_x86_64",
                "cp312-cp312-manylinux2014_x86_64",
            ],
        },
        "nowheel": {"version": "1.0", "tags": []},
    }


def _member(
    code: int, site: bool = True, natives: int = 0, rule_v: object = None
) -> dict[str, object]:
    measured = {"count": 0, "by_distribution": {}} if rule_v is None else rule_v
    return {
        "sync": {"code": code},
        "site_exists": site,
        "native_count": natives,
        "rule_v": measured,
    }


def test_vacuity_passes_a_measured_inventory() -> None:
    members = {"a": _member(0, natives=0), "b": _member(0, natives=2)}
    assert vacuity_problems(members) == []


def test_vacuity_fails_a_missing_site_after_sync() -> None:
    members = {"a": _member(0, site=False), "b": _member(0, natives=1)}
    assert vacuity_problems(members) == ["a: site-packages нет после успешного uv sync"]


def test_vacuity_fails_zero_natives_when_every_member_synced() -> None:
    members = {"a": _member(0), "b": _member(0)}
    assert vacuity_problems(members) == [
        "инвентаризация core: ни одного нативного файла"
    ]


def test_vacuity_fails_when_no_member_synced() -> None:
    """Final review 14, m1: every sync failed (not a network failure) — rule
    (в) ran nowhere, which is not «0 problems»."""
    members = {"a": {"sync": {"code": 1}}, "b": {"sync": {"code": 2}}}
    assert vacuity_problems(members) == [
        "инвентаризация core: ни один член не синхронизирован"
    ]


def test_vacuity_fails_rule_v_error_of_a_synced_member() -> None:
    """Final review 14, m1: natives were counted but rule (в) raised."""
    members = {
        "a": _member(0, natives=2, rule_v={"error": "OSError: boom"}),
        "b": _member(0, natives=1),
    }
    assert vacuity_problems(members) == ["a: правило (в) не исполнено: OSError: boom"]


def test_vacuity_fails_zero_natives_among_synced_despite_a_failed_sync() -> None:
    """PR #633 review: a failed sync no longer turns the zero-natives check
    off — five members fail, one pure-Python member syncs: nothing measured."""
    members: dict[str, dict[str, object]] = {
        f"f{i}": {"sync": {"code": 1}} for i in range(5)
    }
    members["pure"] = _member(0, natives=0)
    assert vacuity_problems(members) == [
        "инвентаризация core: ни одного нативного файла"
    ]


def test_vacuity_failed_sync_with_measured_natives_is_a_fact() -> None:
    """A failed sync stays a recorded fact when the synced members did
    measure native files."""
    members = {"a": _member(0, natives=1), "b": {"sync": {"code": 1}}}
    assert vacuity_problems(members) == []
