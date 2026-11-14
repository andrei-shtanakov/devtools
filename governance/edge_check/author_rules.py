"""Правила ребра узла для промпта автора.

Автор видит те же пункты, по которым его узел потом проверит edge-check.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from governance.edge_check.rules import RuleSet, load_rules

DEFAULT_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts/edge-check/v1"


def _rulesets_for(kind: str, contracts_dir: Path) -> list[RuleSet]:
    """Наборы правил рёбер, где узел `kind` — субъект (порядок по имени файла)."""
    found: list[RuleSet] = []
    for path in sorted((contracts_dir / "rules").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(doc, dict) and doc.get("subject_role") == kind:
            found.append(load_rules(path.stem, contracts_dir))
    return found


def _edge_block(rules: RuleSet) -> str:
    items = "\n".join(f"- {it.id}: {it.text}" for it in rules.items)
    basis = ", ".join(rules.basis_roles)
    return (
        f"Your node will be checked against these rules (edge {rules.edge_id}):\n"
        f"{items}\n"
        f"Reference only ids defined in: {basis}; never ids from discovery "
        "briefs unless the basis defines them."
    )


def rules_for(kind: str, contracts_dir: Path = DEFAULT_CONTRACTS) -> str:
    """Блок правил рёбер для узла `kind`; пусто, если таких рёбер нет."""
    blocks = [_edge_block(rs) for rs in _rulesets_for(kind, contracts_dir)]
    return "\n\n".join(blocks)
