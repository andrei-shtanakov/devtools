"""Trust descriptors and the verdict size budget (installer spec §6.1)."""

from __future__ import annotations

import hashlib
import json

import pytest

from governance import act_trust_findings as tf
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_trust_types import (
    KINDS,
    MAX_DETAIL_BYTES,
    Finding,
    PolicyPin,
    clip_detail,
    detail_size,
)

P = "p" * 40
LIMIT = 32 * 1024
MAX_NAME = "o" * 39 + "/" + "n" * 100  # 140 bytes
MAX_BRANCH = "é" * 127 + "b"  # 255 bytes
# Detail variants that escape worst in JSON: C0 controls are 6 bytes each.
DETAILS = {
    "controls": ["\x01" * 600],
    # a lone surrogate (from untrusted JSON) goes through clip_detail as U+FFFD
    "mixed": ['"\\\x01\x1fя語😀\ud800' * 100, "я" * 300, "語" * 200, "😀" * 200],
}


def _sha(i: int, salt: str) -> str:
    return hashlib.sha1(f"{salt}{i}".encode()).hexdigest()


def _epochs(n: int) -> list[Epoch]:
    closed = [
        Epoch(i, _sha(i, "anchor"), last_confirmed=_sha(i, "last")) for i in range(1, n)
    ]
    return [*closed, Epoch(n, _sha(n, "anchor"))]


def _source(admins: int = 1, *, name: str = "o/approval-policy", branch: str = "main"):
    who = frozenset(f"administrator-{i:08d}-x" for i in range(admins))
    raw = (
        f"APPROVAL_POLICY_REPO={name}\nAPPROVAL_POLICY_BRANCH={branch}\n"
        f"BOOTSTRAP_ACTIVATION={'b' * 40}\nPOLICY_ADMINS={','.join(sorted(who))}\n"
    ).encode()
    return SourceV2(name, branch, "b" * 40, who), raw


def _envelope(epochs: int = 3, admins: int = 1, **source: str) -> dict[str, object]:
    src, raw = _source(admins, **source)
    return {
        "schema": "df-trust-verdict/v1",
        "origin_route": "merge",
        "root_id": "r" * 64,
        "ctx_id": "c" * 64,
        "binding": {
            "host": "github.com",
            "df_trust_repository_id": 123456789012,
            "df_trust_full_name": "o" * 39 + "/" + "d" * 100,
            "approval_policy_repository_id": 210987654321,
            "admin_digest": "a" * 64,
        },
        "relation": "descendant",
        "rules_version": "df-trust-rules/v1",
        "accepted_sha": "1" * 40,
        "snapshot_sha": "2" * 40,
        "policy": PolicyPin(src.repo, src.branch, P),
        "last_epoch": epochs,
        "ranges": tf.range_descriptor(_epochs(epochs), P),
        "source": tf.source_descriptor(src, raw),
    }


def _all_findings(variant: str = "mixed") -> list[Finding]:
    details = DETAILS[variant]
    return [
        Finding(kind, role, clip_detail(details[i % len(details)]))
        for i, (kind, role) in enumerate(
            (k, r) for k, roles in sorted(KINDS.items()) for r in sorted(roles)
        )
    ]


def test_range_descriptor_closed_and_open() -> None:
    got = tf.range_descriptor(_epochs(3), P)
    assert got.ranges == (
        (1, _sha(1, "anchor"), _sha(1, "last")),
        (2, _sha(2, "anchor"), _sha(2, "last")),
        (3, _sha(3, "anchor"), P),
    )
    rows = [list(r) for r in got.ranges]
    canon = json.dumps(rows, separators=(",", ":")).encode()
    assert got.digest == hashlib.sha256(canon).hexdigest()
    assert tf.range_descriptor(_epochs(3), "q" * 40).digest != got.digest


def test_65_epochs_unsupported_size() -> None:
    assert len(tf.range_descriptor(_epochs(64), P).ranges) == 64
    with pytest.raises(tf.UnsupportedSize) as caught:
        tf.range_descriptor(_epochs(65), P)
    assert caught.value.field == "epochs"
    assert str(caught.value) == "unsupported_size: epochs"


@pytest.mark.parametrize(
    ("kw", "field"),
    [
        ({"name": "o/" + "n" * 139}, "policy_full_name"),
        ({"branch": "b" * 256}, "policy_branch"),
    ],
)
def test_source_over_limit_unsupported_size(kw: dict[str, str], field: str) -> None:
    src, raw = _source(**kw)
    with pytest.raises(tf.UnsupportedSize) as caught:
        tf.source_descriptor(src, raw)
    assert caught.value.field == field


def test_source_descriptor_at_limits() -> None:
    src, raw = _source(name="o/" + "n" * 138, branch="b" * 255)
    got = tf.source_descriptor(src, raw)
    assert got.policy_full_name == src.repo and got.policy_branch == src.branch
    assert got.digest == hashlib.sha256(raw).hexdigest()
    assert got.bootstrap_activation == "b" * 40


