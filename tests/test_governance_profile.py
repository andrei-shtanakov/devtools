"""Тесты профиля `profiles/team-exp.yaml` (форма узлов конвейера)."""

from pathlib import Path

import yaml


def test_team_exp_profile_has_design_node():
    prof = yaml.safe_load(Path("profiles/team-exp.yaml").read_text(encoding="utf-8"))
    nodes = {a["id"]: a for a in prof["artifacts"]}
    d = nodes["design"]
    assert d["owner_role"] == "architects"
    assert d["upstream"] == ["requirements", "behaviour-spec"]
    assert d["template"] == "design.md"
    assert nodes["tasks"]["upstream"] == ["design"]
