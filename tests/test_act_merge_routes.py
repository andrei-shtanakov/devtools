"""The publisher's `op: merge` against the real checker (stage 3a, Task 11).

`df.publisher.precheck` and `trust_routes` build the request (the PR's v1
part and the v2 keys of the `merge` observation) and parse and bind the
verdict (stdlib, `df` never imports `governance`); here the request goes
through the real `act_precheck.serve` on the `act_precheck_v2` stand and its
stdout is bound by the publisher's own code: the two sides meet on bytes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("steward")

from df.publisher import precheck, trust_routes  # noqa: E402
from df.publisher.precheck import PrFacts  # noqa: E402
from df.publisher.trust_routes import MergeTrust, Session, Verdict  # noqa: E402
from df.publisher.trust_state import Observation, Pinned  # noqa: E402
from df.publisher.trust_types import PolicyPin  # noqa: E402
from df.stage2_config import TrustBinding  # noqa: E402
from governance import act_precheck_v2  # noqa: E402
from governance.act_precheck import RequestV2, serve  # noqa: E402
from tests import test_act_precheck_v2 as v2  # noqa: E402
from tests.test_act_history_snapshot import API, SLUG, WS  # noqa: E402
from tests.test_act_precheck_v2 import (  # noqa: E402
    ADMIN,
    AP,
    BRANCH,
    CHANGES,
    CTX,
    DIGEST,
    DT,
    GIT,
    HEAD,
    PRODUCT_TOKEN,
    RULES,
    Candidate,
    P,
    X,
    _Wire,
    deps,
)

#: The stand's fixtures (the trust sources, the approved `gh`, the profile,
#: the product side of a candidate).
wire, gh, profile, candidate = v2.wire, v2.gh, v2.profile, v2.candidate

OBS = 7
MERGER = "df-reviewer[bot]"


def session(history: Path | None) -> Session:
    """The publisher's session; `bind_merge` reads its root, key and admin
    digest only (no context, no transport)."""
    binding = TrustBinding(
        "github.com", 11, DT, "main", 22, "public",
        None if history is None else str(history),
    )  # fmt: skip
    return Session(cast(Any, None), binding, ADMIN, cast(Any, None), None)


def observation() -> Observation:
    """The `merge` observation (A = X, X, P), A's record pinned."""
    pinned = Pinned(X, 1, DIGEST, trust_routes.RULES_VERSION, ADMIN)
    policy = PolicyPin(AP, "main", P)
    return Observation(
        OBS, ("github.com", 11, "main"), CTX, "merge", X, X, policy, 1, pinned
    )


def request(sess: Session) -> tuple[dict[str, object], MergeTrust]:
    """The publisher's request: `precheck._request` of the PR and the keys of
    `trust_routes.merge_fields`."""
    oc = SimpleNamespace(
        product=SimpleNamespace(repository_id=42, full_name=SLUG, dag_variant="full"),
        api_base=API,
        git_base="http://127.0.0.1:9",
    )
    changes = tuple((s, p, prev) for s, p, prev in CHANGES)
    facts = PrFacts(42, BRANCH, "master", "c" * 40, changes)
    trust = MergeTrust(sess, observation(), MERGER, f"workstreams/{WS}/spec")
    v1 = precheck._request("candidate", cast(Any, oc), 7, HEAD, facts, PRODUCT_TOKEN)
    return {**v1, **trust_routes.merge_fields(trust, "full")}, trust


def ask(
    home: Path, sess: Session, gh_path: Path | None
) -> tuple[dict[str, object], Verdict | Any]:
    """The publisher's request through the real `serve`; the verdict parsed
    and bound by the publisher."""
    req, trust = request(sess)
    home.mkdir(exist_ok=True)
    out = io.StringIO()
    code = serve(
        io.BytesIO(json.dumps(req).encode()),
        out,
        admin_digest=ADMIN,
        home=home,
        complete_v2=True,
        gh=gh_path,
        git=GIT,
        deps=deps(),
    )
    assert code == 0
    raw = out.getvalue().encode("utf-8")
    parsed = trust_routes.parse_verdict(raw, trust_routes.MERGE_KEYS)
    assert parsed is not None, raw
    return req, trust_routes.bind_merge(parsed, req, trust=trust)


