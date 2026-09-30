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


def test_setup_refuses_gh_without_slurp(tmp_path: Path) -> None:
    # приёмка на VPS 2026-09-30: gh 2.45 из Ubuntu не знает --slurp → partial
    import subprocess

    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    gh_src = (DEPLOY.parents[1] / "conductor" / "sources_gh.py").read_text("utf-8")
    assert "--slurp" in gh_src and 'GH_MIN="2.48.0"' in setup
    check = setup.split("# >>> gh-version-check")[1].split("# <<< gh-version-check")[0]
    for version, code in (("2.45.0", 1), ("2.48.0", 0), ("2.102.0", 0)):
        fake = tmp_path / "gh"
        fake.write_text(f"#!/bin/sh\necho 'gh version {version} (x)'\n")
        fake.chmod(0o755)
        done = subprocess.run(
            ["bash", "-c", check],
            env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == code, (version, done.stdout)
