"""S2 Task 6 — `--fleet` end to end: report, exit codes, key, delta (§9, §4.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfcheck.fleet import match
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.run import main
from tests.selfcheck.helpers import (
    NEIGHBOURS_S2,
    commit,
    fleet_ws,
    git,
    make_repo,
    plist_dir,
    synced,
)


def args(ws: Path, sched: Path, *extra: str, config: str = "none.toml") -> list[str]:
    return [
        "--workspace", str(ws),
        "--manifest", str(ws / "umbrella" / "m.toml"),
        "--out", str(ws / "out"),
        "--config", str(ws / config),
        "--probe", "usage-graph",
        "--sched-dir", str(sched),
        *extra,
    ]  # fmt: skip


def reports(ws: Path) -> list[dict]:
    runs = sorted(
        (ws / "out").iterdir(), key=lambda p: (p / "report.json").stat().st_mtime_ns
    )
    return [json.loads((r / "report.json").read_text()) for r in runs]


def markdown(ws: Path, doc: dict) -> str:
    return (ws / "out" / doc["run"]["run_id"] / "report.md").read_text()


def dead(doc: dict) -> dict[str, dict]:
    return {f["anchor"]: f for f in doc["findings"] if f["category"] == "dead"}


def usage(doc: dict) -> dict:
    (probe,) = [p for p in doc["probes"] if p["probe"] == "usage-graph"]
    return probe


def sched(tmp: Path) -> Path:
    return plist_dir(tmp, ["/x/devtools/live.py"])


def test_fleet_end_to_end(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    (doc,) = reports(ws)
    surface = doc["run"]["surface"]
    assert surface["fleet"] == "complete"
    assert [r["name"] for r in surface["fleet_repos"]] == ["nb", "docs-nb", "umbrella"]
    assert len(surface["manifest_sha1"]) == 40
    assert not [p for p in surface["history"] if "fleet_canary" in p]  # isolated
    graph = doc["graph"]["devtools"]
    check = graph["file:check.py"]
    assert check["class"] == "live" and check["fleet_only"] is True
    assert check["edges"][0]["kind"] == "fleet"
    assert check["edges"][0]["from"].startswith("nb:.github/workflows/c.yml")
    assert not [a for a in graph if "fleet_canary" in a]  # canary isolated
    found = dead(doc)
    assert found["file:orphan.py"]["confidence"] == "confirmed"  # D1 via --fleet
    attest = found["file:attest.sh"]
    assert attest["confidence"] == "likely"
    assert {"kind": "cap", "detail": "P5"} in attest["evidence"]
    assert {"kind": "mentioned-in", "detail": "docs-nb:TODO.md"} in attest["evidence"]
    assert "file:scripts/review/local.sh" not in found  # vendored-in (D15)
    assert graph["file:scripts/review/local.sh"]["vendored"] == [
        {"owner": "steward", "ref": "5bfd829", "declaration": "scripts/review/PIN"}
    ]
    assert not [f for f in doc["findings"] if f["rule"].startswith("selfcheck/")]
    md = markdown(ws, doc)
    assert "complete относительно манифеста" in md
    assert "не покрыто: корневой зонтик, `~/.claude`" in md
    assert "давность refs неизвестна для: nb, docs-nb, umbrella" in md
    assert "fleet-only: 1" in md and "check.py" in md
    vendored_rows = [
        line
        for line in md.splitlines()
        if line.startswith("| scripts/review/prose-paths.env")
    ]
    assert len(vendored_rows) == 2  # member of A and header D: two rows


def test_refs_age_phrase(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    for name in ("nb", "docs-nb", "umbrella"):
        path = git(ws / name, "rev-parse", "--git-path", "FETCH_HEAD").strip()
        (ws / name / path).write_text("")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    (doc,) = reports(ws)
    assert "относительно локальных refs не старше" in markdown(ws, doc)


def test_without_fleet_nothing_else_changes(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    assert main(args(ws, sched(tmp_path))) == 0
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "absent"
    assert "fleet_repos" not in doc["run"]["surface"]
    assert dead(doc)["file:orphan.py"]["confidence"] == "likely"  # P1
    assert "fleet-only: — (без --fleet)" in markdown(ws, doc)  # no meaningless 0


def test_usage_graph_logic_version_bumped() -> None:
    """S2 changes usage-graph even without --fleet (vendored-in, P6, partial):
    the comparability key must change against S1 baselines (§4.3, review r1 M4)."""
    assert USAGE_GRAPH.logic_version == 2


def test_broken_declaration_without_fleet_is_exit_2(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path, scope_files={"scripts/review/PIN": "garbage\n"})
    assert main(args(ws, sched(tmp_path))) == 2
    (doc,) = reports(ws)
    assert usage(doc)["status"] == "partial"
    assert "selfcheck/vendor-pin-unparsed" in {f["rule"] for f in doc["findings"]}


def test_broken_declaration_is_partial_exit_2_and_p6(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path, scope_files={"scripts/review/PIN": "garbage\n"})
    assert main(args(ws, sched(tmp_path), "--fleet")) == 2
    (doc,) = reports(ws)
    assert usage(doc)["status"] == "partial"
    orphan = dead(doc)["file:orphan.py"]
    assert orphan["confidence"] == "likely"
    assert {"kind": "cap", "detail": "P6"} in orphan["evidence"]


@pytest.mark.parametrize("channel", ["match_external", "find_mentions"])
def test_broken_channel_fails_the_probe(tmp_path: Path, monkeypatch, channel) -> None:
    ws = fleet_ws(tmp_path)
    fake = (lambda *a, **k: []) if channel == "match_external" else (lambda *a, **k: {})
    monkeypatch.setattr(match, channel, fake)
    assert main(args(ws, sched(tmp_path), "--fleet")) == 2
    (doc,) = reports(ws)
    assert usage(doc)["status"] == "failed"
    assert "fleet-canary" in usage(doc)["reason"]
    assert "selfcheck/probe-failed" in {f["rule"] for f in doc["findings"]}
    assert doc["run"]["surface"]["fleet"] == "partial"
    assert not [f for f in dead(doc).values() if f["confidence"] == "confirmed"]


def test_every_text_form_of_a_neighbour_caps_dead(tmp_path: Path) -> None:
    """§9.4/§9.8: any role, any extension; each form alone yields P5 (review r1 M5)."""
    scope = {f"t{i}.sh": "#!/bin/sh\n" for i in range(1, 5)}
    scope["pkg/__init__.py"] = ""
    scope["pkg/mod.py"] = "x = 1\n"
    wide = {
        "tests/test_x.py": "# regression for t1.sh\n",
        "lib/a.ex": 'System.cmd("t2.sh", [])\n',
        "notes.md": "```zsh\n./t3.sh\n```\n",
        ".github/workflows/w.yml": (
            "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - run: bash "$WS/devtools/t4.sh"\n'
        ),
        "justfile": "go:\n    python -m pkg.mod\n",
    }
    ws = fleet_ws(
        tmp_path, scope_files=scope, neighbours={**NEIGHBOURS_S2, "wide": wide}
    )
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    (doc,) = reports(ws)
    found = dead(doc)
    expected = {
        "file:t1.sh": "wide:tests/test_x.py",
        "file:t2.sh": "wide:lib/a.ex",
        "file:t3.sh": "wide:notes.md",
        "file:t4.sh": "wide:.github/workflows/w.yml",
        "file:pkg/mod.py": "wide:justfile",
    }
    for anchor, source in expected.items():
        assert found[anchor]["confidence"] == "likely", anchor
        assert {"kind": "mentioned-in", "detail": source} in found[anchor]["evidence"]


def test_fleet_ignores_scope_corpus_exclude_and_roles(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "cfg.toml").write_text(
        '[corpus]\nexclude = ["**/*.md"]\n[roles]\ndiagnostic-output = [".github/**"]\n'
    )
    assert main(args(ws, sched(tmp_path), "--fleet", config="cfg.toml")) == 0
    (doc,) = reports(ws)
    attest = dead(doc)["file:attest.sh"]
    assert {"kind": "mentioned-in", "detail": "docs-nb:TODO.md"} in attest["evidence"]
    # scope [roles] would silence nb's workflow if it leaked into the fleet
    assert doc["graph"]["devtools"]["file:check.py"]["fleet_only"] is True


def test_partial_fleet_keeps_p1_exit_code_and_fates(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "nb" / "dirty.md").write_text("x\n")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0  # no exit change
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "partial"
    assert dead(doc)["file:orphan.py"]["confidence"] == "likely"
    (partial,) = [f for f in doc["findings"] if f["rule"] == "selfcheck/fleet-partial"]
    assert partial["anchor"] == "probe:devtools#fleet"
    (ws / "nb" / "dirty.md").unlink()
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    last = reports(ws)[-1]
    gone = {g["anchor"]: g["status"] for g in last["delta"]["gone"]}
    assert gone["probe:devtools#fleet"] == "resolved"


def _gone(ws: Path) -> dict[str, str]:
    return {g["anchor"]: g["status"] for g in reports(ws)[-1]["delta"]["gone"]}


def test_fleet_fate_without_fleet_is_not_rechecked(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "nb" / "dirty.md").write_text("x\n")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    (ws / "nb" / "dirty.md").unlink()
    assert main(args(ws, sched(tmp_path))) == 0  # the fleet was not read
    assert _gone(ws)["probe:devtools#fleet"] == "not-rechecked"


def test_fleet_fate_needs_an_ok_probe(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "nb" / "dirty.md").write_text("x\n")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    (ws / "nb" / "dirty.md").unlink()
    commit(
        ws / "devtools", {"scripts/review/PIN": "garbage\n"}, date="2026-09-25T00:00:00"
    )
    git(ws / "devtools", "update-ref", "refs/remotes/origin/main", "HEAD")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 2  # usage-graph partial
    assert _gone(ws)["probe:devtools#fleet"] == "not-rechecked"


def test_fleet_composition_enters_the_key(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    synced(make_repo(ws / "extra", {"e.md": "e\n"}))
    manifest = ws / "umbrella" / "m.toml"
    commit(
        ws / "umbrella",
        {"m.toml": manifest.read_text() + '[tools.extra]\ngit_dir = "extra"\n'},
        date="2026-09-25T00:00:00",
    )
    git(ws / "umbrella", "update-ref", "refs/remotes/origin/main", "HEAD")
    assert main(args(ws, sched(tmp_path), "--fleet")) == 0
    first, last = reports(ws)
    assert last["run"]["surface"]["fleet"] == "complete"
    assert usage(first)["key"] != usage(last)["key"]


def test_other_scope_repos_are_fleet_for_each_other(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    code = main(
        args(ws, sched(tmp_path), "--fleet", "--repo", "devtools", "--repo", "nb")
    )
    assert code == 0
    (doc,) = reports(ws)
    assert doc["run"]["scope"] == ["devtools", "nb"]
    rows = [r["name"] for r in doc["run"]["surface"]["fleet_repos"]]
    assert rows == ["docs-nb", "umbrella"]  # display: repos outside scope
    check = doc["graph"]["devtools"]["file:check.py"]
    assert check["class"] == "live" and check["fleet_only"] is True  # nb → devtools
    assert "file:check.py" not in dead(doc)


def test_surface_fleet_is_the_worst_over_scope(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "devtools" / "wip.md").write_text("x\n")  # devtools dirty: stale for nb
    code = main(
        args(ws, sched(tmp_path), "--fleet", "--repo", "devtools", "--repo", "nb")
    )
    assert code == 0
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "partial"
    # devtools' own fleet (U − {devtools}) is complete: its dead may be confirmed
    assert dead(doc)["file:orphan.py"]["confidence"] == "confirmed"
    (partial,) = [f for f in doc["findings"] if f["rule"] == "selfcheck/fleet-partial"]
    assert (partial["owner_repo"], partial["text_key"]) == ("nb", "devtools:stale")


def test_fleet_is_not_complete_when_usage_graph_did_not_run(tmp_path: Path) -> None:
    """Final review M2: without usage-graph the fleet canary never ran, so the
    report must not claim completeness (§9.6 п.1)."""
    ws = fleet_ws(tmp_path)
    argv = args(ws, sched(tmp_path), "--fleet")
    argv[argv.index("usage-graph")] = "ast-dup"
    assert main(argv) == 0
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "partial"
    assert "complete относительно" not in markdown(ws, doc)
