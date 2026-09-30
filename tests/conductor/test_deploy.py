from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_service_runs_level_0_under_flock() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "flock -n /srv/conductor/state/conductor.lock" in unit
    assert "-m conductor run" in unit and "--level" not in unit
    assert "User=conductor" in unit and "TimeoutStartSec=55min" in unit


def test_timer_hourly_and_setup_does_not_enable() -> None:
    assert "OnCalendar=hourly" in (DEPLOY / "conductor.timer").read_text()
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    assert "systemctl enable" not in setup
    assert "clone_fleet.py" in setup and "--root" in setup


def test_setup_clones_full_history_over_https() -> None:
    # рубеж 3 C1/I1: мелкие клоны ломают возраст и историю; ssh-ключа на VPS нет
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    assert "--https" in setup and "--unshallow" in setup
