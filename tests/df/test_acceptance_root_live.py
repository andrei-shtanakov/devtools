"""Live install of profile `core` from the real umbrella manifest (stage-1
closing item, spec §10; TODO `df-installer-stage1-live`).

The root world of `tests/df/root_world.py` (a fresh `<R>`, the root uv copy
at the approved path, the approved `git`/`tar`) without the fixture pin:
`install._profile` is not substituted, so the manifest step fetches
`CORE_MANIFEST` and the build fetches each member by its pin from GitHub.
The argv is the README's first-install command (`documented_argv`), only the
manifest SHA and the uv path (the world's approved copy) filled in; it runs
as bootstrap runs it, in process, over the layout of `<R>` (R87).

After the install: the installed command's `doctor --json` (a real process
of root) reaches level 1 with every check `ok`, and every console
entrypoint of every member answers `--help` with exit 0 as `df-build`
without network (release code never runs as root). The journal, the doctor
report and the smoke results are CI evidence (`live/`, `journals/`).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from df import cli, doctor
from df import platform as plat
from df.boundary import expected_entrypoints
from df.journal import read_journal
from df.release import RELEASE_JSON
from tests.df.conftest import stage1_out
from tests.df.root_world import (
    CORE_MANIFEST,
    CORE_PROFILE,
    ROLE_ENV,
    FixtureRepo,
    RootWorld,
    core_profile,
    infra,
    install_once,
    make_world,
    teardown_world,
    write_evidence,
)
from tests.df.test_cli import SHA, documented_argv

PLATFORM = plat.detect_platform()
#: The README's uv path, replaced by the world's approved copy.
README_UV = "uv=/usr/local/libexec/darkfactory/bin/uv"
#: Kept per entrypoint run in the evidence.
OUTPUT_TAIL = 400


@pytest.fixture()
def live_world(
    acceptance_top: Path, fixture_repo: FixtureRepo, control_url: str
) -> Iterator[RootWorld]:
    """A fresh root world whose profile comes from the real manifest; torn
    down like `root_world`, its journal kept as `journals/live-core.jsonl`."""
    world = make_world(acceptance_top, fixture_repo, control_url)
    yield world
    out = stage1_out()
    teardown_world(world, None if out is None else out / "journals/live-core.jsonl")


def live_argv(uv: Path) -> list[str]:
    """The README's `init` argv with `CORE_MANIFEST`'s SHA and `uv`."""
    repo, sha = CORE_MANIFEST
    argv = documented_argv()
    assert argv.count(SHA) == 1 and argv.count(README_UV) == 1, argv
    assert argv[argv.index("--manifest-repo") + 1] == repo, argv
    assert argv[argv.index("--profile") + 1] == CORE_PROFILE, argv
    return [sha if a == SHA else f"uv={uv}" if a == README_UV else a for a in argv]


@pytest.mark.root
@pytest.mark.network  # the manifest, the members and their wheels
def test_live_core_install(live_world: RootWorld) -> None:
    """`init` of profile `core` at `CORE_MANIFEST` commits; the release holds
    every member at its manifest pin; `darkfactory doctor --json` reaches
    level 1 with every check `ok`; each member's entrypoints run `--help`."""
    world, facts = live_world, {"platform": PLATFORM}
    try:
        _install_and_check(world, facts)
    finally:
        text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
        write_evidence(stage1_out(), f"live/{PLATFORM}.json", text + "\n")


