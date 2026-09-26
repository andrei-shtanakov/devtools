"""Task 4 — target environment as data only (§1.5)."""

from __future__ import annotations

import os
from pathlib import Path

from selfcheck.env import EnvInfo, apply_env_policy, detect_env, package_module_map
from selfcheck.model import Confidence, Finding, Location
from tests.selfcheck.helpers import fake_venv


def test_modes_and_staleness(tmp_path: Path) -> None:
    assert detect_env(tmp_path).mode == "no-env"
    site = fake_venv(tmp_path, version="3.13.7")
    env = detect_env(tmp_path)
    assert (env.mode, env.site_packages, env.python_version, env.stale) == (
        "checkout-venv",
        site,
        "3.13",
        False,
    )
    lock = tmp_path / "uv.lock"
    lock.write_text("")
    later = (tmp_path / ".venv" / "pyvenv.cfg").stat().st_mtime + 60
    os.utime(lock, (later, later))
    assert detect_env(tmp_path).stale is True


def test_mapping_keyed_by_declaration_spelling(tmp_path: Path) -> None:
    site = fake_venv(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = ["pyyaml>=6.0.3"]\n'
        '[dependency-groups]\ndev = ["Tomli", {include-group = "x"}]\n'
    )
    assert package_module_map(site, pyproject) == "pyyaml=_yaml|yaml,Tomli=tomli"


def imp(rule: str) -> Finding:
    return Finding(
        rule=rule,
        category="deps",
        severity="medium",
        confidence=Confidence.LIKELY,
        owner_repo="r",
        anchor="file:a.py",
        locations=[Location("a.py", 1)],
    )


def test_no_env_moves_import_class_out() -> None:
    kept, counts = apply_env_policy(
        [imp("deptry/DEP001"), imp("pyrefly/missing-import"), imp("ruff/F401")],
        EnvInfo("no-env"),
    )
    assert [f.rule for f in kept] == ["ruff/F401"]
    assert counts == {"deptry/DEP001": 1, "pyrefly/missing-import": 1}


def test_stale_caps_import_class() -> None:
    kept, counts = apply_env_policy(
        [imp("deptry/DEP001"), imp("ruff/F401")], EnvInfo("checkout-venv", stale=True)
    )
    assert counts == {}
    assert [f.confidence for f in kept] == [Confidence.CANDIDATE, Confidence.LIKELY]
