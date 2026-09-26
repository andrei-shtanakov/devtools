"""Task 5 — probe contract: statuses, canary, coverage, instrument findings (§4)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.model import Location
from selfcheck.probes.base import (
    Canary,
    ParseResult,
    ProbeCtx,
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    instrument_findings,
    run_probe,
)
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from tests.selfcheck.helpers import fake_tool, make_repo

CANARY = Canary(
    ".selfcheck-canary/fake/c.py",
    "x = 1\n",
    "fake/CAN",
    "file:.selfcheck-canary/fake/c.py",
)


def parse_fake(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    return ParseResult(
        [
            line_finding(
                ctx,
                f"fake/{i['code']}",
                rel_path(ctx, i["path"]),
                i["line"],
                category="bug",
                severity="low",
            )
            for i in data["items"]
        ],
        processed_paths=[rel_path(ctx, p) for p in data["processed"]],
    )


def spec_for(tool: Path, **overrides: object) -> ProbeSpec:
    fields: dict[str, object] = dict(
        name="fake",
        languages=frozenset({"python"}),
        input_mode="files",
        select=lambda t: tuple(p for p in t.corpus if p.endswith(".py")),
        canary=CANARY,
        binary=str(tool),
        version_range=((1, 0), (2, 0)),
        normal_codes=frozenset({0, 1}),
        argv=copy_paths,
        parse=parse_fake,
        timeout=5,
        version_timeout=2,
    )
    fields.update(overrides)
    return ProbeSpec(**fields)  # type: ignore[arg-type]


def which(binary: str) -> str | None:
    return binary if Path(binary).exists() else None


@pytest.fixture
def target(tmp_path: Path):
    repo = make_repo(tmp_path / "repo", {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec_for(Path("t"))]))
    yield RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env")
    )
    release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    return run_probe(spec, target, tmp / "run" / "work", which=which)


def test_findings_with_nonzero_normal_code_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b")), target, tmp_path)
    assert (res.status, res.canary, res.exit_code) == (ProbeStatus.OK, "hit", 1)
    assert sorted(f.locations[0] for f in res.findings) == [
        Location("a.py", 1),
        Location("b.py", 1),
    ]
    assert res.tool_version == "1.2.3"
    assert res.coverage["passed"] == ["a.py", "b.py"]


def test_clean_nonempty_corpus_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", emit_repo=False)), target, tmp_path)
    assert res.status is ProbeStatus.OK and res.findings == []


def test_canary_missed(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", emit_canary=False)), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


def test_canary_right_rule_wrong_anchor_is_missed(target, tmp_path) -> None:
    other = Canary(CANARY.relpath, CANARY.content, "fake/CAN", "func:elsewhere::f")
    res = run(spec_for(fake_tool(tmp_path / "b"), canary=other), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


def test_canary_suppressed_by_config(target, tmp_path) -> None:
    spec = spec_for(
        fake_tool(tmp_path / "b", emit_canary=False), config_suppresses=lambda ctx: True
    )
    assert run(spec, target, tmp_path).reason == "canary-suppressed-by-config"


def test_exit_code_outside_normal_set(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", code=7)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("exit-code")


def test_unparsable_output(target, tmp_path) -> None:
    res = run(
        spec_for(fake_tool(tmp_path / "b", stdout="not json", code=0)), target, tmp_path
    )
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("unparsable")


def test_reported_coverage_missing_input_is_partial(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path / "b", unprocessed="b.py"), coverage="reported")
    res = run(spec, target, tmp_path)
    assert res.status is ProbeStatus.PARTIAL
    assert res.coverage["unprocessed"] == ["b.py"]


def test_reported_zero_processed_fails(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path / "b", unprocessed="*"), coverage="reported")
    res = run(spec, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "zero-processed")


def test_missing_binary_unavailable(target, tmp_path) -> None:
    assert (
        run(spec_for(tmp_path / "nope"), target, tmp_path).status
        is ProbeStatus.UNAVAILABLE
    )


def test_version_out_of_range_unavailable(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", version="3.0.0")), target, tmp_path)
    assert res.status is ProbeStatus.UNAVAILABLE and "3.0.0" in res.reason


def test_version_timeout_fails_without_raising(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", version_sleep=5)), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "timeout")


def test_run_timeout_fails_only_that_probe(target, tmp_path) -> None:
    slow = run(
        spec_for(fake_tool(tmp_path / "slow", sleep=10), timeout=1), target, tmp_path
    )
    fast = run(spec_for(fake_tool(tmp_path / "fast"), name="fake2"), target, tmp_path)
    assert (slow.status, slow.reason) == (ProbeStatus.FAILED, "timeout")
    assert fast.status is ProbeStatus.OK


def test_write_to_corpus_is_visible_failure(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", write_copy=True)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("write-to-corpus")


def test_no_inputs_and_language_skip(target, tmp_path) -> None:
    tool = fake_tool(tmp_path / "b")
    none = run(spec_for(tool, select=lambda t: ()), target, tmp_path)
    shell_only = RepoTarget(
        "repo", target.source, target.copy, frozenset(), target.corpus, target.env
    )
    py = run(spec_for(tool, name="py"), shell_only, tmp_path)
    anyp = run(
        spec_for(tool, name="any", languages=frozenset({"any"})), shell_only, tmp_path
    )
    assert (none.status, none.reason, none.canary) == (
        ProbeStatus.SKIPPED,
        "no-inputs",
        None,
    )
    assert (py.status, py.reason) == (ProbeStatus.SKIPPED, "language")
    assert anyp.status is ProbeStatus.OK


def test_select_error_is_a_status_not_an_exception(target, tmp_path) -> None:
    def broken(t: RepoTarget) -> tuple[str, ...]:
        raise OSError("disk")

    res = run(spec_for(fake_tool(tmp_path / "b"), select=broken), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("select-error")


def test_relative_paths_are_rejected(target, tmp_path) -> None:
    relative = RepoTarget(
        "repo",
        target.source,
        Path("run/src/repo"),
        target.languages,
        target.corpus,
        target.env,
    )
    with pytest.raises(ValueError):
        run_probe(
            spec_for(fake_tool(tmp_path / "b")), relative, tmp_path / "w", which=which
        )
    with pytest.raises(ValueError):
        run_probe(spec_for(fake_tool(tmp_path / "b")), target, Path("w"), which=which)


def test_internal_analyzer_contract(target, tmp_path) -> None:
    def good(ctx: ProbeCtx) -> ParseResult:
        return ParseResult(
            [
                line_finding(
                    ctx, "fake/CAN", CANARY.relpath, 1, category="bug", severity="low"
                )
            ]
        )

    def boom(ctx: ProbeCtx) -> ParseResult:
        raise RuntimeError("x")

    base = dict(
        languages=frozenset({"any"}),
        input_mode="files",
        select=lambda t: t.corpus,
        canary=CANARY,
        logic_version=1,
    )
    ok = run_probe(ProbeSpec(name="int", analyze=good, **base), target, tmp_path / "w")
    bad = run_probe(
        ProbeSpec(name="int2", analyze=boom, **base), target, tmp_path / "w"
    )
    assert ok.status is ProbeStatus.OK
    assert (bad.status, bad.reason) == (
        ProbeStatus.FAILED,
        "analyzer-error: RuntimeError('x')",
    )
    assert ok.tool_version is not None and ok.tool_version.endswith("/logic 1")
    variants = [
        RepoTarget(
            "repo",
            target.source,
            target.copy,
            target.languages,
            target.corpus,
            target.env,
            roles=roles,
            corpus_exclude=exclude,
        )
        for roles, exclude in [
            ({}, ()),
            ({"test": ("qa/**",)}, ()),
            ({}, ("vendor/**",)),
        ]
    ]
    same = ProbeSpec(name="h", analyze=good, **base)
    hashes = [
        run_probe(same, t, tmp_path / f"w{i}").config_hash
        for i, t in enumerate([*variants, variants[0]])
    ]
    assert hashes[0] == hashes[3] and len(set(hashes[:3])) == 3


def test_instrument_findings() -> None:
    rows = [
        ProbeResult("a", "r", ProbeStatus.FAILED, "timeout"),
        ProbeResult("b", "r", ProbeStatus.PARTIAL, "per-file problems"),
        ProbeResult("c", "r", ProbeStatus.UNAVAILABLE, "c not found"),
        ProbeResult("d", "r", ProbeStatus.OK),
        ProbeResult("e", "r", ProbeStatus.SKIPPED),
    ]
    found = {(f.rule, f.severity) for f in instrument_findings(rows)}
    assert found == {
        ("selfcheck/probe-failed", "high"),
        ("selfcheck/probe-partial", "medium"),
        ("selfcheck/probe-unavailable", "medium"),
    }
