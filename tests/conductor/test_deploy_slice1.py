"""Деплой среза 1: примеры конфигов, тень, скрипты приёмки (§8–§10)."""

import json
import subprocess
from pathlib import Path

from conductor.host_config import load_host_config
from conductor.markers import h1, make, with_marker

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_examples_are_valid_configs() -> None:
    fleet = load_host_config(DEPLOY / "conductor.toml.example")
    assert fleet.profile == "fleet" and fleet.shadow is True
    acc = load_host_config(DEPLOY / "acceptance.toml.example")
    assert acc.profile == "acceptance" and acc.state_dir != fleet.state_dir


def test_timer_marks_trigger_and_writer_dropin_adds_config() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "--trigger timer" in unit and "--config" not in unit
    dropin = (DEPLOY / "writer.conf").read_text(encoding="utf-8")
    assert "ExecStart=\n" in dropin
    assert (
        "--config /srv/conductor/conductor.toml" in dropin
        and "--trigger timer" in dropin
    )
    # lock берёт сам run --config (P1-3); внешний flock на тот же файл помешал бы
    assert "flock" not in dropin.split("[Service]")[1]
    # без явного потолка ручной и таймерный run — уровень 0 (P1-2)
    assert "--level 3" in dropin


def _acceptance(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DEPLOY / "acceptance.sh"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_acceptance_script_refuses_before_any_network() -> None:
    assert _acceptance("own/devtools", "1", "nudge").returncode == 2
    assert _acceptance("own/conductor-sandbox", "4", "nudge").returncode == 2
    assert _acceptance("own/conductor-sandbox", "1", "todo_hygiene_pr").returncode == 2


def test_dump_parses_markers() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "acceptance_dump", DEPLOY / "acceptance_dump.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = make("q", id=h1("q", "x"))
    issues = [
        [
            {
                "number": 1,
                "state": "open",
                "state_reason": None,
                "labels": [{"name": "owner-queue"}],
                "pull_request": None,
                "title": "t",
            }
        ]
    ]
    comments = [
        [
            {
                "user": {"login": "conductor[bot]"},
                "created_at": "a",
                "updated_at": "a",
                "body": with_marker("x", marker),
            }
        ]
    ]

    def runner(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        if "comments" in key:
            return 0, json.dumps(comments), ""
        return 0, json.dumps(issues), ""

    data = module.dump("own/conductor-sandbox", runner)
    [issue] = data["items"]
    assert issue["comments"][0]["marker"] == {
        "kind": "q",
        "fields": {"id": h1("q", "x")},
    }
    assert "body" not in issue["comments"][0]
