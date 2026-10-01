"""Конфиг хоста (спека среза 1, §4.2)."""

from pathlib import Path

import pytest

from conductor.host_config import LOCK_PATH, ConfigError, load_host_config

FLEET = """
[app]
app_id = 11
installation_id = 22
private_key = "/srv/conductor/keys/c.pem"
[run]
profile = "fleet"
shadow = true
state_dir = "/srv/conductor/opstate"
"""
ACCEPTANCE = """
[app]
app_id = 11
installation_id = 22
private_key = "/srv/conductor/keys/c.pem"
[run]
profile = "acceptance"
shadow = false
state_dir = "/srv/conductor/acceptance"
[acceptance]
sandbox = "own/conductor-sandbox"
outside = ["own/conductor-sandbox-outside"]
stop_points = ["after_send"]
"""


def _load(tmp_path: Path, text: str):
    path = tmp_path / "c.toml"
    path.write_text(text, encoding="utf-8")
    return load_host_config(path)


def test_fleet_config(tmp_path: Path) -> None:
    cfg = _load(tmp_path, FLEET)
    assert (cfg.app_id, cfg.installation_id, cfg.profile, cfg.shadow) == (
        11,
        22,
        "fleet",
        True,
    )
    assert cfg.state_dir == Path("/srv/conductor/opstate")
    assert cfg.sandbox is None and cfg.stop_points == frozenset()


def test_acceptance_config(tmp_path: Path) -> None:
    cfg = _load(tmp_path, ACCEPTANCE)
    assert cfg.sandbox == "own/conductor-sandbox"
    assert cfg.outside == ("own/conductor-sandbox-outside",)
    assert cfg.stop_points == {"after_send"}


@pytest.mark.parametrize(
    "text",
    [
        FLEET + '[acceptance]\nsandbox = "own/x"\n',  # acceptance в fleet
        FLEET.replace('profile = "fleet"', 'profile = "prod"'),
        FLEET.replace("shadow = true", 'shadow = "yes"'),
        FLEET.replace("app_id = 11", "app_id = 0"),
        FLEET + "[extra]\nx = 1\n",
        FLEET.replace("[run]", "[run]\nunknown = 1"),
        ACCEPTANCE.replace('"/srv/conductor/acceptance"', '"/srv/conductor/opstate"'),
        ACCEPTANCE.replace('["after_send"]', '["anywhere"]'),
        ACCEPTANCE.replace('sandbox = "own/conductor-sandbox"\n', ""),
        FLEET.replace('state_dir = "/srv/conductor/opstate"', 'state_dir = "rel"'),
        FLEET.replace("[run]", '[run]\nlock = "/srv/conductor/opstate/x.lock"'),
        FLEET.replace("[run]", '[run]\nlock = "rel.lock"'),
        "x = [",
    ],
)
def test_invalid_configs_raise(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, text)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_host_config(tmp_path / "absent.toml")


def test_lock_shared_by_profiles_and_outside_state(tmp_path: Path) -> None:
    fleet, acc = _load(tmp_path, FLEET), _load(tmp_path, ACCEPTANCE)
    assert fleet.lock == acc.lock == LOCK_PATH
    assert not LOCK_PATH.is_relative_to(fleet.state_dir)
    custom = _load(tmp_path, FLEET.replace("[run]", '[run]\nlock = "/tmp/c.lock"'))
    assert custom.lock == Path("/tmp/c.lock")
