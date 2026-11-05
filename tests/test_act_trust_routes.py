"""The publisher's `op: trust` against the real checker (stage 3a, Task 10).

`df.publisher.trust_routes` builds the request and parses and binds the
verdict (stdlib, `df` never imports `governance`); here the request goes
through the real `act_precheck.serve` on the `act_precheck_v2` stand and
its stdout is bound by the publisher's own code: the two sides of the
protocol meet on bytes, not on a fake.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("steward")

from df.publisher import trust_routes  # noqa: E402
from df.publisher.trust_routes import Session, Verdict  # noqa: E402
from df.publisher.trust_state import Observation  # noqa: E402
from df.publisher.trust_types import AcceptedBase, PolicyPin  # noqa: E402
from df.stage2_config import TrustBinding  # noqa: E402
from governance.act_precheck import serve  # noqa: E402
from tests import test_act_precheck_v2 as v2  # noqa: E402
from tests.test_act_precheck_v2 import (  # noqa: E402
    ADMIN,
    AP,
    C0,
    CTX,
    DIGEST,
    DT,
    GIT,
    P,
    X,
    _Wire,
    deps,
)

#: The stand's fixtures (the trust sources, the approved `gh`, the profile).
wire, gh, profile = v2.wire, v2.gh, v2.profile

OBS = 7


def binding(history: Path | None) -> TrustBinding:
    return TrustBinding(
        "github.com", 11, DT, "main", 22, "public",
        None if history is None else str(history),
    )  # fmt: skip


def session(history: Path | None) -> Session:
    """The publisher's session; `bind` reads its root, key and admin digest
    only (no context, no transport)."""
    return Session(cast(Any, None), binding(history), ADMIN, cast(Any, None), None)


def observation(head: str, accepted: str | None) -> Observation:
    return Observation(OBS, ("github.com", 11, "main"), CTX, "trust-status",
                       accepted, head, None, 1)  # fmt: skip


def ask(
    home: Path, sess: Session, obs: Observation, gh_path: Path | None, *,
    complete: bool = True,
) -> tuple[dict[str, object], Verdict | Any]:  # fmt: skip
    """The publisher's request through the real `serve`; the verdict bound
    by the publisher."""
    base = None
    if obs.accepted is not None:
        validation = trust_routes.validation(trust_routes.RULES_VERSION, ADMIN)
        base = AcceptedBase(obs.accepted, 1, DIGEST, validation)
    policy = PolicyPin(AP, "main", P)
    req = trust_routes.trust_request(sess, obs, base, policy)
    home.mkdir(exist_ok=True)
    out = io.StringIO()
    code = serve(
        io.BytesIO(json.dumps(req).encode()),
        out,
        admin_digest=ADMIN,
        home=home,
        complete_v2=complete,
        gh=gh_path,
        git=GIT,
        deps=deps(),
    )
    assert code == 0
    parsed = trust_routes.parse_verdict(out.getvalue().encode("utf-8"))
    assert parsed is not None, out.getvalue()
    got = trust_routes.bind(parsed, req, obs=obs, sess=sess, stored_ranges=None)
    return req, got


def test_clean_pair_bound_by_the_publisher(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
) -> None:
    sess = session(profile)
    _req, got = ask(tmp_path / "home", sess, observation(X, C0), gh)
    assert isinstance(got, Verdict), got
    assert (got.outcome, got.relation, got.last_epoch) == ("allow", "descendant", 1)
    assert got.verified and got.source is not None and got.source.digest == DIGEST
    assert got.unverified == ()


def test_unkeyed_pair_bound_with_the_warning(
    tmp_path: Path,
    wire: _Wire,
) -> None:
    got = ask(tmp_path / "home", session(None), observation(X, C0), None)[1]
    assert isinstance(got, Verdict), got
    assert got.unverified == ("history_token_required",)


def test_rollback_shape_bound_source_null(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
) -> None:
    """A gone H: `relation = not_descendant`, `history_unverifiable`, null
    `source` -- the I6 shape the publisher accepts; H not verified."""
    gone = "9" * 40
    wire.routes[f"repos/{DT}/commits/{gone}"] = (404, {"message": "Not Found"})
    got = ask(tmp_path / "home", session(profile), observation(gone, C0), gh)[1]
    assert isinstance(got, Verdict), got
    assert got.outcome == "refuse" and not got.verified
    assert [(f.kind, f.role) for f in got.findings] == [
        ("history_unverifiable", "df-trust")
    ]


def test_tree_incomplete_classified_first(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
) -> None:
    """T10-1: the dispatcher's `tree_incomplete` (not canonical bytes) is
    parsed and bound by `nonce` and `request_digest` only."""
    sess = session(profile)
    got = ask(tmp_path / "home", sess, observation(X, C0), gh, complete=False)[1]
    assert isinstance(got, Verdict) and got.outcome == "tree_incomplete"


def test_substituted_observation_unbound(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
) -> None:
    """The real verdict of one observation does not bind another."""
    sess = session(profile)
    obs = observation(X, C0)
    req, _ = ask(tmp_path / "home", sess, obs, gh)
    out = io.StringIO()
    serve(
        io.BytesIO(json.dumps(req).encode()), out, admin_digest=ADMIN,
        home=tmp_path / "home", complete_v2=True, gh=gh, git=GIT, deps=deps(),
    )  # fmt: skip
    parsed = trust_routes.parse_verdict(out.getvalue().encode("utf-8"))
    assert parsed is not None
    other = Observation(OBS + 1, obs.root, obs.ctx, obs.route, obs.accepted,
                        obs.head, None, 1)  # fmt: skip
    got = trust_routes.bind(parsed, req, obs=other, sess=sess, stored_ranges=None)
    assert not isinstance(got, Verdict)
    assert got.reason == "precheck_failed: binding_mismatch: observation_id"
