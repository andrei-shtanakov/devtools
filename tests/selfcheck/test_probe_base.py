"""Task 5 — probe contract: statuses, canary, coverage, instrument findings (§4)."""

from __future__ import annotations

import json
import os
import subprocess
import time
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
from selfcheck.probes.common import config_hash, copy_paths, line_finding, rel_path
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
    fields: dict[str, object] = {
        "name": "fake",
        "languages": frozenset({"python"}),
        "input_mode": "files",
        "select": lambda t: tuple(p for p in t.corpus if p.endswith(".py")),
        "canary": CANARY,
        "binary": str(tool),
        "version_range": ((1, 0), (2, 0)),
        "normal_codes": frozenset({0, 1}),
        "argv": copy_paths,
        "parse": parse_fake,
        "timeout": 5,
        # a first exec of a freshly written fake tool can stall (macOS checks
        # it); a 2 s version call then timed out in unrelated tests (#425)
        "version_timeout": 60,
    }
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
    slow = fake_tool(tmp_path / "b", version_sleep=5)
    res = run(spec_for(slow, version_timeout=2), target, tmp_path)
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

    base = {
        "languages": frozenset({"any"}),
        "input_mode": "files",
        "select": lambda t: t.corpus,
        "canary": CANARY,
        "logic_version": 1,
    }
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


# ---- final review (2026-09-26): no exception from any probe phase escapes ------


@pytest.mark.parametrize("phase", ["parse", "expected_files", "runner"])
def test_any_exception_is_a_probe_status(phase: str, target, tmp_path) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f"boom in {phase}")

    tool = fake_tool(tmp_path / "b")
    overrides: dict[str, object] = {"parse": boom} if phase == "parse" else {}
    if phase == "expected_files":
        overrides["expected_files"] = boom
    spec = spec_for(tool, **overrides)
    runner = boom if phase == "runner" else subprocess.run
    res = run_probe(spec, target, tmp_path / "run" / "work", which=which, runner=runner)
    assert res.status is ProbeStatus.FAILED and "boom" in res.reason


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_the_whole_process_group(target, tmp_path) -> None:
    """#408: npx → node, uvx → semgrep-core — the timed-out tool's children
    must not outlive the probe, and the probe must not wait for them (they
    hold its stdout). A fake tool starts ``sleep`` and hangs; both sleep far
    longer than the timeout, so a surviving or awaited child shows. #425: the
    old Python tool sometimes hit the 2 s *version* timeout (first exec of a
    fresh script) and the test took that for the run's; now the version call
    has room and must pass, and the pid is written by rename, never empty."""
    pid_file = tmp_path / "grandchild.pid"
    tool = tmp_path / "hang" / "fake-tool"
    tool.parent.mkdir()
    tool.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo "fake 1.2.3"; exit 0; fi\n'
        f"sleep 120 & echo $! > {pid_file}.tmp && mv {pid_file}.tmp {pid_file}\n"
        "sleep 120\n"
    )
    tool.chmod(0o755)
    # the version call has room (spec_for); the timeout below must be the run's
    res = run(spec_for(tool, timeout=5), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "timeout")
    assert res.tool_version == "1.2.3"  # the version call passed: run timed out
    assert res.duration < 60  # seconds normally; a surviving/awaited child: 120+
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    alive = _alive(grandchild)
    if alive:
        os.kill(grandchild, 9)
    assert not alive


def test_any_interruption_kills_the_process_group(tmp_path, monkeypatch) -> None:
    """Review of #424: the tool runs in its own session, so Ctrl-C reaches only
    Python — a KeyboardInterrupt (or any error) while waiting must take the
    group down too, not leave the tool running and ``__exit__`` waiting."""
    from selfcheck.probes.base import run_group

    pid_file = tmp_path / "tool.pid"
    real = subprocess.Popen.communicate
    calls = {"n": 0}

    def interrupted(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            deadline = time.monotonic() + 5
            while not pid_file.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("the tool never wrote its pid")
                time.sleep(0.05)
            raise KeyboardInterrupt
        return real(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    # write-then-rename: the pid file never exists empty (review of #424, #425)
    script = f"echo $$ > {pid_file}.tmp && mv {pid_file}.tmp {pid_file}; sleep 30"
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_group(["sh", "-c", script], capture_output=True, text=True, timeout=60)
    assert time.monotonic() - started < 10
    assert not _alive(int(pid_file.read_text()))


def test_own_files_content_enters_config_hash(target, tmp_path) -> None:
    """Правка собственного файла правил пробы меняет ключ (#433): иначе
    исчезнувшая находка читалась бы `resolved`, хотя код репо не менялся."""
    tool = fake_tool(tmp_path / "b")
    rules = tmp_path / "rules.yml"
    hashes = []
    for i, text in enumerate(("rule: a\n", "rule: a\n", "rule: b\n")):
        rules.write_text(text)
        spec = spec_for(tool, own_files=(rules,))
        hashes.append(run(spec, target, tmp_path / f"o{i}").config_hash)
    assert hashes[0] == hashes[1]
    assert hashes[1] != hashes[2]


def test_without_own_files_config_hash_is_unchanged(target, tmp_path) -> None:
    """Пробы без собственных файлов сохраняют прежний хэш побайтово —
    история их дельты не рвётся."""
    spec = spec_for(fake_tool(tmp_path / "b"), config_files=("ruff.toml",))
    res = run(spec, target, tmp_path)
    assert res.config_hash == config_hash(target.copy, ("ruff.toml",))


def test_llm_sites_declares_its_rule_files() -> None:
    from selfcheck.llm import ENV_PATH, LLM_SITES, RULES_PATH

    assert set(LLM_SITES.own_files) == {RULES_PATH, ENV_PATH}
