"""Оси merge_gate из реальных источников (спека §6): политика + CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    # факт сегодняшней политики: флаг False, ai-prosto не в списках -> unknown
    assert s.agent_merge_allowed is False
    assert s.actor_class == "unknown"


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
