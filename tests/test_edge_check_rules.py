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
    r1 = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- id: R1"))
    r2 = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- id: R2"))
    lines[r1], lines[r2] = lines[r2], lines[r1]
    (swapped_dir / "behaviour-vs-requirements.yaml").write_text(
        "".join(lines), encoding="utf-8"
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