def test_candidate_allowed_bound_by_the_publisher(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    _req, got = ask(tmp_path / "home", session(profile), gh)
    assert isinstance(got, Verdict), got
    assert (got.outcome, got.relation, got.last_epoch) == ("allow", "descendant", 1)
    assert got.verified and got.ranges is not None
    assert got.ranges.ranges[-1][2] == P


def test_unkeyed_refusal_without_findings_bound(
    tmp_path: Path, wire: _Wire, gh: Path
) -> None:
    """Note 1: `op: merge` refuses without findings (O14 here); the shape
    has no pair and the warning is bound both ways."""
    got = ask(tmp_path / "home", session(None), gh)[1]
    assert isinstance(got, Verdict), got
    assert (got.outcome, got.relation, got.findings) == ("refuse", None, ())
    assert got.unverified == ("history_token_required",)


def test_settings_finding_bound_with_its_reason(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    wire.routes[f"repos/{DT}/rules/branches/main"] = (200, RULES[:2])
    got = ask(tmp_path / "home", session(profile), gh)[1]
    assert isinstance(got, Verdict), got
    assert got.outcome == "refuse"
    assert [(f.kind, f.role) for f in got.findings] == [("ruleset_missing", "df-trust")]
    assert got.reason == "perimeter_findings: ruleset_missing@df-trust"
    assert got.ranges is not None  # X verified: the ranges on a refusal too


def test_another_x_is_unbound(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """The verdict of the real checker for this request does not bind to an
    observation of another X."""
    req, trust = request(session(profile))
    home = tmp_path / "home"
    home.mkdir()
    out = io.StringIO()
    serve(
        io.BytesIO(json.dumps(req).encode()), out, admin_digest=ADMIN, home=home,
        complete_v2=True, gh=gh, git=GIT, deps=deps(),
    )  # fmt: skip
    parsed = trust_routes.parse_verdict(
        out.getvalue().encode("utf-8"), trust_routes.MERGE_KEYS
    )
    assert parsed is not None
    other = Observation(OBS, trust.obs.root, CTX, "merge", "4" * 40, "4" * 40,
                        trust.obs.policy, 1, trust.obs.pinned)  # fmt: skip
    got = trust_routes.bind_merge(
        parsed, req, trust=MergeTrust(trust.sess, other, MERGER, trust.bundle_dir)
    )
    assert not isinstance(got, Verdict)
    assert got.reason == "precheck_failed: binding_mismatch: expected_snapshot_sha"


def test_merge_contract_mirrored() -> None:
    """The publisher's mirror of the `op: merge` contract, pinned to the
    checker: the request's keys, the preliminary state, the merger type,
    the outcomes and the envelope's keys."""
    req, _trust = request(session(Path("/h")))
    assert set(req) == set(act_precheck_v2._COMMON) | set(act_precheck_v2._MERGE)
    assert trust_routes.PRELIMINARY == act_precheck_v2.PRELIMINARY
    assert trust_routes.MERGER_TYPE in act_precheck_v2.MERGER_TYPES
    assert set(trust_routes.MERGE_STATUSES) == {
        act_precheck_v2.ALLOW,
        act_precheck_v2.REFUSE,
        act_precheck_v2.UNSET,
        act_precheck_v2.UNSUPPORTED,
    }
    assert trust_routes.FINDINGS_REASON == act_precheck_v2._findings_reason(())
    parsed = act_precheck_v2.parse(
        RequestV2(str(req["nonce"]), precheck.request_digest(req), req)
    )
    got = act_precheck_v2.Answer(act_precheck_v2.UNSET, "deadline")
    envelope = json.loads(act_precheck_v2._wire(parsed, got, ADMIN))
    assert set(envelope) == trust_routes.MERGE_KEYS