def _install_and_check(world: RootWorld, facts: dict[str, Any]) -> None:
    profile = core_profile()
    pins = {p.name: p.sha for p in profile.repos}
    facts["manifest"] = {"repo": CORE_MANIFEST[0], "sha": CORE_MANIFEST[1]}
    facts["pins"] = pins
    argv = live_argv(world.uv)
    facts["argv"] = argv
    started = time.monotonic()
    installed = install_once(world, argv)
    facts["install_seconds"] = round(time.monotonic() - started, 1)
    facts["journal_steps"] = _journal_summary(world)
    facts["release_id"] = installed.release_id
    record = json.loads((installed.release / RELEASE_JSON).read_text())
    facts["release"] = {
        k: record[k] for k in ("manifest_repo", "manifest_sha", "profile")
    }
    facts["release_repos"] = record["repos"]
    assert (record["manifest_repo"], record["manifest_sha"]) == CORE_MANIFEST
    assert record["profile"] == CORE_PROFILE
    assert {r["name"]: r["sha"] for r in record["repos"]} == pins, record["repos"]
    report = _doctor(world, facts)
    not_ok = [c for c in report["checks"] if c["status"] != doctor.OK]
    assert report["level1"] == "reached" and not_ok == [], not_ok
    smoke = _smoke(world, installed.release, list(pins))
    facts["smoke"] = smoke
    assert sum(len(m) for m in smoke.values()) > 0, smoke
    failed = {
        f"{name}/{ep}": run
        for name, runs in smoke.items()
        for ep, run in runs.items()
        if run["code"] != 0
    }
    assert failed == {}, failed


def _journal_summary(world: RootWorld) -> list[list[str]]:
    """`[step, event]` of every journal row, in order."""
    rows = read_journal(world.layout.journal)
    return [[str(r["step"]), str(r["event"])] for r in rows]


def _doctor(world: RootWorld, facts: dict[str, Any]) -> dict[str, Any]:
    """The installed command's `doctor --json` (a real process of root),
    kept whole as `live/<platform>-doctor.json`; exit `EXIT_REACHED`,
    nothing on stderr."""
    done = world.command(["doctor", "--json"])
    write_evidence(stage1_out(), f"live/{PLATFORM}-doctor.json", done.stdout)
    facts["doctor"] = {"code": done.returncode, "stderr": done.stderr[-2000:]}
    assert done.returncode == doctor.EXIT_REACHED and done.stderr == "", facts
    report = json.loads(done.stdout)
    facts["doctor"]["level1"] = report["level1"]
    facts["doctor"]["checks"] = {c["id"]: c["status"] for c in report["checks"]}
    return report


def _smoke(
    world: RootWorld, release: Path, names: list[str]
) -> dict[str, dict[str, Any]]:
    """`<release>/venv/<member>/bin/<entrypoint> --help` for every declared
    entrypoint, as `df-build` from its home without network."""
    home = world.layout.build_home
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        out[name] = {}
        for ep in sorted(expected_entrypoints(release / "src" / name)):
            argv = [str(release / "venv" / name / "bin" / ep), "--help"]
            done = plat.run_as(
                plat.BUILD_USER,
                argv,
                env=ROLE_ENV,
                cwd=home,
                network=False,
                timeout=120,
            )
            if not done.launched:
                infra(f"{plat.BUILD_USER} не запущен: {argv}: {done}")
            out[name][ep] = {
                "code": done.returncode,
                "stdout_head": _text(done.stdout)[:OUTPUT_TAIL],
                "stderr_tail": _text(done.stderr)[-OUTPUT_TAIL:],
            }
    return out


def _text(data: bytes) -> str:
    return re.sub(r"\s+$", "", data.decode("utf-8", "replace"))


def test_live_argv_is_the_readme_command() -> None:
    """Only the manifest SHA and the uv path differ from the README argv;
    `cli` builds the `core` configuration of `CORE_MANIFEST` from it."""
    argv = live_argv(Path("/approved/uv"))
    readme = documented_argv()
    changed = [(a, b) for a, b in zip(readme, argv, strict=True) if a != b]
    assert changed == [(SHA, CORE_MANIFEST[1]), (README_UV, "uv=/approved/uv")]
    cfg = cli._init_config(cli._parse(argv))  # noqa: SLF001 - the `init` parser
    assert (cfg.manifest_repo, cfg.manifest_sha, cfg.profile) == (
        *CORE_MANIFEST,
        CORE_PROFILE,
    )
