"""Конфиг, который генератор фикстуры discovery пишет до `approve`.

Генератор исполняется в venv discovery и импортирует пакет `discovery`, которого
в devtools нет. Поэтому тест грузит файл генератора с подменёнными модулями
`discovery.*` и проверяет его TOML загрузчиком `governance.df_config`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from governance import df_config

GENERATOR = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / ("gen_discovery_approval_fixture.py")
)
DISCOVERY_MODULES = (
    "discovery",
    "discovery.forge",
    "discovery.approval",
    "discovery.cli",
    "discovery.policy",
)


def _load_generator(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Загрузить генератор без пакета discovery (его модули — заглушки)."""
    for name in DISCOVERY_MODULES:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    spec = importlib.util.spec_from_file_location("gen_fixture_under_test", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_generated_config_loads_as_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = _load_generator(monkeypatch)
    xdg = tmp_path / "xdg"
    (xdg / "darkfactory").mkdir(parents=True)
    (xdg / "darkfactory" / "config.toml").write_text(
        gen.fixture_config_toml(tmp_path), encoding="utf-8"
    )

    cfg = df_config.load({"XDG_CONFIG_HOME": str(xdg)})

    assert cfg.mode == "strict"
    assert cfg.accounts.human == gen.HUMAN
    assert cfg.accounts.agent == "fixture-agent"
    assert cfg.policy == df_config.PolicySource(
        repo=gen.POLICY_REPO, ref="main", path="policy/approvers.env"
    )
    assert cfg.workspace_root == tmp_path
    assert (cfg.author_harness, cfg.author_model) == ("claude", "claude-opus-5")
