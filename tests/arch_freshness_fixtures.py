"""arch_freshness_fixtures — фикстурный polyrepo-workspace для тестов сенсора.

Строит во временном каталоге: bare-репо canon (играет GitHub-remote prograph),
его клон prograph внутри workspace, и steward с вендоренными копиями + PIN +
evidence-парой WS-005. Все времена задаются снаружи — реальных часов здесь нет.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_INTENDED = b'{"$id": "intended-graph/v1", "type": "object"}\n'
SCHEMA_REPORT = b'{"$id": "conformance-report/v1", "type": "object"}\n'
UPSTREAM_DIRS = {
    "intended-graph": "contracts/intended-graph/v1",
    "conformance-report": "contracts/conformance-report/v1",
}
VENDORED_DIRS = {
    "intended-graph": "contracts/prograph-intended-graph/v1",
    "conformance-report": "contracts/prograph-conformance-report/v1",
}
EVIDENCE_DIR = "workstreams/WS-005-gate-verdicts/spec"


def git(cwd: Path, *args: str, at_time: datetime | None = None) -> str:
    env = os.environ.copy()
    if at_time is None and "commit" in args:
        # For commit commands without explicit time, try to use last commit time + 1 sec
        code = subprocess.run(
            ["git", "-C", str(cwd), "log", "-1", "--format=%cI"],
            capture_output=True, text=True,
        ).returncode
        if code == 0:
            last_output = subprocess.run(
                ["git", "-C", str(cwd), "log", "-1", "--format=%cI"],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if last_output:
                try:
                    last_time = datetime.fromisoformat(
                        last_output.replace("Z", "+00:00")
                    )
                    at_time = last_time + timedelta(seconds=1)
                except (ValueError, AttributeError):
                    pass
    if at_time:
        iso_time = at_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        iso_time = iso_time.replace("T", " ").replace("Z", "+00:00")
        env["GIT_AUTHOR_DATE"] = iso_time
        env["GIT_COMMITTER_DATE"] = iso_time
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True, env=env,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-b", "master")
    git(path, "config", "user.email", "fixture@test")
    git(path, "config", "user.name", "fixture")


@dataclass
class Workspace:
    root: Path      # workspace: содержит prograph/ и steward/
    prograph: Path  # локальный клон canon
    steward: Path
    seed: Path      # upstream-рабочий клон (для правок «на GitHub»)
    canon: Path     # bare-репо, играет origin


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_workspace(
    tmp: Path, *, now: datetime, report_age_hours: int = 1
) -> Workspace:
    root = tmp / "ws"
    root.mkdir()

    seed = tmp / "seed"
    _init_repo(seed)
    schemas = {"intended-graph": SCHEMA_INTENDED, "conformance-report": SCHEMA_REPORT}
    for name, rel in UPSTREAM_DIRS.items():
        target = seed / rel / "schema.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(schemas[name])
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "contracts v1", at_time=now)

    canon = tmp / "prograph-canon.git"
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", str(seed), str(canon)], check=True
    )
    subprocess.run(
        ["git", "clone", "--quiet", str(canon), str(root / "prograph")], check=True
    )
    prograph = root / "prograph"

    steward = root / "steward"
    _init_repo(steward)
    pinned = git(seed, "rev-parse", "--short", "HEAD")
    for name, rel in VENDORED_DIRS.items():
        vdir = steward / rel
        vdir.mkdir(parents=True)
        (vdir / "schema.json").write_bytes(schemas[name])
        sha = hashlib.sha256(schemas[name]).hexdigest()
        (vdir / "PIN").write_text(
            f"source: prograph@{pinned} {UPSTREAM_DIRS[name]}/schema.json\n"
            f"sha256: {sha}\nvendored: 2026-08-03\npurpose: test fixture\n"
        )
    evidence = steward / EVIDENCE_DIR
    evidence.mkdir(parents=True)
    (evidence / "intended-graph.yaml").write_text("components: []\n")
    report_time = now - timedelta(hours=report_age_hours)
    (evidence / "conformance-report.json").write_text(json.dumps({
        "schema": "conformance-report/v1",
        "generated_at": _iso(report_time),
        "snapshot": {"indexed_at": _iso(report_time), "id": 1, "complete": True},
    }))
    git(steward, "add", "-A")
    git(steward, "commit", "-m", "vendored contracts + WS-005 evidence", at_time=report_time)
    return Workspace(root, prograph, steward, seed, canon)


def upstream_change(ws: Workspace, relpath: str, content: bytes, msg: str) -> None:
    """Правка «на GitHub»: коммит в seed + push в canon. Клон ws.prograph не трогаем."""
    target = ws.seed / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    git(ws.seed, "add", "-A")
    git(ws.seed, "commit", "-m", msg)
    git(ws.seed, "push", "--quiet", str(ws.canon), "master:master")