def test_policy_admins_4000_compact() -> None:
    src, raw = _source(4000)
    assert len(raw) > 88 * 1000  # the list itself would not fit the budget
    got = tf.source_descriptor(src, raw)
    assert got.admins_count == 4000
    joined = "\n".join(sorted(src.policy_admins)).encode()
    assert got.admins_digest == hashlib.sha256(joined).hexdigest()
    verdict = tf.fit_verdict(_envelope(admins=4000), _all_findings(), "")
    assert len(verdict) <= LIMIT
    assert b"administrator-" not in verdict
    assert json.loads(verdict)["source"]["admins_count"] == 4000


@pytest.mark.parametrize("variant", sorted(DETAILS))
def test_64_epochs_all_16_combinations_max_details_fit_32k(variant: str) -> None:
    """The true worst case: maximal epochs, admins, name, branch and details
    that escape worst; the bytes `fit_verdict` returns are the wire form."""
    findings = _all_findings(variant)
    assert len(findings) == 16
    assert all(
        MAX_DETAIL_BYTES - 6 < detail_size(f.detail) <= MAX_DETAIL_BYTES
        for f in findings
    )
    envelope = _envelope(64, 4000, name=MAX_NAME, branch=MAX_BRANCH)
    bare = tf.fit_verdict(envelope, findings, "")  # the maximal legal input fits
    assert len(bare) <= LIMIT
    verdict = tf.fit_verdict(envelope, findings, "диагностика\x01 " * 10_000)
    assert len(verdict) <= LIMIT
    doc = json.loads(verdict)
    assert len(doc["ranges"]["ranges"]) == 64
    assert doc["policy"] == {"full_name": MAX_NAME, "branch": MAX_BRANCH, "p_sha": P}
    got = {(f["kind"], f["role"]): f["detail"] for f in doc["findings"]}
    assert got == {(f.kind, f.role): f.detail for f in findings}
    assert doc["diagnostics"].startswith("диагностика")
    assert ("\ufffd" in verdict.decode()) == (variant == "mixed")


def test_codes_never_truncated() -> None:
    findings = _all_findings()
    envelope = _envelope(64)
    bare = tf.fit_verdict(envelope, findings, "")
    verdict = tf.fit_verdict(envelope, findings, "ж" * LIMIT)
    assert len(verdict) <= LIMIT
    doc = json.loads(verdict)
    assert {**doc, "diagnostics": ""} == json.loads(bare)
    assert doc["diagnostics"] and set(doc["diagnostics"]) == {"ж"}
    # the cut is the remainder: one more character would not fit
    assert len(verdict) + len("ж".encode()) > LIMIT


def test_short_diagnostics_kept_whole() -> None:
    doc = json.loads(tf.fit_verdict(_envelope(), [], 'line "one"\nline two'))
    assert doc["diagnostics"] == 'line "one"\nline two'
    assert doc["findings"] == []


def test_kind_outside_route_rejected() -> None:
    envelope = {**_envelope(), "origin_route": "trust-status"}
    breaks = Finding("policy_history_break", "approval-policy", "x")
    with pytest.raises(ValueError, match="outside route"):
        tf.fit_verdict(envelope, [breaks], "")
    with pytest.raises(ValueError, match="unknown origin_route"):
        tf.fit_verdict({**envelope, "origin_route": "stop"}, [], "")
    tf.fit_verdict({**envelope, "origin_route": "merge"}, [breaks], "")


def test_one_finding_per_kind_and_role() -> None:
    twice = [Finding("token_write", "df-trust", "a")] * 2
    with pytest.raises(ValueError, match="duplicate finding"):
        tf.fit_verdict(_envelope(), twice, "")


def test_envelope_over_budget_unsupported_size() -> None:
    with pytest.raises(tf.UnsupportedSize) as caught:
        tf.fit_verdict({**_envelope(), "extra": "x" * LIMIT}, [], "")
    assert caught.value.field == "verdict"


@pytest.mark.parametrize(
    ("epochs", "message"),
    [
        ([], "no epochs"),
        ([Epoch(2, "a" * 40)], "not contiguous"),
        ([Epoch(1, "a" * 40), Epoch(3, "b" * 40)], "not contiguous"),
        ([Epoch(1, "a" * 40), Epoch(2, "b" * 40)], "open epoch 1"),
    ],
)
def test_range_descriptor_malformed_epochs(epochs: list[Epoch], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        tf.range_descriptor(epochs, P)


def test_reserved_keys_rejected() -> None:
    with pytest.raises(ValueError, match="reserved"):
        tf.fit_verdict({**_envelope(), "findings": []}, [], "")


def test_lone_surrogates_in_diagnostics_replaced() -> None:
    doc = json.loads(tf.fit_verdict(_envelope(), [], "bad \ud800 text"))
    assert doc["diagnostics"] == "bad \ufffd text"
