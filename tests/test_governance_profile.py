"""Тесты профиля `profiles/team-exp.yaml` (форма узлов конвейера)."""

from pathlib import Path

import pytest
import yaml


def test_team_exp_profile_has_design_node():
    prof = yaml.safe_load(Path("profiles/team-exp.yaml").read_text(encoding="utf-8"))
    nodes = {a["id"]: a for a in prof["artifacts"]}
    d = nodes["design"]
    assert d["owner_role"] == "architects"
    assert d["upstream"] == ["requirements", "behaviour-spec"]
    assert d["template"] == "design.md"
    assert nodes["decomposition"]["owner_role"] == "tech-lead"
    assert nodes["decomposition"]["upstream"] == ["design"]
    assert nodes["tasks"]["upstream"] == ["decomposition"]


def test_team_exp_profile_loads_via_real_steward_and_orders_nodes():
    """LOW-1 финального ревью: канонический профиль обязан проходить через
    РЕАЛЬНЫЙ steward (`load_profile`/`load_roles_catalog`), не только
    собственный `yaml.safe_load` этого теста — иначе форма, валидная для
    yaml, но невалидная для steward (например, дублирующийся `id` или
    несуществующий upstream), тихо проходит мимо."""
    pytest.importorskip("steward")
    from steward.graph import load_profile
    from steward.roles import load_roles_catalog

    profile = Path("profiles/team-exp.yaml")
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    assert graph.topo_order() == [
        "charter", "requirements", "behaviour-spec", "design",
        "decomposition", "tasks",
    ]
