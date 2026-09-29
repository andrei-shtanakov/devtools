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


# Вложенная вендоренная копия со своим PIN (фикстуры владения, devtools#491):
# CI зовёт integrity_findings()/drift_findings(CONTRACT_DIR, …) по корню — они
# обязаны спуститься в неё, даже когда PIN корня (схемы) ещё нет.
NESTED = "fixtures/ownership"
UP_PATH = "tests/fixtures/criteria-closure/v1/ownership"


def _nested_upstream(tmp_path, content: bytes) -> tuple[Path, str]:
    import subprocess

    up = tmp_path / "up"
    d = up / UP_PATH
    d.mkdir(parents=True)
    (d / "01_case.py").write_bytes(content)
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


def _vendor_nested(root, sha, content: bytes, *, upstream_path: str | None = UP_PATH):
    root.mkdir(parents=True, exist_ok=True)
    write_min(root)
    d = root / NESTED
    d.mkdir(parents=True, exist_ok=True)
    (d / "01_case.py").write_bytes(content)
    pin = f"SOURCE: spec-runner @ {sha}\n"
    if upstream_path is not None:
        pin += f"UPSTREAM_PATH: {upstream_path}\n"
    (d / "PIN").write_text(pin)
    (d / "manifest.json").write_text(
        json.dumps({"01_case.py": hashlib.sha256(content).hexdigest()})
    )


def test_nested_copy_integrity_is_checked_from_root(tmp_path):
    _vendor_nested(tmp_path, "a" * 40, b"x")
    assert cc.integrity_findings(tmp_path) == []
    assert not cc.vendored(tmp_path)  # вложенная копия схемы не вендорит
    (tmp_path / NESTED / "01_case.py").write_bytes(b"y")
    found = cc.integrity_findings(tmp_path)
    assert found and all(f"{NESTED}/01_case.py" in f for f in found)


def test_nested_copy_drift_uses_upstream_path_from_pin(tmp_path):
    up, sha = _nested_upstream(tmp_path, b"x")
    vend = tmp_path / "v"
    _vendor_nested(vend, sha, b"x")
    assert cc.drift_findings(vend, up, ci=True) == ([], [])
    _vendor_nested(vend, sha, b"y")
    errors, _ = cc.drift_findings(vend, up, ci=True)
    assert errors and all(f"{NESTED}/01_case.py" in e for e in errors)


def test_nested_copy_without_upstream_is_error_in_ci(tmp_path):
    _vendor_nested(tmp_path, "a" * 40, b"x")
    errors, _ = cc.drift_findings(tmp_path, None, ci=True)
    assert errors
    errors, notes = cc.drift_findings(tmp_path, None, ci=False)
    assert errors == [] and any("not-checked" in n for n in notes)


def test_nested_pin_without_upstream_path_is_refused(tmp_path):
    up, sha = _nested_upstream(tmp_path, b"x")
    vend = tmp_path / "v"
    _vendor_nested(vend, sha, b"x", upstream_path=None)
    errors, _ = cc.drift_findings(vend, up, ci=True)
    assert errors and any("UPSTREAM_PATH" in e for e in errors)


def test_shipped_nested_copies_are_intact():
    nested = [
        p.parent for p in cc.CONTRACT_DIR.rglob("PIN") if p.parent != cc.CONTRACT_DIR
    ]
    assert nested  # фикстуры владения вендорены
    assert cc.integrity_findings() == []


def test_pin_without_parsable_sha_is_refused_not_read_from_index(tmp_path):
    up, sha = _nested_upstream(tmp_path, b"x")
    vend = tmp_path / "v"
    _vendor_nested(vend, sha, b"x")
    pin = vend / NESTED / "PIN"
    for text in (f"# SOURCE: spec-runner @ {sha}\n", "SOURCE: spec-runner @ \n", ""):
        pin.write_text(text + f"UPSTREAM_PATH: {UP_PATH}\n")
        errors, _ = cc.drift_findings(vend, up, ci=True)
        assert errors and any("SOURCE" in e for e in errors), text


def test_broken_nested_copy_does_not_switch_the_oracle_off(tmp_path):
    root_content = b"{}"
    _vendor(tmp_path, "a" * 40, root_content)  # корень вендорен
    _vendor_nested(tmp_path, "a" * 40, b"x")
    assert cc.vendored(tmp_path)
    (tmp_path / NESTED / "01_case.py").write_bytes(b"y")
    assert cc.integrity_findings(tmp_path)  # поломка видна гейту CI
    assert cc.vendored(tmp_path)  # но оракул выключает только корень


def test_file_in_copy_but_not_in_manifest_is_a_finding(tmp_path):
    _vendor_nested(tmp_path, "a" * 40, b"x")
    (tmp_path / NESTED / "02_extra.py").write_bytes(b"z")
    found = cc.integrity_findings(tmp_path)
    assert found and any(f"{NESTED}/02_extra.py" in f for f in found)


def test_unreachable_revision_is_one_finding_not_per_file(tmp_path):
    up, _ = _nested_upstream(tmp_path, b"x")
    vend = tmp_path / "v"
    _vendor_nested(vend, "b" * 40, b"x")
    errors, _ = cc.drift_findings(vend, up, ci=True)
    assert len(errors) == 1 and "ревизи" in errors[0]
