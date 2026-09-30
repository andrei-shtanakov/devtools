"""criteria_contract: min-spec-runner.env и целостность вендоренной копии."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from governance import criteria_contract as cc


def write_min(d, v="4.3.0"):
    (d / "min-spec-runner.env").write_text(f"MIN_SPEC_RUNNER_VERSION={v}\n")


def test_not_vendored_means_unavailable(tmp_path):
    write_min(tmp_path)
    mv = cc.read_min_version(tmp_path / "min-spec-runner.env")
    assert mv == cc.MinVersion("4.3.0")
    assert cc.integrity_findings(tmp_path) == []
    assert not cc.vendored(tmp_path)
    assert not cc.oracle_available("9.9.9", mv, is_vendored=False)


def test_vendored_requires_matching_manifest(tmp_path):
    write_min(tmp_path)
    schema = tmp_path / "response.schema.json"
    schema.write_text("{}")
    (tmp_path / "PIN").write_text("SOURCE: spec-runner @ abc1234\n")
    assert cc.integrity_findings(tmp_path) != [] and not cc.vendored(
        tmp_path
    )  # нет manifest
    (tmp_path / "manifest.json").write_text(
        json.dumps({"response.schema.json": hashlib.sha256(b"{}").hexdigest()})
    )
    assert cc.integrity_findings(tmp_path) == [] and cc.vendored(tmp_path)
    mv = cc.read_min_version(tmp_path / "min-spec-runner.env")
    assert cc.oracle_available("4.3.0", mv, is_vendored=True)
    assert cc.oracle_available("4.10.1", mv, is_vendored=True)
    assert not cc.oracle_available("4.2.9", mv, is_vendored=True)
    assert not cc.oracle_available(None, mv, is_vendored=True)
    schema.write_text("{ }")
    assert cc.integrity_findings(tmp_path) != [] and not cc.vendored(tmp_path)


def _upstream(tmp_path, content: bytes) -> tuple[Path, str]:
    import subprocess

    up = tmp_path / "up"
    d = up / "contracts/criteria-closure/v1"
    d.mkdir(parents=True)
    (d / "response.schema.json").write_bytes(content)
    subprocess.run(["git", "init", "-q", str(up)], check=True)
    subprocess.run(["git", "-C", str(up), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(up), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return up, sha


def _vendor(d, sha, content: bytes):
    write_min(d)
    (d / "response.schema.json").write_bytes(content)
    (d / "PIN").write_text(f"SOURCE: spec-runner @ {sha}\n")
    (d / "manifest.json").write_text(
        json.dumps({"response.schema.json": hashlib.sha256(content).hexdigest()})
    )


def test_drift_against_pinned_upstream(tmp_path):
    up, sha = _upstream(tmp_path, b"{}")
    vend = tmp_path / "v"
    vend.mkdir()
    _vendor(vend, sha, b"{}")
    assert cc.drift_findings(vend, up, ci=True) == ([], [])
    _vendor(vend, sha, b'{"x": 1}')
    errors, _ = cc.drift_findings(vend, up, ci=True)
    assert errors


def test_drift_missing_upstream_is_error_in_ci_note_locally(tmp_path):
    vend = tmp_path / "v"
    vend.mkdir()
    _vendor(vend, "a" * 40, b"{}")
    errors, _ = cc.drift_findings(vend, None, ci=True)
    assert errors
    errors, notes = cc.drift_findings(vend, None, ci=False)
    assert errors == [] and any("not-checked" in n for n in notes)


def test_shipped_contract_is_not_vendored_yet():
    assert not cc.vendored()
    assert cc.integrity_findings() == []
