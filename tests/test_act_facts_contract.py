"""Копия контракта approval-facts v3: целостность и пин (без steward).

Две гарантии вендоренной копии: copy-integrity (MANIFEST ↔ файлы, точный состав
каталога) и upstream-drift (пин копии == пин зависимости). Тесты не зависят от
steward и не пропускаются без группы governance.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts/approval-facts/v3"
VECTORS = CONTRACT / "vectors"
TOP_FILES = {"MANIFEST.json", "README.md", "SCHEMA.json", "PINNED.txt"}
UPSTREAM = "andrei-shtanakov/steward"


def _pinned() -> dict[str, str]:
    lines = (CONTRACT / "PINNED.txt").read_text(encoding="utf-8").splitlines()
    pairs = [
        line.split(": ", 1) if ": " in line else line.split(" ", 1) for line in lines
    ]
    return {k: v for k, v in pairs}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _id(path: Path) -> str:
    return path.relative_to(VECTORS).as_posix()


def test_vendored_set_is_complete() -> None:
    assert len(list(VECTORS.rglob("*.json"))) == 79


def test_contract_directory_has_exact_top_level_set() -> None:
    """Лишний или недостающий файл верхнего уровня — расхождение копии."""
    names = {p.name for p in CONTRACT.iterdir()}
    assert names == TOP_FILES | {"vectors"}


def test_pinned_txt_has_exact_shape() -> None:
    """Изменённый PINNED.txt (лишний/пропавший ключ) не проходит."""
    assert set(_pinned()) == {
        "upstream",
        "commit",
        "MANIFEST.json",
        "README.md",
        "SCHEMA.json",
    }


def test_copy_integrity_both_ways() -> None:
    """О7: copy-integrity — гарантия потребителя (MANIFEST ↔ файлы, в обе стороны)."""
    pinned = _pinned()
    for name in ("MANIFEST.json", "README.md", "SCHEMA.json"):
        assert _sha256(CONTRACT / name) == pinned[name], name
    expected = json.loads((CONTRACT / "MANIFEST.json").read_text("utf-8"))["vectors"]
    actual = {_id(p): _sha256(p) for p in VECTORS.rglob("*") if p.is_file()}
    assert actual == expected


def test_vendor_pin_equals_dependency_pin() -> None:
    """Upstream-drift: копия контракта — от того же репо и SHA, что зависимость."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    source = data["tool"]["uv"]["sources"]["steward"]
    pinned = _pinned()
    assert pinned["commit"] == source["rev"]
    assert source["git"].endswith(f"/{UPSTREAM}")
    assert pinned["upstream"] == UPSTREAM
