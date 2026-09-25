"""Static properties of deploy/r16 that the spec relies on (§1.3, §2.2)."""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "r16"


def test_service_runs_as_r16_with_umask_0027() -> None:
    unit = (DEPLOY / "r16-kb-freshness.service").read_text().splitlines()
    for line in (
        "User=r16",
        "UMask=0027",
        "Type=oneshot",
        "EnvironmentFile=/srv/r16/r16.env",
    ):
        assert line in unit, line


def test_timer_is_hourly_and_persistent() -> None:
    timer = (DEPLOY / "r16-kb-freshness.timer").read_text().splitlines()
    assert "OnCalendar=hourly" in timer
    assert "Persistent=true" in timer


def test_setup_installs_but_does_not_enable_the_timer() -> None:
    """Switching executors is the handover (spec §2.2), not a setup side effect."""
    setup = (DEPLOY / "setup.sh").read_text()
    commands = [
        line.strip()
        for line in setup.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().startswith("echo")
    ]
    assert "systemctl daemon-reload" in commands
    assert not [c for c in commands if c.startswith("systemctl") and "enable" in c]


def test_setup_sets_the_permission_table() -> None:
    setup = (DEPLOY / "setup.sh").read_text()
    for fragment in (
        'install -d -o r16 -g r16-readers -m 0710 "$R16_HOME"',
        'install -d -o r16 -g r16-readers -m 0710 "$R16_HOME/state"',
        'install -d -o r16 -g r16-readers -m 2750 "$R16_HOME/state/receipts"',
        'install -d -o r16 -g r16 -m 0700 "$R16_HOME/gh"',
        'install -o r16 -g r16 -m 0600 /dev/null "$R16_HOME/state/r16.lock"',
    ):
        assert fragment in setup, fragment


def test_env_example_names_every_setting() -> None:
    env = (DEPLOY / "env.example").read_text()
    for name in ("R16_WORKSPACE", "R16_STATE_DIR", "R16_GH_CONFIG_DIR", "R16_HOST_LABEL"):
        assert f"{name}=" in env
