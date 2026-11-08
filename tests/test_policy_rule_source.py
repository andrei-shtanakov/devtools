"""Синтетическая политика test (план 3a, Task 3): источник и версия из конфига.

В `mode = "test"` утверждающий — `accounts.human` конфига: `policy_source()`
отдаёт служебные координаты, не читая ни SSOT, ни `[policy]`. `strict` и
отсутствие конфига — как прежде (SSOT, сверка `[policy]`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from governance import approval_facts, policy_rule
from governance.facts import Outcome
from tests.df_config_fixture import write_config

SSOT = ("andrei-shtanakov/approval-policy", "main", "policy/approvers.env")
_STRICT = {
    "mode": "strict",
    "agent_gh_config_dir": Path("~/.config/review"),
    "human_gh_config_dir": Path("~/.config/gh"),
}


def _use_config(monkeypatch: pytest.MonkeyPatch, xdg: Path, **kw: Any) -> None:
    env = write_config(xdg, **kw)
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])


def test_policy_source_strict_and_no_config_unchanged(monkeypatch, tmp_path) -> None:
    """Review Focus 1: strict — SSOT и сверка `[policy]`; конфига нет — SSOT."""
    _use_config(monkeypatch, tmp_path / "a", **_STRICT)
    assert policy_rule.policy_source() == SSOT
    assert policy_rule.test_policy() is None
    _use_config(monkeypatch, tmp_path / "b", policy_repo="evil/policy", **_STRICT)
    with pytest.raises(RuntimeError, match="расходится с authority-root SSOT"):
        policy_rule.policy_source()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert policy_rule.policy_source() == SSOT
    assert policy_rule.test_policy() is None


def test_policy_source_test_mode_ignores_policy_table(monkeypatch, tmp_path) -> None:
    """Review Focus 3: `[policy]` ≠ SSOT в test — не отказ, а не используется."""
    _use_config(monkeypatch, tmp_path, policy_repo="evil/policy", policy_ref="x")
    assert policy_rule.policy_source() == policy_rule.TEST_POLICY_SOURCE
    assert policy_rule.TEST_POLICY_SOURCE == (
        "darkfactory-config",
        "test",
        "accounts.human",
    )


def test_policy_source_test_mode_does_not_read_ssot(monkeypatch, tmp_path) -> None:
    """В test SSOT не читается: битый SSOT-файл ему не мешает."""
    _use_config(monkeypatch, tmp_path)
    monkeypatch.setattr(policy_rule, "POLICY_SOURCE_FILE", tmp_path / "nowhere.env")
    assert policy_rule.policy_source() == policy_rule.TEST_POLICY_SOURCE


def test_policy_source_test_mode_without_policy_table(monkeypatch, tmp_path) -> None:
    """test без `[policy]` — те же служебные координаты."""
    _use_config(monkeypatch, tmp_path)
    path = tmp_path / "darkfactory" / "config.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"\[policy\]\n(.+\n){3}", "", text), encoding="utf-8")
    assert policy_rule.policy_source() == policy_rule.TEST_POLICY_SOURCE


def test_test_policy_sha_is_content_digest(monkeypatch, tmp_path) -> None:
    """Содержимое — `AUTHORIZED_APPROVER_ACCOUNTS=<human>`; sha — 40 hex sha256."""
    _use_config(monkeypatch, tmp_path, human="alice", agent="alice")
    policy = policy_rule.test_policy()
    assert policy is not None
    assert policy.content == "AUTHORIZED_APPROVER_ACCOUNTS=alice\n"
    assert policy.sha == hashlib.sha256(policy.content.encode()).hexdigest()[:40]
    assert re.fullmatch(r"[0-9a-f]{40}", policy.sha)
    assert policy_rule.policy_accounts(policy.content) == frozenset({"alice"})
    _use_config(monkeypatch, tmp_path, human="carol", agent="alice")
    changed = policy_rule.test_policy()
    assert changed is not None and changed.sha != policy.sha


def test_test_policy_broken_config_is_runtime_error(monkeypatch, tmp_path) -> None:
    """Битый конфиг — RuntimeError, как у `policy_source`."""
    path = tmp_path / "darkfactory" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("schema = 1\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="config.toml"):
        policy_rule.test_policy()


def test_env_allowlist_still_refused_in_test_mode(monkeypatch, tmp_path) -> None:
    """Запрет env `AUTHORIZED_APPROVER_ACCOUNTS` — в обоих режимах."""
    _use_config(monkeypatch, tmp_path, human="alice", agent="alice")
    monkeypatch.setenv(policy_rule.APPROVER_ALLOWLIST_ENV, "alice")
    snapshot = approval_facts.policy_snapshot(object(), pinned_sha=None)  # type: ignore[arg-type]
    assert snapshot.outcome is Outcome.FORBIDDEN
    assert policy_rule.APPROVER_ALLOWLIST_ENV in snapshot.detail
