"""Tests for grouping native files and rule (в) problems by distribution
(Task 14-B inventory helper)."""

from __future__ import annotations

from pathlib import Path

from tests.df.dist_records import (
    NO_FILE,
    NO_RECORD,
    by_distribution,
    reason_class,
    record_owners,
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
