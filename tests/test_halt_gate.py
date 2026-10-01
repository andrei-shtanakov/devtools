"""Стоп-кран D2: правило допуска (contracts/halt-admission/v1, вендорено)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from governance import halt_gate, runner
from governance import run_state as rs

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "halt-admission" / "v1"
VECTORS = json.loads((CONTRACT / "vectors.json").read_text())["vectors"]


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_every_vector_of_the_contract(v: dict) -> None:
    admit, code, _ = halt_gate.decide(v["listing"], v["detail"])
    assert (admit, code) == (v["admit"], v["code"])


def test_the_vendored_copy_is_the_pinned_one() -> None:
    """Целостность копии: файлы совпадают с sha256, записанными в PINNED."""
    pinned = dict(
        line.split("  ", 1)[::-1]
        for line in (CONTRACT / "PINNED.txt").read_text().splitlines()
        if line[:64].isalnum() and len(line.split("  ")) == 2
    )
    for name in ("README.md", "vectors.json"):
        digest = hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest()
        assert pinned.get(name) == digest, name


def test_main_exit_codes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(halt_gate, "check", lambda slug: (True, "admit_off", "off"))
    assert halt_gate.main(["o/r"]) == 0
    monkeypatch.setattr(halt_gate, "check", lambda slug: (False, "refuse_on", "on"))
    assert halt_gate.main(["o/r"]) == halt_gate.EXIT_HALTED
    assert "refuse_on" in capsys.readouterr().out
    assert halt_gate.main(["not-a-slug"]) == 2


def test_check_reads_the_detail_only_for_a_single_halt(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    answers = {
        "list": [[{"id": 7, "name": "darkfactory-halt"}]],
        "detail": {"enforcement": "active"},
    }

    def fake(*args: str):
        calls.append(args)
        return answers["list"] if "--slurp" in args else answers["detail"]

    monkeypatch.setattr(halt_gate, "_gh_json", fake)
    assert halt_gate.check("o/r")[:2] == (False, "refuse_on")
    assert calls[-1] == ("repos/o/r/rulesets/7",)
    answers["list"] = [[]]
    calls.clear()
    assert halt_gate.check("o/r")[:2] == (True, "admit_missing")
    assert len(calls) == 1


def test_an_unreadable_listing_refuses(monkeypatch) -> None:
    monkeypatch.setattr(halt_gate, "_gh_json", lambda *a: None)
    assert halt_gate.check("o/r")[:2] == (False, "refuse_unknown")


@pytest.fixture
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


_START = [
    "start",
    "--subject",
    "s",
    "--repo",
    "alpha",
    "--repo-slug",
    "owner/alpha",
    "--ws-id",
    "ws-1",
    "--target-dir",
    "/nonexistent",
]


def test_runner_start_under_a_halt_refuses_before_anything(
    monkeypatch, runs_root, capsys
) -> None:
    asked: list[str] = []

    def gate(slug: str) -> str | None:
        asked.append(slug)
        return "стоп-кран DarkFactory (refuse_on): on"

    monkeypatch.setattr(runner, "_HALT_GATE", gate)
    assert runner.main(_START) == halt_gate.EXIT_HALTED
    assert asked == ["owner/alpha"]
    assert "стоп-кран" in capsys.readouterr().err
    assert not runs_root.exists()


def test_a_direct_start_is_gated_too(monkeypatch, runs_root, tmp_path) -> None:
    """Ревью devtools#531: spec_loop зовёт runner.start() напрямую, мимо
    main() — гейт обязан стоять в самой функции."""
    monkeypatch.setattr(runner, "_HALT_GATE", lambda slug: "стоп-кран (refuse_on): on")
    with pytest.raises(halt_gate.HaltedError):
        runner.start(
            subject="s",
            repo="alpha",
            repo_slug="owner/alpha",
            ws_id="ws-1",
            target_dir=str(tmp_path),
            bundle_dir="workstreams/ws-1/spec",
            profile="default",
            run_id="ws-1-aaaaaa",
            ops=None,  # type: ignore[arg-type] — отказ раньше любого эффекта
        )
    assert not runs_root.exists()


@pytest.mark.parametrize("fn", ["start", "verify", "reopen"])
def test_every_new_work_entry_asks_the_halt(fn: str) -> None:
    import inspect

    assert "_refuse_if_halted(" in inspect.getsource(getattr(runner, fn))


def test_resume_drains_and_does_not_ask() -> None:
    """Допущенный прогон дорабатывает: resume стоп не спрашивает."""
    import inspect

    assert "_refuse_if_halted" not in inspect.getsource(runner.resume)
