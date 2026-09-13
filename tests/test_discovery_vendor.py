"""Vendored discovery-brief contract: integrity, provenance and drift."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_discovery_vendor as vendor  # noqa: E402


def test_real_copy_matches_manifest_and_gate_is_importable() -> None:
    from governance.discovery_contract.gate_check import FRAMES, check

    assert vendor.verify("consistency").status == "ok"
    assert set(FRAMES) == {"customer", "engineer"}
    assert check("", base_dir=None)


def test_manifest_cannot_drop_expected_file(tmp_path, monkeypatch) -> None:
    (tmp_path / "PINNED.txt").write_text(
        "commit: " + "a" * 40 + "\n"
        "DISCOVERY-BRIEF-CONTRACT.md " + "0" * 64 + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vendor, "CONTRACT", tmp_path)

    for mode in ("consistency", "provenance"):
        verdict = vendor.verify(mode, fetch=lambda *_: b"unused")
        assert verdict.status == "failed"
        assert "gate_check.py" in verdict.detail


def test_consistency_detects_changed_bytes(tmp_path, monkeypatch) -> None:
    contract = tmp_path / "DISCOVERY-BRIEF-CONTRACT.md"
    gate = tmp_path / "gate_check.py"
    contract.write_text("changed\n", encoding="utf-8")
    gate.write_text("gate\n", encoding="utf-8")
    gate_digest = hashlib.sha256(gate.read_bytes()).hexdigest()
    (tmp_path / "PINNED.txt").write_text(
        "commit: " + "a" * 40 + "\n"
        "DISCOVERY-BRIEF-CONTRACT.md " + "0" * 64 + "\n"
        f"gate_check.py {gate_digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vendor, "CONTRACT", tmp_path)

    verdict = vendor.verify("consistency")
    assert verdict.status == "failed"
    assert "DISCOVERY-BRIEF-CONTRACT.md" in verdict.detail


def test_provenance_matching_copy_passes() -> None:
    def fetch(_commit: str, rel: str) -> bytes:
        return vendor.local_path(rel).read_bytes()

    assert vendor.verify("provenance", fetch=fetch).status == "ok"


def test_provenance_unreachable_is_unknown_not_ok() -> None:
    assert vendor.verify("provenance", fetch=lambda *_: None).status == "unknown"


def test_provenance_mismatch_fails() -> None:
    assert vendor.verify(
        "provenance", fetch=lambda *_: b"not-upstream"
    ).status == "failed"


def test_drift_equal_moved_and_unavailable() -> None:
    commit, _ = vendor.read_pinned()
    assert vendor.drift(fetch=lambda *_: commit.encode()).status == "ok"
    assert vendor.drift(fetch=lambda *_: b"b" * 40).status == "failed"
    assert vendor.drift(fetch=lambda *_: None).status == "unknown"


def test_default_drift_fetch_resolves_explicit_default_branch(monkeypatch) -> None:
    urls: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def urlopen(request, timeout):
        assert timeout == 20
        urls.append(request.full_url)
        payload = (
            {"default_branch": "trunk"}
            if request.full_url == vendor.REPO_API
            else {"sha": "c" * 40}
        )
        return Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(vendor.urllib.request, "urlopen", urlopen)

    assert vendor.github_head_fetch("HEAD", "HEAD") == b"c" * 40
    assert urls == [vendor.REPO_API, vendor.COMMITS_API.format(ref="trunk")]
