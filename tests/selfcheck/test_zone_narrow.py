"""zone-narrow (spec rev 5.8): caller-dir zone → cap P7; [[operator]] roots (§2.3, §3.2.3, §3.2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.config import ConfigError, load_config
from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import NodeFacts, Surface, dead_confidence
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.model import Confidence
from selfcheck.probes.base import (
    ProbeResult,
    RepoTarget,
    _analyzer_config_hash,
    canary_files,
    run_probe,
)
from selfcheck.roles import role_of
from selfcheck.run import main
from tests.selfcheck.helpers import (
    NOW,
    ago,
    fleet_ws,
    make_repo,
    plist_dir,
    workspace,
    write,
)

FULL = Surface("complete", "/sched", [])
L = Confidence.LIKELY

RUNNER = (
    "import subprocess\n\ndef run(cfg):\n"
    "    subprocess.run([cfg.tool_path, '--json'])\n"
)
SCRIPT = 'if __name__ == "__main__":\n    pass\n'


def usage(
    tmp: Path, files: dict[str, str], operator: tuple[str, ...] = ()
) -> ProbeResult:
    repo = make_repo(tmp / "repo", files, date=ago(90))
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([USAGE_GRAPH]))
    target = RepoTarget(
        "repo",
        repo,
        copy,
        frozenset({"python"}),
        corpus,
        EnvInfo("no-env"),
        fleet="complete",
        sched_dir=tmp,
        now=NOW,
        operator=operator,
    )
    try:
        return run_probe(USAGE_GRAPH, target, tmp / "run" / "work")
    finally:
        release(copy)


def dead(res: ProbeResult) -> dict[str, object]:
    return {f.anchor: f for f in res.findings if f.rule.startswith("usage-graph/dead")}


def caps(finding) -> set[str]:
    return {e["detail"] for e in finding.evidence if e["kind"] == "cap"}


@pytest.mark.parametrize(
    ("row", "facts", "expected"),
    [
        ("D10", NodeFacts("orphan", False, True, True, 90, False), (None, [])),
        (
            "D10b",
            NodeFacts("orphan", False, False, True, 90, False, dir_zone=True),
            (L, ["P7"]),
        ),
        (
            "P7+P5",
            NodeFacts("orphan", False, False, True, 90, True, dir_zone=True),
            (L, ["P5", "P7"]),
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_p7_matrix(row, facts, expected) -> None:
    assert dead_confidence(facts, FULL) == expected


def test_zone_kind_is_recorded(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "suffix.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
            "local.sh": "#!/bin/sh\n",
            "tools/runner.py": RUNNER,
            "tools/tool.py": SCRIPT,
        },
    )
    files = ["suffix.sh", "local.sh", "tools/runner.py", "tools/tool.py"]
    g = build_graph(files, tmp_path, role_of, repo_name="r", sched_dir=None)
    kinds = {z.caller.path: z.suffix for z in g.zones}
    assert kinds == {"suffix.sh": True, "tools/runner.py": False}


def test_caller_dir_zone_caps_instead_of_exempting(tmp_path: Path) -> None:
    res = usage(
        tmp_path,
        {
            "tools/runner.py": RUNNER,
            "tools/tool.py": SCRIPT,
            "solo/alone.py": SCRIPT,
            "suffix.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
            "kit/local.sh": "#!/bin/sh\n",
        },
    )
    found = dead(res)
    tool = found["file:tools/tool.py"]
    assert tool.confidence is L and "P7" in caps(tool)  # visible, never confirmed
    assert found["file:solo/alone.py"].confidence is Confidence.CONFIRMED  # no zone
    assert "file:kit/local.sh" not in found  # suffix zone still exempts (D10)
    zone = [f for f in res.findings if f.rule == "usage-graph/unresolved-exec"]
    assert {f.anchor for f in zone} == {"file:tools/runner.py", "file:suffix.sh"}


def test_operator_entry_is_a_root(tmp_path: Path) -> None:
    files = {"tools/runner.py": RUNNER, "tools/tool.py": SCRIPT, "gen.py": SCRIPT}
    res = usage(tmp_path, files, operator=("tools/tool.py", "gen.py"))
    assert not {"file:tools/tool.py", "file:gen.py"} & set(dead(res))  # D17
    graph = res.extra["graph"]
    assert (
        graph["file:gen.py"]["root"] is True
    )  # a root everywhere, not only in classify
    assert not [f for f in res.findings if f.rule == "selfcheck/operator-missing"]


def test_operator_on_a_missing_path_is_a_finding(tmp_path: Path) -> None:
    res = usage(tmp_path, {"a.py": SCRIPT}, operator=("gone.sh",))
    (missing,) = [f for f in res.findings if f.rule == "selfcheck/operator-missing"]
    assert (missing.anchor, missing.text_key, missing.severity, missing.category) == (
        "probe:repo#usage-graph",
        "gone.sh",
        "medium",
        "selfcheck",
    )
    assert missing.suggestion  # says what to do: drop the entry or restore the file


def test_doc_only_dead_hints_at_operator(tmp_path: Path) -> None:
    res = usage(
        tmp_path,
        {
            "README.md": "| `tool.sh` | генератор |\n",
            "tool.sh": "#!/bin/sh\n",
            "orphan.sh": "#!/bin/sh\n",
        },
    )
    found = dead(res)
    finding = found["file:tool.sh"]
    assert {"kind": "class", "detail": "doc-only"} in finding.evidence
    assert finding.confidence is Confidence.CANDIDATE  # still a candidate (§3.2.5)
    assert "[[operator]]" in finding.suggestion  # a hint, not an exemption
    assert "[[operator]]" not in found["file:orphan.sh"].suggestion  # only doc-only


def test_operator_config_parsing(tmp_path: Path) -> None:
    good = tmp_path / "good.toml"
    good.write_text(
        '[[operator]]\nrepo = "devtools"\npath = "a.sh"\nreason = "ручной гейт"\n'
    )
    config = load_config(good)
    assert [(o.repo, o.path, o.reason) for o in config.operator] == [
        ("devtools", "a.sh", "ручной гейт")
    ]
    for body in (
        '[[operator]]\nrepo = "devtools"\npath = "a.sh"\n',  # no reason
        '[[operator]]\npath = "a.sh"\nreason = "r"\n',  # no repo
    ):
        bad = tmp_path / "bad.toml"
        bad.write_text(body)
        with pytest.raises(ConfigError):
            load_config(bad)


def test_operator_enters_the_analyzer_config_hash(tmp_path: Path) -> None:
    def target(operator: tuple[str, ...]) -> RepoTarget:
        return RepoTarget(
            "r",
            tmp_path,
            tmp_path,
            frozenset(),
            (),
            EnvInfo("no-env"),
            operator=operator,
        )

    assert _analyzer_config_hash(target(())) != _analyzer_config_hash(target(("a.sh",)))


def test_usage_graph_logic_version_is_5() -> None:
    assert USAGE_GRAPH.logic_version == 5  # BOM-aware reads, make calls (#411)


def test_run_reads_operator_from_config(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "sc.toml").write_text(
        '[[operator]]\nrepo = "devtools"\npath = "orphan.py"\n'
        'reason = "запускает человек"\n'
    )
    argv = [
        "--workspace", str(ws), "--manifest", str(ws / "m.toml"),
        "--out", str(ws / "out"), "--config", str(ws / "sc.toml"),
        "--probe", "usage-graph",
    ]  # fmt: skip
    assert main(argv) == 0
    import json

    (run,) = list((ws / "out").iterdir())
    doc = json.loads((run / "report.json").read_text())
    assert not [f for f in doc["findings"] if f["anchor"] == "file:orphan.py"]
    (ws / "bad.toml").write_text('[[operator]]\nrepo = "devtools"\nreason = "r"\n')
    argv[argv.index(str(ws / "sc.toml"))] = str(ws / "bad.toml")
    assert main(argv) == 4
    (ws / "typo.toml").write_text(
        '[[operator]]\nrepo = "devtols"\npath = "orphan.py"\nreason = "r"\n'
    )
    argv[argv.index(str(ws / "bad.toml"))] = str(ws / "typo.toml")
    assert main(argv) == 4  # an unknown repo would silently disable the entry


def test_operator_entries_apply_to_their_repo_only(tmp_path: Path) -> None:
    """Review r1 M1: entries of devtools neither root nor miss in another repo."""
    import json

    ws = fleet_ws(tmp_path, neighbours={"nb": {"orphan.py": SCRIPT}})
    (ws / "sc.toml").write_text(
        '[[operator]]\nrepo = "devtools"\npath = "orphan.py"\nreason = "r"\n'
        '[[operator]]\nrepo = "devtools"\npath = "attest.sh"\nreason = "r"\n'
    )
    argv = [
        "--workspace", str(ws), "--manifest", str(ws / "umbrella" / "m.toml"),
        "--out", str(ws / "out"), "--config", str(ws / "sc.toml"),
        "--probe", "usage-graph", "--sched-dir", str(plist_dir(tmp_path, ["/x"])),
        "--repo", "devtools", "--repo", "nb",
    ]  # fmt: skip
    assert main(argv) == 0
    (run,) = list((ws / "out").iterdir())
    doc = json.loads((run / "report.json").read_text())
    dead_by = {
        (f["owner_repo"], f["anchor"])
        for f in doc["findings"]
        if f["category"] == "dead"
    }
    assert ("devtools", "file:orphan.py") not in dead_by  # rooted in its repo
    assert ("nb", "file:orphan.py") in dead_by  # same name elsewhere: not rooted
    assert not [f for f in doc["findings"] if f["rule"] == "selfcheck/operator-missing"]


@pytest.mark.parametrize(
    "body",
    [
        '[operator]\nrepo = "devtools"\npath = "a.sh"\nreason = "r"\n',  # single brackets
        'operator = "a.sh"\n',
        '[allow]\nanchor = "file:x"\nreason = "r"\nuntil = 2027-01-01\n',
        "operator = [1, 2]\n",
    ],
)
def test_malformed_tables_are_config_errors(tmp_path: Path, body: str) -> None:
    """Final review m1: a typo in table syntax is exit 4, not a traceback."""
    bad = tmp_path / "bad.toml"
    bad.write_text(body)
    with pytest.raises(ConfigError):
        load_config(bad)
