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


def test_service_has_a_start_timeout() -> None:
    """A hung git/gh call must not hold r16.lock forever (final review I1)."""
    unit = (DEPLOY / "r16-kb-freshness.service").read_text().splitlines()
    assert "TimeoutStartSec=30min" in unit


def readme() -> str:
    return (DEPLOY / "README.md").read_text()


def test_rollback_waits_for_activating_not_for_inactive() -> None:
    """After ok:false the oneshot unit is `failed`, never `inactive` (final review I2)."""
    text = readme()
    assert '= inactive ]' not in text
    assert "is-active -q r16-kb-freshness.service" in text


def test_mac_lock_check_does_not_need_flock() -> None:
    """macOS has no flock(1) (final review I3): the Mac step uses Python fcntl."""
    mac_step = readme().split("На Mac (шаг 2):", 1)[1].split("Снимок (шаг 3)", 1)[0]
    assert "flock -n" not in mac_step
    assert "fcntl.LOCK_NB" in mac_step


def test_setup_fails_when_the_manifest_yields_no_vault() -> None:
    """python3 < 3.11 has no tomllib: an empty repo list must stop setup (review M4)."""
    setup = (DEPLOY / "setup.sh").read_text()
    assert "mapfile -t REPOS < <(" not in setup
    assert 'grep -qx prograph-vault <<<"$REPO_LIST"' in setup
