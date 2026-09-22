"""Оси merge_gate из реальных источников (спека §6): политика + CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from governance.merge_gate import Authority, Safety
from governance.policy_sources import (
    build_authority,
    ecosystem_authority,
    load_safety,
    repo_authority,
)


def test_safety_from_vendored_copy() -> None:
    s = load_safety("ai-prosto")
    assert isinstance(s, Safety)
    # факт политики после пина steward@6a70d15 (steward PR #142): флаг True,
    # ai-prosto в agent_identities -> agent
    assert s.agent_merge_allowed is True
    assert s.actor_class == "agent"


def test_safety_integrity_mismatch_is_unknown(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "v1"
    bad.mkdir()
    (bad / "approval-policy.yaml").write_text("agent_merge_allowed: true\n")
    (bad / "PIN").write_text("0" * 64 + "  approval-policy.yaml  x\n")
    monkeypatch.setattr("governance.policy_sources.CONTRACT_DIR", bad)
    s = load_safety("ai-prosto")
    assert s.agent_merge_allowed is None and s.actor_class == "unknown"


def test_safety_missing_copy_is_unknown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("governance.policy_sources.CONTRACT_DIR", tmp_path / "no")
    s = load_safety("ai-prosto")
    assert s.agent_merge_allowed is None and s.actor_class == "unknown"


def test_safety_empty_pin_is_unknown(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "v1"
    bad.mkdir()
    (bad / "approval-policy.yaml").write_text("agent_merge_allowed: true\n")
    for content in ("", "   \n\n"):
        (bad / "PIN").write_text(content)
        monkeypatch.setattr("governance.policy_sources.CONTRACT_DIR", bad)
        s = load_safety("ai-prosto")
        assert s.agent_merge_allowed is None and s.actor_class == "unknown"


def test_repo_authority_reads_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("## Git workflow\n- Мерж: человек\n")
    assert repo_authority(tmp_path) == "human"
    (tmp_path / "CLAUDE.md").write_text("обычный текст без объявления\n")
    assert repo_authority(tmp_path) is None
    assert repo_authority(tmp_path / "нет-такого") is None


def test_ecosystem_default_is_agent() -> None:
    assert ecosystem_authority() == "agent"


def test_build_authority_only_tightens(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("- Мерж: человек\n")
    a = build_authority(tmp_path, run_override=None)
    assert a == Authority(ecosystem="agent", repo="human", run=None)
    assert a.effective() == "human"
    b = build_authority(tmp_path / "пусто", run_override="human")
    assert b.run == "human" and b.effective() == "human"


# --- проекция профиля на волну (sequential-node-approval S5, Task 3) ---

_SIBLINGS = ("roles.yaml", "gate-catalog.yaml", "risk-model.yaml")
_PROFILE = "profiles/team-exp.yaml"


def _target_with_profile(tmp_path: Path) -> Path:
    target = tmp_path / "t"
    (target / "profiles").mkdir(parents=True)
    src = Path(_PROFILE).read_text(encoding="utf-8")  # собственный devtools
    (target / _PROFILE).write_text(src, encoding="utf-8")
    for sib in _SIBLINGS:
        (target / "profiles" / sib).write_text(f"# {sib}\n", encoding="utf-8")
    return target


def test_wave_profile_is_a_level_prefix_of_the_target_profile(
    tmp_path: Path,
) -> None:
    from governance import policy_sources as ps

    target = _target_with_profile(tmp_path)
    run_dir = tmp_path / "run"
    projected = ps.wave_profile_dir(str(target), _PROFILE, 2, run_dir)  # W2 → ≤1
    data = yaml.safe_load(projected.read_text(encoding="utf-8"))
    assert [a["id"] for a in data["artifacts"]] == ["charter", "requirements"]
    assert projected.parent == run_dir / "profile-w2"
    assert (projected.parent / "roles.yaml").read_text() == "# roles.yaml\n"
    verify = ps.verify_wave_profile_dir
    assert verify(str(target), _PROFILE, projected, 1) == []
    # Отрицательный контроль по КОПИИ (ревью #343, R1): её читает steward.
    (projected.parent / "roles.yaml").write_text("# changed\n", encoding="utf-8")
    assert verify(str(target), _PROFILE, projected, 1) == ["roles.yaml"]
    (projected.parent / "roles.yaml").write_text("# roles.yaml\n", encoding="utf-8")
    tampered = projected.read_text(encoding="utf-8") + "# tampered\n"
    projected.write_text(tampered, encoding="utf-8")
    assert verify(str(target), _PROFILE, projected, 1) == ["team-exp.yaml"]
    projected.write_bytes(ps._truncate_profile((target / _PROFILE).read_bytes(), 1))
    assert verify(str(target), _PROFILE, projected, 1) == []
    (projected.parent / "extra.yaml").write_text("x: 1\n", encoding="utf-8")
    assert verify(str(target), _PROFILE, projected, 1) == ["extra.yaml"]
    (projected.parent / "extra.yaml").unlink()
    (projected.parent / "gate-catalog.yaml").unlink()
    assert verify(str(target), _PROFILE, projected, 1) == ["gate-catalog.yaml"]
    (projected.parent / "gate-catalog.yaml").write_text(
        "# gate-catalog.yaml\n", encoding="utf-8"
    )
    # Изменение ИСХОДНИКА sibling'а после копирования проекцию не портит —
    # пин закреплён за копией; а изменение исходного профиля — портит:
    # усечённый профиль обязан быть проекцией ЗАКРЕПЛЁННОГО полного.
    (target / "profiles/roles.yaml").write_text("# later\n", encoding="utf-8")
    assert verify(str(target), _PROFILE, projected, 1) == []
    (target / _PROFILE).write_text("profile: x\nartifacts: []\n", encoding="utf-8")
    assert verify(str(target), _PROFILE, projected, 1) == ["team-exp.yaml"]


def test_wave_profile_pins_source_bytes_read_before_copy(
    tmp_path: Path, monkeypatch,
) -> None:
    """Исходник, изменившийся между чтением и записью копии, не подменяет
    пин: копия и manifest строятся из одних и тех же байтов (R1)."""
    from governance import policy_sources as ps

    target = _target_with_profile(tmp_path)
    roles_src = target / "profiles/roles.yaml"
    real_write = Path.write_bytes

    def racing_write(self: Path, data: bytes) -> int:
        if self.name == "roles.yaml" and roles_src.read_bytes() != b"# raced\n":
            real_write(roles_src, b"# raced\n")  # исходник уехал под ногами
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", racing_write)
    projected = ps.wave_profile_dir(str(target), _PROFILE, 1, tmp_path / "run")
    monkeypatch.undo()
    assert (projected.parent / "roles.yaml").read_bytes() == b"# roles.yaml\n"
    assert roles_src.read_bytes() == b"# raced\n"
    assert ps.verify_wave_profile_dir(str(target), _PROFILE, projected, 0) == []


def test_wave_profile_last_wave_equals_source_artifacts(tmp_path: Path) -> None:
    from governance import policy_sources as ps

    target = _target_with_profile(tmp_path)
    projected = ps.wave_profile_dir(str(target), _PROFILE, 5, tmp_path / "run")
    data = yaml.safe_load(projected.read_text(encoding="utf-8"))
    # Уровни считаются по upstream ПРОФИЛЯ: делегат `tasks` (уровень 5)
    # отсекается, шесть узлов бандла — все.
    assert [a["id"] for a in data["artifacts"]] == [
        "charter", "requirements", "behaviour-spec", "design",
        "acceptance", "decomposition",
    ]
    assert data["profile"] == "team-exp" and data["solo_auto_approve"] is True
    assert ps.wave_profile_dir(str(target), _PROFILE, 5, tmp_path / "run") == projected
    assert ps.verify_wave_profile_dir(str(target), _PROFILE, projected, 4) == []


def test_profile_levels_refuse_cycle_or_unknown_upstream() -> None:
    from governance import policy_sources as ps

    with pytest.raises(RuntimeError, match="цикл|неизвестный"):
        ps._profile_levels([{"id": "a", "upstream": ["b"]}, {"id": "b", "upstream": ["a"]}])
    with pytest.raises(RuntimeError, match="цикл|неизвестный"):
        ps._profile_levels([{"id": "a", "upstream": ["zzz"]}])
