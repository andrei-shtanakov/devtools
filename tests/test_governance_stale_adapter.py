"""Семантика prospective stale-адаптера (спека §3): пины против локального контента.

Адаптер временный (до candidate-контракта steward) — тесты и есть его контракт.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.gatecheck.checks import collect_bundle
from steward.graph import load_profile
from steward.roles import load_roles_catalog

from governance.stale_adapter import StaleFinding, blob_sha1, check_stale
from tests.governance_fixtures.bundles import (
    REQUIREMENTS_MD,
    blob_hash,
    make_bundle,
    make_profile,
)


def _artifacts(tmp_path: Path, *, behaviour_ok: bool = True):
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    bundle = make_bundle(tmp_path, behaviour_ok=behaviour_ok)
    artifacts, _ = collect_bundle(graph, bundle)
    return bundle, artifacts


def test_blob_sha1_matches_git_hash_object() -> None:
    assert blob_sha1(REQUIREMENTS_MD) == blob_hash(REQUIREMENTS_MD)


def test_fresh_pins_give_no_findings(tmp_path: Path) -> None:
    _, artifacts = _artifacts(tmp_path)
    assert check_stale(artifacts) == []


def test_stale_pin_is_reported(tmp_path: Path) -> None:
    bundle, _ = _artifacts(tmp_path)
    req = bundle / "10-requirements.md"
    req.write_text(REQUIREMENTS_MD + "\n## FR-2 (Must) Новое требование\n")
    # перечитать бандл после правки
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_stale(artifacts)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, StaleFinding)
    assert f.upstream == "requirements"
    assert f.pinned != f.actual and f.actual is not None


def test_pin_to_absent_upstream_is_reported(tmp_path: Path) -> None:
    bundle, _ = _artifacts(tmp_path)
    (bundle / "10-requirements.md").unlink()
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_stale(artifacts)
    assert [f.upstream for f in findings] == ["requirements"]
    assert findings[0].actual is None  # «факт недоступен», не «совпало»
