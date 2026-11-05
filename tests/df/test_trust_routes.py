"""The perimeter routes over the trust state: `perimeter-status` and
`trust-status` (installer spec 5.2, 6.1 rev 11; stage 3a, Task 10).

Through the publisher frame, with the trust sources and the checker faked
(`tests/df/trust_world.py`): no network, no real `gh`, no real checker.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from df import platform
from df.config import InstallConfig
from df.publisher import history, perimeter, request, trust_routes, trust_state
from df.publisher.perimeter import Unavailable
from df.publisher.request import BadResponse, Response, perimeter_result
from df.publisher.trust_routes import RULES_VERSION, check_ranges, range_digest
from df.publisher.trust_types import Finding, PolicyPin, RangeDescriptor
from df.stage2_config import TrustBinding
from tests.df.conftest import FakeLayout
from tests.df.launch_world import ADMIN, entry, put_record, record, snapshot
from tests.df.publisher_world import World, build_world, raw_of
from tests.df.trust_world import (
    BINDING,
    POLICY_ID,
    POLICY_REPO,
    S1,
    S2,
    S3,
    SOURCE_DIGEST,
    TRUST_ID,
    P,
    TrustWorld,
    accept,
    install,
    ranges_of,
    source_descriptor,
    verdict,
    wire,
    write_trust,
)

ROOT = BINDING.root_id
RULESET = Finding("ruleset_missing", "df-trust", "no ruleset on main")
TOKEN = "ghp_history0123456789"
_n = iter(range(1, 10_000))


@pytest.fixture()
def tw(monkeypatch: pytest.MonkeyPatch) -> TrustWorld:
    return install(monkeypatch)


@pytest.fixture()
def world(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch, tw: TrustWorld
) -> World:
    w = build_world(fake_layout, monkeypatch)
    w.lay.perimeter_dir.mkdir(mode=0o700)
    perimeter.create_db(w.lay)
    write_trust(w.lay)
    return w


def status(world: World, capsys: pytest.CaptureFixture[str], sha: str = S1) -> Response:
    raw = raw_of(
        "perimeter-status",
        request_id=f"req-stat-{next(_n):04d}",
        expected_snapshot_sha=sha,
    )
    return world.run("perimeter-status", raw, capsys=capsys)[1]


def tstatus(world: World, capsys: pytest.CaptureFixture[str]) -> Response:
    raw = raw_of("trust-status", request_id=f"req-trst-{next(_n):04d}")
    return world.run("trust-status", raw, capsys=capsys)[1]


def db_rows(world: World, sql: str) -> list[tuple]:
    conn = sqlite3.connect(world.lay.perimeter_dir / perimeter.DB_NAME)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def observations(world: World) -> list[tuple]:
    return db_rows(
        world,
        "SELECT route, accepted_sha, head_sha, outcome FROM trust_observations "
        "ORDER BY observation_id",
    )


def events(world: World) -> list[tuple]:
    return db_rows(
        world,
        "SELECT state, epoch, detected_by, violated_role FROM events ORDER BY event_id",
    )


def head(world: World) -> tuple | None:
    found = db_rows(world, "SELECT sha, last_epoch, row_version FROM trust_heads")
    return found[0] if found else None


def keyed(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> TrustBinding:
    """A keyed binding: its profile directory, an approved `gh` (the path
    policy and the hash are `approved_gh`'s own tests) and the token."""
    profile = tmp_path / "history-gh"
    profile.mkdir()
    write_trust(world.lay, history=profile)
    monkeypatch.setattr(trust_routes, "approved_gh", lambda _s: Path("/opt/gh"))
    monkeypatch.setattr(history, "history_token", lambda *_a, **_k: TOKEN)
    return replace(BINDING, history_gh_config_dir=str(profile))


# --- the fast path ---------------------------------------------------------------


def test_fast_path_no_checker_no_observation(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    accept(world.lay, S1, 2)
    resp = status(world, capsys)
    assert resp.result == {**perimeter_result("clear", None, S1, 2), "events": []}
    assert tw.checker.requests == []
    assert len(observations(world)) == 1  # the seed's only


def test_fast_path_source_digest_changed_goes_observation(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """The raw digest of `source.env` on H differs from the recorded one: the
    fast path is not taken -- an observation (A, A) and `op: trust`. A
    verdict repeating the recorded digest completes it (H = A: no write);
    one with another digest for the same SHA is unbound (D-d)."""
    accept(world.lay, S1, 2, source_digest="0" * 64)
    tw.checker.answer = lambda req: wire(verdict(req, source=source_descriptor()))
    resp = status(world, capsys)
    assert len(tw.checker.requests) == 1
    req = tw.checker.requests[0]
    assert (req["origin_route"], req["trust_head"]) == ("perimeter-status", S1)
    assert observations(world)[-1] == ("perimeter-status", S1, S1, "unresolved")
    assert resp.result["state"] == "unavailable"
    recorded = source_descriptor("0" * 64)
    tw.checker.answer = lambda req: wire(verdict(req, source=recorded))
    resp = status(world, capsys)
    assert observations(world)[-1] == ("perimeter-status", S1, S1, "accepted")
    assert resp.result["state"] == "clear" and resp.result["last_epoch"] == 2
    assert head(world) == (S1, 2, 1)


def test_fast_path_only_without_open_observations(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unfinished observation of the root (a lost answer of another
    pair) keeps the fast path closed: it is evaluated again first."""
    accept(world.lay, S1, 2)
    with perimeter.open_db(world.lay) as db:
        ctx = trust_state.ctx_id(
            "trust-status", BINDING, ADMIN, RULES_VERSION, S1, S2, None
        )
        trust_state.open_observation(
            db, ROOT, ctx, route="trust-status", binding=BINDING, admin_digest=ADMIN,
            rules=RULES_VERSION, accepted=S1, head=S2, policy=None,
        )  # fmt: skip
    tw.checker.answer = lambda req: wire(verdict(req, outcome="unset", reason="x"))
    resp = status(world, capsys)
    assert [r["trust_head"] for r in tw.checker.requests] == [S2]
    assert resp.result == perimeter_result(
        "unavailable", f"{trust_routes.UNFINISHED}: 2", None, None
    ) | {"events": []}
    assert observations(world)[1:] == [
        ("trust-status", S1, S2, "unresolved"),
        ("perimeter-status", S1, S1, "open"),  # durable; evaluated next time
    ]


def test_head_moved_creates_observation(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """H ≠ A: an observation (A, H) of `perimeter-status` is committed before
    the checker runs; a clean verdict accepts H (CAS)."""
    accept(world.lay, S1, 2)
    tw.sources.heads["main"] = S2
    seen: list[list[tuple]] = []

    def answer(req: dict[str, object]) -> bytes:
        seen.append(observations(world))  # what is durable when it is asked
        return wire(verdict(req, last_epoch=3))

    tw.checker.answer = answer
    resp = status(world, capsys, S2)
    req = tw.checker.requests[0]
    assert req["trust_head"] == S2
    assert cast(dict[str, object], req["trust_accepted"])["sha"] == S1
    assert seen[0][-1] == ("perimeter-status", S1, S2, "open")
    assert observations(world)[-1] == ("perimeter-status", S1, S2, "accepted")
    assert head(world) == (S2, 3, 2)
    assert resp.result == {**perimeter_result("clear", None, S2, 3), "events": []}


def test_stale_snapshot_without_observation(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    accept(world.lay, S1, 2)
    resp = status(world, capsys, S2)
    assert (resp.outcome, resp.result) == ("stale_snapshot", {"snapshot_sha": S1})
    assert tw.checker.requests == [] and len(observations(world)) == 1


def test_first_head_cold_then_fast(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """A root seen for the first time: (None, H), `trust_accepted` null; once
    accepted, the next call takes the fast path."""
    resp = status(world, capsys)
    assert tw.checker.requests[0]["trust_accepted"] is None
    assert resp.result["state"] == "clear" and head(world) == (S1, 1, 1)
    status(world, capsys)
    assert len(tw.checker.requests) == 1


# --- the self-report on the fast path (Task 9's path) ----------------------------


def test_fast_path_token_write_not_clear(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The history token can write `approval-policy`: a `token_write` finding
    of the publisher -- no fast path, the finding registered, never clear."""
    accept(world.lay, S1, 2, binding=keyed(world, monkeypatch, tmp_path))
    tw.sources.permissions[POLICY_ID] = {
        "push": True,
        "maintain": False,
        "admin": False,
    }
    resp = status(world, capsys)
    assert resp.result["state"] == "blocked"
    assert ("registered", 2, "trust:perimeter-status", "approval-policy") in events(
        world
    )
    assert len(tw.checker.requests) == 1
    calls = tw.sources.calls
    assert calls and all(h["Authorization"] == f"Bearer {TOKEN}" for _u, h in calls)


def test_fast_path_permissions_missing_unavailable(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accept(world.lay, S1, 2, binding=keyed(world, monkeypatch, tmp_path))
    tw.sources.permissions[TRUST_ID] = {"push": False, "maintain": False}
    resp = status(world, capsys)
    assert resp.result["state"] == "unavailable"
    assert "admin not a bool" in str(resp.result["reason"])
    assert tw.checker.requests == [] and len(observations(world)) == 1


@pytest.mark.parametrize("code", [403, 404])
def test_fast_path_403_404_unavailable(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code: int,
) -> None:
    accept(world.lay, S1, 2, binding=keyed(world, monkeypatch, tmp_path))
    tw.sources.status[f"repositories/{POLICY_ID}"] = code
    resp = status(world, capsys)
    assert resp.result["state"] == "unavailable"
    assert f"HTTP {code}" in str(resp.result["reason"])
    assert tw.checker.requests == []


def test_fast_path_unreadable_profile_no_anonymous(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A key and an unreadable profile (no `hosts.yml`): `unavailable` from
    Task 9's `history_token` itself, and not one anonymous read."""
    profile = tmp_path / "history-gh"
    profile.mkdir()
    write_trust(world.lay, history=profile)
    monkeypatch.setattr(trust_routes, "approved_gh", lambda _s: Path("/opt/gh"))
    accept(world.lay, S1, 2)
    resp = status(world, capsys)
    assert resp.result["state"] == "unavailable"
    assert "no hosts.yml" in str(resp.result["reason"])
    assert tw.sources.calls == [] and tw.checker.requests == []
    resp = tstatus(world, capsys)
    assert (resp.result["state"], tw.sources.calls) == ("unavailable", [])


def test_keyed_binding_without_approved_gh_unavailable(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """No `gh` in `install.toml`: never `gh` from `PATH`, never anonymous."""
    write_trust(world.lay, history=tmp_path)
    resp = status(world, capsys)
    assert resp.result["reason"] == trust_routes.NO_APPROVED_GH
    assert tw.sources.calls == []


def test_approved_gh_checks_path_and_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`approved_gh` checks `gh` as `precheck serve` does: present in
    `install.toml`, a trusted path, the recorded sha256."""
    gh = tmp_path / "gh"
    gh.write_bytes(b"#!/bin/sh\n")
    snap = snapshot("rel-a", entry("rel-a"))
    got = trust_routes.approved_gh(snap)
    assert isinstance(got, Unavailable) and got.reason == trust_routes.NO_APPROVED_GH
    cli = {n: Path(f"/bin/{n}") for n in ("git", "uv", "tar")}
    digest = platform.file_sha256(gh)
    good = InstallConfig("o/m", "a" * 40, "core", {**cli, "gh": gh}, {"gh": digest})
    monkeypatch.setattr(platform, "trusted_path_problems", lambda _p: [])
    assert trust_routes.approved_gh(replace(snap, install=good)) == gh
    bad = InstallConfig("o/m", "a" * 40, "core", {**cli, "gh": gh}, {"gh": "0" * 64})
    got = trust_routes.approved_gh(replace(snap, install=bad))
    assert isinstance(got, Unavailable) and "sha256" in got.reason
    monkeypatch.setattr(platform, "trusted_path_problems", lambda _p: ["group write"])
    got = trust_routes.approved_gh(replace(snap, install=good))
    assert isinstance(got, Unavailable) and "not trusted" in got.reason


def test_no_history_key_anonymous_warning(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """O14: an unkeyed binding reads anonymously (no `Authorization`), and
    both routes carry `history_token_required` as a warning."""
    accept(world.lay, S1, 2)
    resp = status(world, capsys)
    assert resp.result["state"] == "clear"
    assert resp.evidence["warnings"] == ["history_token_required"]
    resp = tstatus(world, capsys)
    assert resp.result["unverified"] == ["history_token_required"]
    assert resp.evidence["warnings"] == ["history_token_required"]
    assert tw.sources.calls
    assert all("Authorization" not in h for _u, h in tw.sources.calls)


# --- trust-status ----------------------------------------------------------------


def test_trust_status_always_calls_checker(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """H = A, A valid, digest equal: `trust-status` still observes (A, A) and
    asks the checker (fresh settings)."""
    accept(world.lay, S1, 2)
    resp = tstatus(world, capsys)
    assert len(tw.checker.requests) == 1
    req = tw.checker.requests[0]
    assert (req["origin_route"], req["trust_head"]) == ("trust-status", S1)
    assert resp.result == {
        "root": {"host": "github.com", "repository_id": TRUST_ID, "branch": "main"},
        "snapshot_sha": S1,
        "last_epoch": 2,  # A's own: a re-check of A never rewrites it (D-d)
        "state": "clear",
        "reason": None,
        "unverified": ["history_token_required"],
        "findings": [],
    }


def test_trust_status_h_eq_a_removed_ruleset_finding(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec §10 3a: `trust-status` at H = A with a ruleset removed -- the
    finding is registered at the current epoch (the head of the branch is
    A in the same transaction), the root blocked, A kept at H."""
    accept(world.lay, S1, 2)
    tw.checker.answer = lambda req: wire(
        verdict(req, outcome="refuse", last_epoch=2, findings=[RULESET])
    )
    resp = tstatus(world, capsys)
    assert resp.result["state"] == "blocked"
    assert resp.result["findings"] == [
        {"kind": "ruleset_missing", "role": "df-trust", "detail": "no ruleset on main"}
    ]
    assert events(world) == [("registered", 2, "trust:trust-status", "df-trust")]
    assert observations(world)[-1] == ("trust-status", S1, S1, "finding_registered")
    assert head(world) == (S1, 2, 1)  # A re-checked: the row is not written
    again = tstatus(world, capsys)  # the same pair: the same event
    assert again.result["state"] == "blocked" and len(events(world)) == 1


def test_trust_status_without_binding(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    (world.lay.etc / "trust.toml").unlink()
    resp = tstatus(world, capsys)
    assert resp.outcome == "done"
    assert (resp.result["state"], resp.result["reason"]) == (
        "unavailable",
        "no_binding",
    )
    assert resp.result["root"] is None and tw.checker.requests == []


def test_root_validates_response_schema_and_root(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """Root checks the schema and that `result.root` is the binding it read
    itself; any other answer is `BadResponse`."""
    accept(world.lay, S1, 2)
    resp = tstatus(world, capsys)
    line = request.emit(resp)
    got = request.parse_trust_status(line, ROOT)
    assert (got.state, got.snapshot_sha, got.last_epoch) == ("clear", S1, 2)
    assert got.unverified == ("history_token_required",)
    with pytest.raises(BadResponse, match="root"):
        request.parse_trust_status(line, ("github.com", TRUST_ID, "next"))
    data = json.loads(line)
    for broken in (
        {**data, "schema": "df/publisher-response/v2"},
        {**data, "result": {**data["result"], "extra": 1}},
        {**data, "result": {**data["result"], "state": "fine"}},
        {**data, "result": {**data["result"], "snapshot_sha": None}},
        {**data, "result": {**data["result"], "findings": [{"kind": 1}]}},
        {**data, "outcome": "refused", "reason": "x"},
        # M3: strict types in the root
        {
            **data,
            "result": {
                **data["result"],
                "root": {**data["result"]["root"], "repository_id": 5.0},
            },
        },
        {
            **data,
            "result": {
                **data["result"],
                "root": {**data["result"]["root"], "repository_id": True},
            },
        },
    ):
        with pytest.raises(BadResponse):
            request.parse_trust_status(json.dumps(broken), ROOT)


# --- the verdict: outcomes and binding -------------------------------------------


@pytest.mark.parametrize(
    ("status_", "want"),
    [("tree_incomplete", "unresolved"), ("unset", "unresolved"),
     ("unsupported_size", "conservative")],
)  # fmt: skip
def test_verdict_outcome_mapping(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    status_: str,
    want: str,
) -> None:
    """`OUTCOME_OF_VERDICT`: `tree_incomplete` and `unset` keep the
    observation unfinished; `unsupported_size` completes it by K."""

    def answer(req: dict[str, object]) -> bytes:
        if status_ == "tree_incomplete":  # not canonical: parsed, not compared
            data = {
                "schema": trust_routes.VERDICT_SCHEMA_V2,
                "nonce": req["nonce"],
                "request_digest": trust_routes.precheck.request_digest(req),
                "outcome": "tree_incomplete",
                "reason": "tree_incomplete: reinstall the release",
                "version": trust_routes.PRECHECK_VERSION_V2,
                "admin_digest": ADMIN,
            }
            return (json.dumps(data) + "\n").encode()
        no_pair = {"relation": None, "last_epoch": None, "source": None}
        return wire(verdict(req, outcome=status_, reason=f"{status_}: x", **no_pair))

    tw.checker.answer = answer
    resp = tstatus(world, capsys)
    assert observations(world)[-1][3] == want
    if want == "unresolved":
        assert (resp.result["state"], resp.result["reason"]) == (
            "unavailable",
            f"{trust_routes.UNFINISHED}: 1",
        )
    else:
        assert resp.result["state"] == "blocked"
        assert events(world) == [("pending", None, "conservative_completion", None)]


@pytest.mark.parametrize(
    "over",
    [
        {"snapshot_sha": S3},
        {"accepted_sha": S3},
        {"root": {"host": "github.com", "df_trust_repository_id": 9, "branch": "main"}},
        {"ctx_id": "0" * 64},
        {"observation_id": 999},
        {"nonce": "0" * 32},
        {"request_digest": "0" * 64},
        {"admin_digest": "sha256:" + "b" * 64},
        {"validation": "df-trust-rules/2:x"},
        {"origin_route": "merge"},
        {"version": "df-precheck/3"},
        {"policy": {"full_name": POLICY_REPO, "branch": "main", "p_sha": S3}},
    ],
)
def test_trust_verdict_substituted_h_or_root_unbound(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    over: dict[str, object],
) -> None:
    """Every bound field, substituted alone: the verdict is unbound, the
    observation `unresolved`, H not accepted, the root not clear."""
    accept(world.lay, S1, 2)
    tw.sources.heads["main"] = S2
    tw.checker.answer = lambda req: wire(verdict(req, **over))
    resp = status(world, capsys, S2)
    assert observations(world)[-1] == ("perimeter-status", S1, S2, "unresolved")
    assert head(world) == (S1, 2, 1)
    assert resp.result["state"] == "unavailable"


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"findings": [RULESET]}, "outcome: allow"),  # allow with a finding
        ({"outcome": "refuse"}, "outcome: refuse"),  # refuse without one
        ({"source": None}, "shape: source"),  # null source, no source finding
        ({"relation": None}, "shape: no pair"),
        ({"unverified": []}, "unverified: history_token_required"),  # unkeyed
        ({"unverified": ["history_token_required", "x@y"]}, "unverified"),
        (
            {
                "outcome": "refuse",
                "findings": [Finding("policy_history_break", "approval-policy", "x")],
            },
            "findings: route kinds",
        ),  # fmt: skip
        ({"ranges": ranges_of(1, P)}, "ranges"),  # ranges off the merge route
    ],
)
def test_verdict_contract_unbound(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    kw: dict[str, object],
    rule: str,
) -> None:
    """The rules that tie the verdict's fields together (I6, O14, the
    outcome and its findings, route kinds, ranges only for `merge`)."""
    reasons: list[str] = []
    real = trust_routes.bind

    def spy(*a: object, **k: object) -> object:
        got = real(*a, **k)  # type: ignore[arg-type]
        reasons.append(getattr(got, "reason", ""))
        return got

    monkeypatch.setattr(trust_routes, "bind", spy)
    tw.checker.answer = lambda req: wire(verdict(req, **kw))
    tstatus(world, capsys)
    assert reasons == [f"precheck_failed: binding_mismatch: {rule}"]
    assert observations(world)[-1][3] == "unresolved"


def test_verdict_not_canonical_or_extra_key_unbound(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    tw.checker.answer = lambda req: (json.dumps(verdict(req)) + "\n").encode()
    tstatus(world, capsys)
    tw.checker.answer = lambda req: wire({**verdict(req), "extra": 1})
    tstatus(world, capsys)
    assert [o[3] for o in observations(world)] == ["unresolved"]  # one ctx, kept


@pytest.mark.parametrize(
    "result",
    [
        platform.SupervisedResult(1, None, b"Traceback (most recent call last):\n", False, 1.0),
        platform.SupervisedResult(2, None, b"", False, 0.2),
        platform.SupervisedResult(None, None, b"", False, 200.0),
        platform.SupervisedResult(0, None, b"x" * 10, True, 1.0),
        platform.SupervisedResult(0, None, b"", False, 1.0),
    ],
)  # fmt: skip
def test_failed_exit_or_no_verdict_unresolved(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    result: platform.SupervisedResult,
) -> None:
    """T10-2: a nonzero exit, a timeout, an oversize or a missing verdict is
    `unresolved` (a traceback is never parsed); the repeat re-evaluates."""
    tw.checker.answer = lambda _req: result
    resp = tstatus(world, capsys)
    assert observations(world) == [("trust-status", None, S1, "unresolved")]
    assert resp.result["state"] == "unavailable"
    tw.checker.answer = lambda req: wire(verdict(req))
    resp = tstatus(world, capsys)
    assert observations(world) == [("trust-status", None, S1, "accepted")]
    assert resp.result["state"] == "clear"


# --- K ---------------------------------------------------------------------------


def test_stale_context_is_conservative_not_unresolved(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unfinished observation of another check version (`admin_digest`):
    its inputs cannot be reproduced -- K, not an eternal `unresolved`."""
    with perimeter.open_db(world.lay) as db:
        old = "sha256:" + "0" * 64
        ctx = trust_state.ctx_id(
            "trust-status", BINDING, old, RULES_VERSION, None, S1, None
        )
        trust_state.open_observation(
            db, ROOT, ctx, route="trust-status", binding=BINDING, admin_digest=old,
            rules=RULES_VERSION, accepted=None, head=S1, policy=None,
        )  # fmt: skip
    tstatus(world, capsys)
    assert [o[3] for o in observations(world)] == ["conservative", "accepted"]
    assert events(world)[0][2] == "conservative_completion"


def test_binding_changed_during_check_is_conservative(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """K before advance (Task 8 M7): `trust.toml` rebound while the checker
    ran -- a conservative event, never an advance from the old binding."""

    def answer(req: dict[str, object]) -> bytes:
        write_trust(world.lay, branch="next")
        return wire(verdict(req))

    tw.checker.answer = answer
    tstatus(world, capsys)
    assert observations(world)[-1][3] == "conservative"
    assert head(world) is None


def test_unsupported_binding_prevalidated_without_checker(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D3: a binding branch over 255 bytes is the publisher's typed
    `unsupported_size: binding.branch`, terminal by K; the checker (whose
    exit 2 is only a backstop) is not asked."""
    long = "b" * 256
    write_trust(world.lay, branch=long)
    rec = replace(record("run-1", "rel-a"), root_id=("github.com", TRUST_ID, long))
    put_record(world.fake, rec)
    tw.sources.heads[long] = S1
    resp = tstatus(world, capsys)
    assert tw.checker.requests == []
    assert observations(world)[-1][3] == "conservative"
    assert resp.result["state"] == "blocked"


def test_limits_mirror_names_each_field() -> None:
    policy = PolicyPin(POLICY_REPO, "main", P)
    assert trust_routes.limits_problem(BINDING, policy) is None
    cases = {
        "binding.host": replace(BINDING, host="-bad-"),
        "binding.df_trust_repository_id": replace(
            BINDING, df_trust_repository_id=2**63
        ),
        "binding.df_trust_full_name": replace(
            BINDING, df_trust_full_name="o/" + "n" * 139
        ),
        "binding.branch": replace(BINDING, df_trust_branch="a..b"),
    }
    for field, binding in cases.items():
        assert trust_routes.limits_problem(binding, policy) == field
    assert trust_routes.limits_problem(BINDING, replace(policy, branch="-x")) == (
        "policy.branch"
    )
    assert (
        trust_routes.limits_problem(BINDING, replace(policy, full_name="o/" + "я" * 70))
        == "policy.full_name"
    )


# --- P and the ranges ------------------------------------------------------------


def test_pin_policy_by_id_default_branch(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """P by the binding's anchor: `GET /repositories/{id}` → full name and
    default branch → the head of that branch; sent on every `op: trust`."""
    tw.sources.policy["default_branch"] = "release"
    tw.sources.policy_head = S3
    tstatus(world, capsys)
    assert tw.checker.requests[0]["policy"] == {
        "full_name": POLICY_REPO,
        "branch": "release",
        "p_sha": S3,
    }
    paths = [u.removeprefix(f"{history.github.API}/") for u, _h in tw.sources.calls]
    assert f"repositories/{POLICY_ID}" in paths
    assert f"repos/{POLICY_REPO}/branches/release" in paths


def test_pin_policy_of_another_repository_unavailable(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    tw.sources.policy["id"] = 77
    resp = tstatus(world, capsys)
    assert tw.checker.requests == []
    assert observations(world)[-1][3] == "unresolved"
    assert resp.result["state"] == "unavailable"


def _merge_obs(world: World, policy: PolicyPin) -> int:
    """An unfinished `merge` observation (A = X = S1, P pinned)."""
    accept(world.lay, S1, 1)
    with perimeter.open_db(world.lay) as db:
        ctx = trust_state.ctx_id("merge", BINDING, ADMIN, RULES_VERSION, S1, S1, policy)
        obs = trust_state.open_observation(
            db, ROOT, ctx, route="merge", binding=BINDING, admin_digest=ADMIN,
            rules=RULES_VERSION, accepted=S1, head=S1, policy=policy,
        )  # fmt: skip
    return obs.id


def _merge_outcome(world: World, obs: int) -> str:
    return str(
        db_rows(
            world,
            f"SELECT outcome FROM trust_observations WHERE observation_id = {obs}",
        )[0][0]
    )


def test_ranges_correct_p_other_set_unbound(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-evaluation of a `merge` observation: its pinned P goes into the
    request; a descriptor ending at another P, or of another set of epochs
    than 1…E, is unbound; the correct one completes it (no `PUT`, ever)."""
    pin = PolicyPin(POLICY_REPO, "main", P)
    obs = _merge_obs(world, pin)

    def other_end(req: dict[str, object]) -> bytes:
        return wire(verdict(req, ranges=ranges_of(1, S3)))

    tw.checker.answer = other_end
    tstatus(world, capsys)
    merge_req = tw.checker.requests[0]
    assert (merge_req["origin_route"], merge_req["policy"]) == (
        "merge",
        {"full_name": POLICY_REPO, "branch": "main", "p_sha": P},
    )
    assert _merge_outcome(world, obs) == "unresolved"
    tw.checker.answer = lambda req: wire(verdict(req, ranges=ranges_of(2, P)))
    tstatus(world, capsys)
    assert _merge_outcome(world, obs) == "unresolved"
    tw.checker.answer = lambda req: wire(verdict(req))
    tstatus(world, capsys)
    assert _merge_outcome(world, obs) == "accepted"
    pinned = db_rows(
        world,
        f"SELECT ranges_digest FROM trust_observations WHERE observation_id = {obs}",
    )
    assert pinned == [(ranges_of(1, P)["digest"],)]


def test_ranges_reeval_digest_mismatch_unbound(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """The digest pinned at the first bound verdict must hold on
    re-evaluation (the inputs are immutable)."""
    pin = PolicyPin(POLICY_REPO, "main", P)
    obs = _merge_obs(world, pin)
    with perimeter.open_db(world.lay) as db:
        trust_state.pin_ranges(db, ROOT, obs, "c" * 64)
    tw.checker.answer = lambda req: wire(verdict(req))
    tstatus(world, capsys)
    assert _merge_outcome(world, obs) == "unresolved"


def test_check_ranges_rules() -> None:
    rows = ((1, "a" * 40, "b" * 40), (2, "c" * 40, P))
    good = RangeDescriptor(rows, range_digest(rows))
    assert check_ranges(good, last_epoch=2, p_sha=P, stored_digest=None)
    assert check_ranges(good, last_epoch=2, p_sha=P, stored_digest=good.digest)
    assert not check_ranges(good, last_epoch=2, p_sha=S3, stored_digest=None)
    assert not check_ranges(good, last_epoch=3, p_sha=P, stored_digest=None)
    assert not check_ranges(good, last_epoch=2, p_sha=P, stored_digest="0" * 64)
    swapped = ((2, "c" * 40, P), (1, "a" * 40, "b" * 40))
    assert not check_ranges(
        RangeDescriptor(swapped, range_digest(swapped)), last_epoch=2, p_sha=P,
        stored_digest=None,
    )  # fmt: skip
    assert not check_ranges(
        RangeDescriptor(rows, "0" * 64), last_epoch=2, p_sha=P, stored_digest=None
    )


# --- admission of a restored database -------------------------------------------


def _restored(world: World) -> None:
    """Mark the world's database a restored copy (`restored_pending`)."""
    conn = sqlite3.connect(world.lay.perimeter_dir / perimeter.DB_NAME)
    conn.execute("UPDATE meta SET value = 'restored_pending' WHERE key = 'admission'")
    conn.execute("UPDATE meta SET value = 'true' WHERE key = 'restored'")
    conn.commit()
    conn.close()


def test_restored_copy_admitted_by_the_verified_head(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """A copy whose A is the branch head: the conservative event of the
    restore is `registered` at A's epoch, and the copy is admitted."""
    accept(world.lay, S1, 4)
    _restored(world)
    resp = status(world, capsys)
    assert resp.result["state"] == "blocked"
    assert events(world) == [("registered", 4, "db_restore", None)]


def test_restore_empty_admitted_pending_then_registered(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-b: an empty restore (no accepted head) is admitted at once with a
    `pending` conservative event; the root is never `clear` before that
    event is registered at the first verified epoch, then blocked by it."""
    _restored(world)
    seen: list[str] = []

    def answer(req: dict[str, object]) -> bytes:
        seen.append(str(db_rows(world, "SELECT state FROM events")))
        return wire(verdict(req, last_epoch=3))

    tw.checker.answer = answer
    resp = status(world, capsys)
    assert seen == ["[('pending',)]"]  # admitted before the checker ran
    assert resp.result["state"] == "blocked"
    assert events(world)[0] == ("registered", 3, "db_restore", None)
    assert resp.result["last_epoch"] == 3


def test_restore_moved_head_admitted_pending(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-b: the copy's A is no longer the branch head: `pending`, admitted;
    the observation (A, H) proceeds and the event blocks at H's epoch."""
    accept(world.lay, S1, 4)
    _restored(world)
    tw.sources.heads["main"] = S2
    tw.checker.answer = lambda req: wire(verdict(req, last_epoch=5))
    resp = status(world, capsys, S2)
    assert events(world)[0][:3] == ("registered", 5, "db_restore")
    assert resp.result["state"] == "blocked"


def test_restore_copy_with_old_root_admitted(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-b: a copy that knows an old root (after `--rebind`): its event is
    `pending` for good (the old root gets no runs), the bound root's is
    registered, and the copy is admitted."""
    old = replace(BINDING, df_trust_repository_id=9)
    accept(world.lay, S1, 4, binding=old)
    accept(world.lay, S1, 4)
    _restored(world)
    resp = status(world, capsys)
    assert resp.result["state"] == "blocked"
    rows = db_rows(
        world,
        "SELECT root_repository_id, state, epoch FROM events "
        "WHERE detected_by = 'db_restore' ORDER BY root_repository_id",
    )
    assert rows == [(TRUST_ID, "registered", 4), (9, "pending", None)]


# --- the frame wiring ------------------------------------------------------------


def test_main_routes_to_trust_routes() -> None:
    from df.publisher import main as pmain

    table = pmain.OPERATION_TABLE
    assert table["perimeter-status"] is trust_routes.route_perimeter_status
    assert table["trust-status"] is trust_routes.route_trust_status


def test_session_reads_with_one_token(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The self-report and every read of a call carry the same token value
    (Task 9: one transport)."""
    keyed(world, monkeypatch, tmp_path)
    tstatus(world, capsys)
    auth = {h.get("Authorization") for _u, h in tw.sources.calls}
    assert auth == {f"Bearer {TOKEN}"}
    req = tw.checker.requests[0]
    binding = cast(dict[str, object], req["binding"])
    assert binding["history_gh_config_dir"] == str(tmp_path / "history-gh")
    assert tw.checker.requests and all(
        "ghp_" not in json.dumps(r) for r in tw.checker.requests
    )


def test_accepted_record_sent_back_verbatim(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D6 (ruling R1): `trust_accepted.validation` is the `validation` every
    bound verdict carries -- `<rules>:<admin_digest>` of the stored check."""
    accept(world.lay, S1, 2)
    tw.sources.heads["main"] = S2
    status(world, capsys, S2)
    sent = cast(dict[str, object], tw.checker.requests[0]["trust_accepted"])
    assert sent == {
        "sha": S1,
        "last_epoch": 2,
        "source_digest": SOURCE_DIGEST,
        "validation": f"{RULES_VERSION}:{ADMIN}",
    }


def test_binding_parameters_of_another_root(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """`perimeter-status` of a root other than the binding's is refused
    before any read (`root_mismatch`); `trust-status` names the binding."""
    raw = raw_of(
        "perimeter-status",
        request_id="req-other-root",
        root_id={"host": "github.com", "repository_id": 9, "branch": "main"},
    )
    code, resp = world.run("perimeter-status", raw, capsys=capsys)
    assert (code, resp.reason) == (2, "root_mismatch")
    assert tw.sources.calls == [] and tw.checker.requests == []


@pytest.mark.parametrize(
    ("over", "rule"),
    [
        # a wrong rules prefix, the publisher's admin digest
        ({"validation": f"df-trust-rules/2:{ADMIN}"}, "validation"),
        # the right prefix, another digest in the suffix (review M7)
        ({"validation": f"{RULES_VERSION}:sha256:" + "b" * 64}, "validation"),
        # another admin digest, its validation consistent with it
        (
            {
                "admin_digest": "sha256:" + "b" * 64,
                "validation": f"{RULES_VERSION}:sha256:" + "b" * 64,
            },
            "admin_digest",
        ),
    ],
)
def test_validation_pair_mismatch_unbound(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    over: dict[str, object],
    rule: str,
) -> None:
    """Ruling R1 (amends D6): the verdict binds on `validation ==
    "<rules>:<admin_digest>"` and on `admin_digest` = the publisher's; either
    mismatch is unbound, the observation `unresolved`, H not accepted."""
    reasons: list[str] = []
    real = trust_routes.bind

    def spy(*a: object, **k: object) -> object:
        got = real(*a, **k)  # type: ignore[arg-type]
        reasons.append(getattr(got, "reason", ""))
        return got

    monkeypatch.setattr(trust_routes, "bind", spy)
    accept(world.lay, S1, 2)
    tw.sources.heads["main"] = S2
    tw.checker.answer = lambda req: wire(verdict(req, **over))
    status(world, capsys, S2)
    assert reasons == [f"precheck_failed: binding_mismatch: {rule}"]
    assert observations(world)[-1] == ("perimeter-status", S1, S2, "unresolved")
    assert head(world) == (S1, 2, 1)


# --- review round 1 --------------------------------------------------------------


def _open_pair(world: World, route: str, accepted: str | None, head_sha: str) -> int:
    """An unfinished observation of `route` (a lost answer), opened directly."""
    with perimeter.open_db(world.lay) as db:
        ctx = trust_state.ctx_id(
            route,
            BINDING,
            ADMIN,
            RULES_VERSION,
            accepted,
            head_sha,
            None,  # type: ignore[arg-type]
        )
        obs = trust_state.open_observation(
            db, ROOT, ctx, route=route, binding=BINDING, admin_digest=ADMIN,  # type: ignore[arg-type]
            rules=RULES_VERSION, accepted=accepted, head=head_sha, policy=None,
        )  # fmt: skip
    return obs.id


def _outcome(world: World, obs: int) -> str:
    sql = f"SELECT outcome FROM trust_observations WHERE observation_id = {obs}"
    return str(db_rows(world, sql)[0][0])


@pytest.mark.parametrize("finding", [None, RULESET])
def test_common_a_second_superseded_never_conservative(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    finding: Finding | None,
) -> None:
    """C1 / D-a: two unfinished observations with a common A. The first is
    accepted (A moves); the second is still asked on its pinned A record,
    its findings registered, and its CAS ends `superseded` (or
    `finding_registered`) -- never K, no `conservative_completion`."""
    accept(world.lay, S1, 2)
    tw.sources.heads["main"] = S2
    first = _open_pair(world, "perimeter-status", S1, S2)
    second = _open_pair(world, "trust-status", S1, S2)

    def answer(req: dict[str, object]) -> bytes:
        sent = cast(dict[str, object], req["trust_accepted"])
        assert (sent["sha"], sent["last_epoch"]) == (S1, 2)  # the pinned record
        if req["observation_id"] == second and finding is not None:
            return wire(
                verdict(req, outcome="refuse", last_epoch=3, findings=[finding])
            )
        return wire(verdict(req, last_epoch=3))

    tw.checker.answer = answer
    tstatus(world, capsys)
    assert _outcome(world, first) == "accepted"
    want = "superseded" if finding is None else "finding_registered"
    assert _outcome(world, second) == want
    detected = [e[2] for e in events(world)]
    assert "conservative_completion" not in detected
    assert head(world) == (S2, 3, 2)


def test_reaccept_same_sha_epoch_bound(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-d: re-checking A (H = A), the publisher knows A's epoch and digest;
    a verdict with another epoch is unbound, A's row is never rewritten."""
    accept(world.lay, S1, 2)
    tw.checker.answer = lambda req: wire(verdict(req, last_epoch=5))
    resp = tstatus(world, capsys)
    assert observations(world)[-1][3] == "unresolved"
    assert head(world) == (S1, 2, 1)
    assert resp.result["state"] == "unavailable"


def test_merge_reeval_at_x_eq_a_keeps_row_version(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-d / I4: a merge observation re-evaluated at X = A, and the call's
    own (A, A), are accepted without a write: `row_version` unchanged, so no
    sibling observation pinned at it is superseded."""
    obs = _merge_obs(world, PolicyPin(POLICY_REPO, "main", P))
    sibling = _open_pair(world, "perimeter-status", S1, S2)
    tw.checker.answer = lambda req: (
        wire(
            verdict(
                req,
                outcome="unset",
                reason="x",
                relation=None,
                last_epoch=None,
                source=None,
            )
        )  # fmt: skip
        if req["observation_id"] == sibling
        else wire(verdict(req))
    )
    tstatus(world, capsys)
    assert _merge_outcome(world, obs) == "accepted"
    assert head(world) == (S1, 1, 1)
    assert _outcome(world, sibling) == "unresolved"  # still pinned at version 1
    with perimeter.open_db(world.lay) as db:
        pending = trust_state.unfinished(db.conn, ROOT)
    assert [o.row_version for o in pending] == [1, 1]  # the sibling and own


def test_deleted_p_objects_conservative(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-c: re-evaluating a merge observation whose pinned P's objects are
    gone -- the checker's typed `irreproducible` -- is K with the reason
    named in the event, not an eternal `unresolved`."""
    obs = _merge_obs(world, PolicyPin(POLICY_REPO, "main", P))
    reason = "irreproducible: policy_objects_missing"
    tw.checker.answer = lambda req: (
        wire(verdict(req, outcome="irreproducible", reason=reason, ranges=None))
        if req["origin_route"] == "merge"
        else wire(verdict(req))
    )
    resp = tstatus(world, capsys)
    assert _merge_outcome(world, obs) == "conservative"
    found = db_rows(world, "SELECT detected_by, detail FROM events ORDER BY event_id")
    assert found[0][0] == "conservative_completion" and reason in found[0][1]
    assert resp.result["state"] == "blocked"


def test_pin_mismatch_conservative(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-c: the policy pin and `source.env` at H disagree for good
    (`irreproducible: policy_pin_mismatch`, no pair read): K."""
    reason = "irreproducible: policy_pin_mismatch"
    no_pair = {"relation": None, "last_epoch": None, "source": None}
    tw.checker.answer = lambda req: wire(
        verdict(req, outcome="irreproducible", reason=reason, **no_pair)
    )
    tstatus(world, capsys)
    assert observations(world)[-1][3] == "conservative"


def test_irreproducible_reason_closed(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reason outside the closed set is unbound: `unresolved`, not K."""
    tw.checker.answer = lambda req: wire(
        verdict(req, outcome="irreproducible", reason="irreproducible: other")
    )
    tstatus(world, capsys)
    assert observations(world)[-1][3] == "unresolved"


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "ruleset_missing", "role": "df-trust", "detail": 5},
        {"kind": "ruleset_missing", "role": "df-trust", "detail": ["x"]},
        {"kind": 1, "role": "df-trust", "detail": "x"},
    ],
)
def test_finding_fields_typed(
    world: World,
    tw: TrustWorld,
    capsys: pytest.CaptureFixture[str],
    row: dict[str, object],
) -> None:
    """M1: a finding's kind, role and detail are strings, or it is unbound."""
    tw.checker.answer = lambda req: wire(
        {**verdict(req, outcome="refuse", findings=[RULESET]), "findings": [row]}
    )
    tstatus(world, capsys)
    assert observations(world)[-1][3] == "unresolved"
    assert events(world) == []


def test_pin_policy_out_of_limits_typed(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """M2: a default branch outside the limits is the typed
    `unsupported_size: policy.branch` -- no placeholder P is ever read or
    sent, the checker not asked, the observation completed by K."""
    tw.sources.policy["default_branch"] = "-bad"
    resp = tstatus(world, capsys)
    assert tw.checker.requests == []
    paths = [u for u, _h in tw.sources.calls]
    assert not any("branches/-bad" in u for u in paths)
    assert observations(world)[-1][3] == "conservative"
    assert resp.result["state"] == "blocked"
    ctx = SimpleNamespace(layout=world.lay, snapshot=world.snap)
    sess = trust_routes.session(cast(Any, ctx), BINDING)
    assert not isinstance(sess, Unavailable)
    got = trust_routes.pin_policy(sess.transport, sess)
    assert got == trust_routes.OutOfLimits("policy.branch")


def test_evaluations_capped_per_call(
    world: World, tw: TrustWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5: one call asks the checker at most `MAX_EVALUATIONS` times; the
    rest stay unfinished (the root `unavailable` by I) for the next calls."""
    heads = ["4" * 40, "5" * 40, "6" * 40, "7" * 40]
    opened = [_open_pair(world, "trust-status", None, h) for h in heads]
    tw.checker.answer = lambda req: wire(verdict(req, outcome="refuse",
                                                 findings=[RULESET]))  # fmt: skip
    resp = tstatus(world, capsys)
    assert len(tw.checker.requests) == trust_routes.MAX_EVALUATIONS
    left = [o for o in opened if _outcome(world, o) == "open"]
    assert left == opened[trust_routes.MAX_EVALUATIONS :]
    assert resp.result["state"] == "blocked"  # the registered findings
