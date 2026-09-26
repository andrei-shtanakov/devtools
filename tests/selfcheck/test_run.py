"""Task 14 — orchestrator, report, exit codes (§1, §4.2–4.3, §0 criteria)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from selfcheck import run as run_module
from selfcheck.corpus import snapshot_hashes
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.probes.base import Canary, ProbeResult, ProbeSpec, ProbeStatus
from selfcheck.probes.python_tools import RUFF
from selfcheck.registry import REGISTRY
from selfcheck.report import new_run_dir
from selfcheck.run import exit_code, main
from tests.selfcheck.helpers import (
    plist_dir,
    require_npx_package,
    require_tool,
    workspace,
)


@pytest.mark.parametrize(
    ("statuses", "code"),
    [
        ([ProbeStatus.OK, ProbeStatus.SKIPPED], 0),
        ([ProbeStatus.OK, ProbeStatus.PARTIAL], 2),
        ([ProbeStatus.FAILED, ProbeStatus.UNAVAILABLE], 2),
        ([ProbeStatus.OK, ProbeStatus.UNAVAILABLE], 3),
    ],
)
def test_exit_codes(statuses, code) -> None:
    assert exit_code([ProbeResult("p", "r", s) for s in statuses]) == code


def test_run_dirs_unique_under_frozen_time(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    first, _ = new_run_dir(tmp_path, now)
    second, _ = new_run_dir(tmp_path, now)
    assert first != second and first.startswith("20260926T120000Z-")


def test_run_dir_collision_retries_then_gives_up(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    new_run_dir(tmp_path, now, token=lambda: "aaaaaa")
    tokens = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    assert new_run_dir(tmp_path, now, token=lambda: next(tokens))[0].endswith("bbbbbb")
    with pytest.raises(OSError):
        new_run_dir(tmp_path, now, token=lambda: "aaaaaa")


def args(ws: Path, *extra: str) -> list[str]:
    return [
        "--workspace",
        str(ws),
        "--manifest",
        str(ws / "m.toml"),
        "--out",
        str(ws / "out"),
        "--config",
        str(ws / "none.toml"),
        *extra,
    ]


def reports(ws: Path) -> list[dict]:
    runs = sorted(
        (ws / "out").iterdir(), key=lambda p: (p / "report.json").stat().st_mtime_ns
    )
    return [json.loads((r / "report.json").read_text()) for r in runs]


def test_end_to_end_report_delta_and_provenance(tmp_path: Path, capsys) -> None:
    ws = workspace(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"], name="dev.atp.live.plist")
    before = snapshot_hashes(ws / "devtools")
    extra = ("--probe", "usage-graph", "--probe", "ast-dup", "--sched-dir", str(sched))
    assert main(args(ws, *extra)) == 0
    printed = capsys.readouterr().out.strip()
    assert main(args(ws, *extra)) == 0
    assert snapshot_hashes(ws / "devtools") == before  # §6.2
    first, doc = reports(ws)
    assert printed.endswith(f"{first['run']['run_id']}/report.md")  # m1
    dead = [f for f in doc["findings"] if f["rule"] == "usage-graph/dead.file"]
    assert [f["anchor"] for f in dead] == ["file:orphan.py"]
    assert doc["delta"]["statuses"][dead[0]["id"]] == "persisting"
    run = doc["run"]
    assert run["manifest"]["entries_read"] == 1 and run["host"]
    assert run["scope"] == ["devtools"]
    assert run["config_sha1"] == hashlib.sha1(b"").hexdigest()
    assert len(run["repos"]["devtools"]["head"]) == 40
    assert run["repos"]["devtools"]["dirty"] is False
    assert run["surface"]["plists"] == ["dev.atp.live.plist"]
    live = doc["graph"]["devtools"]["file:live.py"]
    assert live["class"] == "live"
    assert {e["kind"] for e in live["edges"]} == {"make", "sched"}
    assert all(p["rules"] for p in doc["probes"] if p["status"] == "ok")
    assert not (ws / "out" / run["run_id"] / "src" / "devtools").exists()
    md = (ws / "out" / run["run_id"] / "report.md").read_text()
    assert "| usage-graph | devtools | ok |" in md


BOOM = ProbeSpec(
    name="boom",
    languages=frozenset({"any"}),
    input_mode="files",
    select=lambda t: t.corpus,
    canary=Canary(".selfcheck-canary/boom/c.py", "x = 1\n", "boom/X", "file:x"),
    analyze=lambda ctx: 1 / 0,
    logic_version=1,
    rules=("boom/X",),
)


def test_failed_probe_is_a_finding_and_run_continues(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    assert main(args(ws), registry=(BOOM, USAGE_GRAPH)) == 2
    (doc,) = reports(ws)
    assert {p["probe"]: p["status"] for p in doc["probes"]} == {
        "boom": "failed",
        "usage-graph": "ok",
    }
    assert any(
        f["rule"] == "selfcheck/probe-failed" and f["severity"] == "high"
        for f in doc["findings"]
    )
    assert not (ws / "out" / doc["run"]["run_id"] / "src" / "devtools").exists()


def test_relative_out_from_make_style_cwd(tmp_path: Path, monkeypatch) -> None:
    require_tool("ruff")
    ws = workspace(tmp_path, {"a.py": "import os\n"})
    monkeypatch.chdir(ws / "devtools")
    code = main(
        ["--workspace", "..", "--manifest", "../m.toml", "--config", "none.toml"],
        registry=(RUFF,),
    )
    (doc,) = [
        json.loads(p.read_text())
        for p in (ws / "devtools" / "out" / "selfcheck").glob("*/report.json")
    ]
    assert code == 0 and doc["probes"][0]["status"] == "ok"
    assert any(f["rule"] == "ruff/F401" for f in doc["findings"])


def test_bad_config_and_unknown_repo_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "bad.toml").write_text("[[allow]]\nanchor = 'x'\n")
    bad = args(ws)
    bad[bad.index("--config") + 1] = str(ws / "bad.toml")
    assert main(bad) == 4
    assert main(args(ws, "--repo", "nope")) == 4


def test_full_registry_leaves_source_untouched(tmp_path: Path, monkeypatch) -> None:
    for spec in REGISTRY:
        if spec.name == "jscpd":
            require_npx_package("jscpd@4.3.0")
        elif spec.binary:
            require_tool(spec.binary)
    ws = workspace(tmp_path, {"a.py": "import os\n", "run.sh": "#!/bin/sh\necho $1\n"})
    repo = ws / "devtools"
    before = snapshot_hashes(repo)
    before.pop(".git/index", None)
    index_mtime = (repo / ".git" / "index").stat().st_mtime_ns
    monkeypatch.chdir(repo)
    code = main(
        ["--workspace", "..", "--manifest", "../m.toml", "--config", "none.toml"]
    )
    after = {
        k: v
        for k, v in snapshot_hashes(repo).items()
        if not k.startswith("out/") and k != ".git/index"
    }
    assert code in (0, 3)
    assert after == before
    assert (repo / ".git" / "index").stat().st_mtime_ns == index_mtime


def test_repo_missing_is_finding_and_exit_2(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[apps.ghost]\ngit_dir = "ghost"\n'
    )
    assert (
        main(
            args(ws, "--repo", "devtools", "--repo", "ghost", "--probe", "usage-graph")
        )
        == 2
    )
    (doc,) = reports(ws)
    assert any(f["rule"] == "selfcheck/repo-missing" for f in doc["findings"])


def test_report_write_failure_exit_4(tmp_path: Path, monkeypatch) -> None:
    ws = workspace(tmp_path)

    def fail(run_dir, doc):
        raise OSError("disk full")

    monkeypatch.setattr(run_module, "write_report", fail)
    assert main(args(ws, "--probe", "usage-graph")) == 4


def test_cleanup_failure_is_a_warning(tmp_path: Path, monkeypatch) -> None:
    ws = workspace(tmp_path)
    monkeypatch.setattr(run_module, "release", lambda dest: f"cleanup failed: {dest}")
    assert main(args(ws, "--probe", "usage-graph")) == 0
    (doc,) = reports(ws)
    assert any(w.startswith("cleanup failed") for w in doc["run"]["warnings"])


# ---- final review (2026-09-26) ---------------------------------------------------


def test_corrupt_baseline_is_skipped_with_a_warning(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    bad = ws / "out" / "20000101T000000Z-aaaaaa"
    bad.mkdir(parents=True)
    (bad / "report.json").write_text('{"schema": 1, "run": {')
    assert main(args(ws, "--probe", "usage-graph")) == 0
    doc = next(d for d in reports_all(ws) if d is not None)
    assert any("baseline" in w for w in doc["run"]["warnings"])


def reports_all(ws: Path) -> list[dict | None]:
    out = []
    for p in sorted((ws / "out").glob("*/report.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            out.append(None)
    return out


def test_narrowed_run_is_not_a_baseline_and_skips_graph_probes(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    assert main(args(ws, "--probe", "usage-graph")) == 0
    narrowed = main(args(ws, "--probe", "usage-graph", "--path", "live.py"))
    assert narrowed == 0
    assert main(args(ws, "--probe", "usage-graph")) == 0
    _first, middle, last = reports(ws)
    assert {p["probe"]: (p["status"], p["reason"]) for p in middle["probes"]} == {
        "usage-graph": ("skipped", "narrowed-corpus")
    }
    dead = [f["id"] for f in last["findings"] if f["rule"] == "usage-graph/dead.file"]
    assert dead and all(last["delta"]["statuses"][i] == "persisting" for i in dead)
