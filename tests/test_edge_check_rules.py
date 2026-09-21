from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def test_identity_changes_when_items_are_reordered(tmp_path: Path) -> None:
    src = (CONTRACTS / "rules/behaviour-vs-requirements.yaml").read_text(
        encoding="utf-8"
    )
    first = r.load_rules("behaviour-vs-requirements", CONTRACTS)

    swapped_dir = tmp_path / "rules"
    swapped_dir.mkdir(parents=True)
    lines = src.splitlines(keepends=True)

    # Найти индекс начала блока R1 и R2
    r1_idx = next(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id: R1"))
    r2_idx = next(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("- id: R2"))

    # Найти конец блока R1 (строка перед R2, но без самой R2)
    r1_end = r2_idx
    # Найти конец блока R2 (первая строка на уровне секции, не пункта)
    r2_end = next((i for i in range(r2_idx + 1, len(lines))
                   if lines[i][0] not in (' ', '\t')),
                  len(lines))

    # Извлечь блоки пунктов целиком
    block_r1 = lines[r1_idx:r1_end]
    block_r2 = lines[r2_idx:r2_end]

    # Поменять местами: R2 идёт на место R1, R1 идёт на место R2
    new_lines = (lines[:r1_idx] + block_r2 + block_r1 + lines[r2_end:])

    (swapped_dir / "behaviour-vs-requirements.yaml").write_text(
        "".join(new_lines), encoding="utf-8"
    )
    (tmp_path / "instruction.md").write_text(
        (CONTRACTS / "instruction.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    second = r.load_rules("behaviour-vs-requirements", tmp_path)

    assert first.identity != second.identity


def test_unknown_edge_is_config_error() -> None:
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("no-such-edge", CONTRACTS)
    assert exc.value.code == "unknown_edge"
    assert "no-such-edge" in str(exc.value)
