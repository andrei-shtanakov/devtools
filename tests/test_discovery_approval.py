"""Вендоренный self-hash discovery (спека need-stage §11.4.2, T40)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from governance import discovery_approval as da

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_discovery_approval_vendor as vendor  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "discovery_approval"


def _load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_self_hash_matches_producer_on_signed_fixture() -> None:
    expected = json.loads(_load("signed-brief.json"))
    assert da.self_hash(_load("signed-brief.md")) == expected["self_hash"]
    assert da.self_hash(_load("draft-brief.md")) == expected["self_hash"]


def test_crlf_variant_has_the_same_hash() -> None:
    signed = _load("signed-brief.md")
    assert da.self_hash(signed.replace("\n", "\r\n")) == da.self_hash(signed)


def test_verify_agrees_with_producer() -> None:
    expected = json.loads(_load("signed-brief.json"))
    assert da.verify(_load("signed-brief.md")) == expected["verify_signed"]
    assert da.verify(_load("draft-brief.md")) == expected["verify_draft"]


def test_not_a_brief_is_raised() -> None:
    with pytest.raises(da.NotABrief):
        da.self_hash("no frontmatter")


def test_loader_refuses_unknown_discovery_import(tmp_path: Path) -> None:
    src = (da.VENDOR / "approval.py").read_bytes()
    bad = tmp_path / "approval.py"
    bad.write_bytes(src + b"\nfrom discovery.render import render_brief\n")
    import hashlib

    with pytest.raises(da.VendorError, match="import"):
        da.load_module(bad, hashlib.sha256(bad.read_bytes()).hexdigest())


def test_loader_refuses_digest_mismatch(tmp_path: Path) -> None:
    bad = tmp_path / "approval.py"
    bad.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(da.VendorError, match="sha256"):
        da.load_module(bad, "0" * 64)


def test_vendor_consistency_ok() -> None:
    assert vendor.verify("consistency").status == "ok"


def test_vendor_consistency_detects_dropped_pin(tmp_path: Path, monkeypatch) -> None:
    for name in ("approval.py", "hashing.py", "gate_check.py"):
        (tmp_path / name).write_bytes((vendor.CONTRACT / name).read_bytes())
    pinned = (vendor.CONTRACT / "PINNED.txt").read_text(encoding="utf-8")
    kept = [ln for ln in pinned.splitlines() if not ln.startswith("hashing.py")]
    (tmp_path / "PINNED.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
    monkeypatch.setattr(vendor, "CONTRACT", tmp_path)
    verdict = vendor.verify("consistency")
    assert verdict.status == "failed" and "hashing.py" in verdict.detail


def test_vendor_provenance_matching_copy_passes() -> None:
    def fetch(_commit: str, rel: str) -> bytes:
        return (vendor.CONTRACT / rel).read_bytes()

    assert vendor.verify("provenance", fetch=fetch).status == "ok"


def test_vendor_provenance_unreachable_is_unknown() -> None:
    assert vendor.verify("provenance", fetch=lambda *_: None).status == "unknown"


def test_vendor_provenance_mismatch_fails() -> None:
    assert vendor.verify("provenance", fetch=lambda *_: b"x").status == "failed"


def test_vendor_drift_equal_moved_unknown() -> None:
    commit, _ = vendor.read_pinned()
    assert vendor.drift(fetch=lambda *_: commit.encode()).status == "ok"
    assert vendor.drift(fetch=lambda *_: b"b" * 40).status == "failed"
    assert vendor.drift(fetch=lambda *_: None).status == "unknown"
