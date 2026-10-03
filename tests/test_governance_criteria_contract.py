"""criteria_contract: min-spec-runner.env и целостность вендоренной копии."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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
    d = up / "schemas/criteria-closure/v1"
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


def test_vendored_copy_is_consistent():
    assert cc.integrity_findings() == []
    assert cc.vendored()


def test_responses_copy_is_consistent():
    assert cc.integrity_findings(cc.RESPONSES_DIR) == []


def test_responses_drift_against_upstream_checkout():
    """Локальный дрейф эталонов против соседнего клона spec-runner. Гейт —
    шаг CI (ci=True); здесь недоступный апстрим или ревизия PIN, которой нет
    в соседнем клоне (не фетчен), — not-checked, а не красный тест (#532)."""
    import subprocess

    upstream = Path(__file__).resolve().parents[1].parent / "spec-runner"
    sha = (cc.RESPONSES_DIR / "PIN").read_text().split("@")[-1].strip()
    if (
        upstream.exists()
        and subprocess.run(
            ["git", "-C", str(upstream), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
        ).returncode
    ):
        pytest.skip(f"ревизии PIN {sha[:7]} нет в соседнем клоне spec-runner")
    errors, notes = cc.drift_findings(
        cc.RESPONSES_DIR,
        upstream if upstream.exists() else None,
        ci=False,
        upstream_path="tests/fixtures/criteria-closure/v1/responses",
    )
    assert errors == [], errors
    if notes:
        pytest.skip(notes[0])


def test_pending_min_version_keeps_oracle_unavailable(tmp_path):
    """`pending` (вендоринг схем до релиза команды — штатно для будущего v2)
    держит оракул недоступным при любом установленном spec-runner: ветка
    `minimum.version is None` в oracle_available (ревью #534)."""
    env = tmp_path / "min.env"
    env.write_text("MIN_SPEC_RUNNER_VERSION=pending\n")
    minimum = cc.read_min_version(env)
    assert minimum.version is None
    assert not cc.oracle_available("99.0.0", minimum, is_vendored=True)
    assert not cc.oracle_available("99.0.0", cc.MinVersion(None), is_vendored=True)


def test_released_min_version_enables_oracle_from_4_5_0():
    """spec-runner v4.5.0 (release X, B2b — `verify --criteria`) опубликовал
    min-spec-runner.env; копия вендорит его байты: оракул доступен при 4.5.0+
    и недоступен ниже."""
    minimum = cc.read_min_version()
    assert minimum.version == "4.5.0"
    assert cc.oracle_available("4.5.0", minimum, is_vendored=True)
    assert not cc.oracle_available("4.4.9", minimum, is_vendored=True)
    assert "min-spec-runner.env" in json.loads(
        (cc.CONTRACT_DIR / "manifest.json").read_text()
    )


def test_numeric_min_version_gates_as_before(tmp_path):
    env = tmp_path / "min.env"
    env.write_text("MIN_SPEC_RUNNER_VERSION=4.5.0\n")
    m = cc.read_min_version(env)
    assert cc.oracle_available("4.5.0", m, is_vendored=True)
    assert not cc.oracle_available("4.4.9", m, is_vendored=True)


def test_drift_reads_schemas_from_schemas_dir():
    """Апстрим держит схемы в schemas/criteria-closure/v1, не в contracts/."""
    import inspect

    sig = inspect.signature(cc.drift_findings)
    assert sig.parameters["upstream_path"].default == "schemas/criteria-closure/v1"


@pytest.mark.parametrize(
    "installed",
    ["4.5.0rc1", "4.5.0a2", "4.5.0b1", "4.5.0.dev3", "4.5.0-rc.1", "4.5.0-beta"],
)
def test_prerelease_is_below_its_release(installed):
    """Pre-release ниже своего релиза (#481 M-12): `4.5.0rc1` — не 4.5.0,
    оракул им недоступен. Прежний `_parts` выбрасывал `0rc1` целиком."""
    minimum = cc.MinVersion("4.5.0")
    assert not cc.oracle_available(installed, minimum, is_vendored=True)
    assert cc.oracle_available(installed, cc.MinVersion("4.4.0"), is_vendored=True)


@pytest.mark.parametrize("installed", ["4.5.0.post1", "4.5.0+local.7", "4.5.1rc1"])
def test_post_local_and_next_prerelease_reach_the_release(installed):
    """Post-release и local-метка не ниже релиза; rc СЛЕДУЮЩЕГО релиза выше
    текущего — суффикс понижает только внутри своего номера."""
    assert cc.oracle_available(installed, cc.MinVersion("4.5.0"), is_vendored=True)


def test_prerelease_phases_are_ordered():
    """dev < a < b < rc внутри одного номера; нераспознанный суффикс —
    ниже любого распознанного (fail-closed)."""
    order = ["4.5.0-weird", "4.5.0.dev1", "4.5.0a1", "4.5.0b1", "4.5.0rc1"]
    order += ["4.5.0rc2", "4.5.0"]
    for lower, higher in zip(order, order[1:], strict=False):
        assert cc.oracle_available(higher, cc.MinVersion(lower), is_vendored=True)
        assert not cc.oracle_available(
            lower, cc.MinVersion(higher), is_vendored=True
        ), (lower, higher)
