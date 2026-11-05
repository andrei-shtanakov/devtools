"""`act_precheck_v2`: protocol v2 of the df precheck program (stage 3a, Task 7).

The stand: `df-trust` and `approval-policy` replayed over the real `AnonTransport`
(`act_anon._default_open` replaced by a route table), the history token read by a
fake approved `gh` (an absolute script path, the df launch contract), the product
repository a real git bare repo over dumb HTTP with a replayed REST forge (from
`tests/test_act_precheck.py`). The request goes in as bytes on stdin of
`act_precheck.serve`, the verdict comes out as one line of canonical JSON.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from df.admin_tree import LEGACY_2B  # noqa: E402
from governance import (  # noqa: E402
    act_anon,
    act_context,
    act_epochs,
    act_facts,
    act_precheck,
    act_precheck_v2,
    act_premerge_core,
    act_trust,
    act_trust_findings,
)
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import (  # noqa: E402
    Accepted,
    Rejected,
    ReviewView,
    WaitingDelivery,
)
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    envelope_path,
    expected_envelope,
    render_envelope,
)
from governance.act_forge_http import TokenTransport  # noqa: E402
from governance.act_history_snapshot import open_snapshot  # noqa: E402
from governance.act_precheck import PrecheckDeps, request_digest, serve  # noqa: E402
from governance.act_request import manifest_path  # noqa: E402
from governance.act_time import Unavailable, parse_ts  # noqa: E402
from governance.act_trust_types import Finding  # noqa: E402
from tests.test_act_history_snapshot import (  # noqa: E402
    API,
    GIT,
    SLUG,
    WS,
    Forge,
    GitServer,
)
from tests.test_act_precheck import Repo, _closure, _request  # noqa: E402
from tests.test_act_premerge import ACT, POLICY, manifest  # noqa: E402
from tests.test_act_trust import (  # noqa: E402
    AP,
    DT,
    J1,
    J2,
    SOURCE_ENV,
    _perms,
    _Wire,
)
from tests.test_act_trust import TOKEN as HISTORY_TOKEN  # noqa: E402
from tests.test_act_trust_pair import world as pair_world  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_LOAD_RANGES = act_trust.load_ranges
NONCE = "0123456789abcdef0123456789abcdef"
ADMIN = "a" * 64
CTX = "c" * 64
OBS = 7
C0, X = "0" * 40, "1" * 40
P = "f" * 40
HEAD = "e" * 40
PRODUCT_TOKEN = "ghs_PRODUCT-token-1"
BRANCH = "spec/ws-7-approve-2-1-1"
NOW = parse_ts("2026-10-12T12:00:00Z")
DIGEST = hashlib.sha256(SOURCE_ENV.encode()).hexdigest()
VALIDATION = act_precheck_v2.validation_of(ADMIN)
EVIDENCE = f"workstreams/{WS}/evidence/edge-check/requirements--charter.json"
NODE_FILE = f"workstreams/{WS}/spec/10-requirements.md"
CHANGES = [
    ["modified", NODE_FILE, None],
    ["added", manifest_path(WS, "approve-2-1-1"), None],
    ["added", EVIDENCE, None],
]
RULES = [{"type": k} for k in ("deletion", "non_fast_forward", "pull_request")]


# --- the stand -----------------------------------------------------------------------


def trust_routes(journal_x: str = J1, **repo_extra: object) -> dict:
    """`df-trust` C0 → X (journal J1, or `journal_x` on X), both repos public,
    rules and settings clean, `permissions` of the history token read-only."""
    routes = pair_world({C0: (None, J1), X: (C0, journal_x)})
    for repo in (DT, AP):
        routes[f"repos/{repo}/rules/branches/main"] = (200, RULES)
        body = {"full_name": repo, "allow_rebase_merge": False, **_perms()}
        routes[f"repos/{repo}"] = (200, body | repo_extra)
    return routes


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    """The trust sources: every `AnonTransport` without `open_url` reads them."""
    routes = _Wire(trust_routes())
    monkeypatch.setattr(act_anon, "_default_open", lambda: routes)
    return routes


@pytest.fixture()
def gh(tmp_path: Path) -> Path:
    """The approved `gh`: an absolute script; `auth token` prints the history token."""
    script = tmp_path / "bin" / "gh"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = auth ]; then echo {HISTORY_TOKEN}; else echo "{{}}"; fi\n'
    )
    script.chmod(0o755)
    return script


@pytest.fixture()
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "history-gh"
    path.mkdir()
    (path / "hosts.yml").write_text("github.com:\n  user: reader\n")
    return path


def binding(profile: Path | None) -> dict[str, object]:
    return {
        "host": "github.com",
        "df_trust_repository_id": 11,
        "df_trust_full_name": DT,
        "branch": "main",
        "approval_policy_repository_id": 22,
        "history_gh_config_dir": None if profile is None else str(profile),
    }


def accepted(sha: str = X) -> dict[str, object]:
    return {
        "sha": sha,
        "last_epoch": 1,
        "source_digest": DIGEST,
        "validation": VALIDATION,
    }


def common(profile: Path | None, op: str, route: str) -> dict[str, object]:
    return {
        "schema": "df/precheck-request/v2",
        "nonce": NONCE,
        "op": op,
        "origin_route": route,
        "ctx_id": CTX,
        "observation_id": OBS,
        "binding": binding(profile),
        "policy": {"full_name": AP, "branch": "main", "p_sha": P},
    }


def trust_req(profile: Path | None, **over: object) -> dict[str, object]:
    base = common(profile, "trust", "trust-status")
    base |= {"trust_accepted": accepted(C0), "trust_head": X}
    return base | over


def merge_req(profile: Path | None, **over: object) -> dict[str, object]:
    base = common(profile, "merge", "merge")
    base |= {
        "expected_snapshot_sha": X,
        "trust_accepted": accepted(),
        "preliminary_state": "clear",
        "kind": "candidate",
        "repository_id": 42,
        "full_name": SLUG,
        "pr": 7,
        "head_sha": HEAD,
        "head_ref": BRANCH,
        "base_ref": "master",
        "base_tip": "c" * 40,
        "changes": CHANGES,
        "token": PRODUCT_TOKEN,
        "merger_login": "df-reviewer[bot]",
        "merger_type": "Bot",
        "bundle_dir": "workstreams/{ws}/spec",
        "dag_variant": "full",
        "api_base": API,
        "git_base": "http://127.0.0.1:9",
    }
    return base | over


def deps(forge: Any = None, slept: list[float] | None = None) -> PrecheckDeps:
    def transport_for(token: str, api_base: str) -> TokenTransport:
        return TokenTransport(token, api_base=api_base, opener=forge)

    sink = [] if slept is None else slept
    return PrecheckDeps(
        transport_for,
        open_snapshot,
        time.monotonic,
        wall=lambda: NOW,
        sleep=sink.append,
    )


def run(
    home: Path,
    payload: dict[str, object],
    gh: Path | None,
    precheck_deps: PrecheckDeps | None = None,
) -> tuple[dict[str, Any], str]:
    """`serve` on a complete tree: (verdict, raw stdout)."""
    home.mkdir(exist_ok=True)
    out = io.StringIO()
    code = serve(
        io.BytesIO(json.dumps(payload).encode()),
        out,
        admin_digest=ADMIN,
        home=home,
        complete_v2=True,
        gh=gh,
        git=GIT,
        deps=precheck_deps or deps(),
    )
    text = out.getvalue()
    assert code == 0, text
    return json.loads(text), text


class Candidate:
    """The product side of a candidate: manifest at the head, review facts with
    the human approve, the policy history of the open epoch (`load_ranges`)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.approve_on = HEAD
        self.heads: list[str | None] = []
        monkeypatch.setattr(act_premerge_core, "_manifest_text", lambda *a: manifest())
        monkeypatch.setattr(act_facts, "open_review", lambda *a: self.facts())
        monkeypatch.setattr(
            act_facts,
            "effective",
            lambda facts, clock: {
                r.login: ReviewView(
                    r.review_id, r.login, r.type_hint, r.state, r.commit_id
                )
                for r in facts.reviews
            },
        )
        monkeypatch.setattr(
            act_trust,
            "load_anchor_times",
            lambda *a: {ACT: parse_ts("2026-10-12T08:00:00Z")},
        )
        monkeypatch.setattr(act_trust, "load_ranges", self.ranges)

    def facts(self) -> object:
        review = v3.Review(
            "11", "alice", "User", "APPROVED", self.approve_on, "2026-10-12T11:00:00Z"
        )
        return v3.ReviewFacts(SLUG, 7, HEAD, "master", "OPEN", (review,), ())

    def ranges(self, trust: act_trust.Trust, *_a: object) -> object:
        self.heads.append(trust.policy_head)
        view = ActivationView(
            ("activation", ACT), ACT, parse_ts("2026-10-12T08:00:00Z")
        )
        return {1: (view,)}, {ACT: POLICY}


@pytest.fixture()
def candidate(monkeypatch: pytest.MonkeyPatch) -> Candidate:
    return Candidate(monkeypatch)


# --- dispatcher and request ----------------------------------------------------------


def test_dispatch_by_schema(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """One program, two protocols by `schema`: v1 stop keeps its v1 verdict, a v2
    request gets a v2 verdict from the v2 program."""
    v2, text = run(tmp_path / "home", trust_req(profile), gh)
    assert v2["schema"] == "df/precheck-verdict/v2"
    assert v2["version"] == "df-precheck/2"
    assert text.count("\n") == 1
    stop = {
        "schema": "df/precheck-request/v1",
        "nonce": NONCE,
        "kind": "stop",
        "repository_id": 42,
        "full_name": SLUG,
        "pr": 8,
        "head_sha": "b" * 40,
        "head_ref": "stop/ws-7-requirements-g1",
        "base_ref": "master",
        "base_tip": "c" * 40,
        "changes": [],
        "token": PRODUCT_TOKEN,
        "api_base": API,
        "git_base": "http://127.0.0.1:9",
    }
    v1, _ = run(tmp_path / "home", stop, gh)
    assert (v1["schema"], v1["outcome"]) == ("df/precheck-verdict/v1", "refuse")


@pytest.mark.parametrize(
    "over,named",
    [
        ({"op": "delete"}, "op"),
        ({"ctx_id": "x"}, "ctx_id"),
        ({"observation_id": 0}, "observation_id"),
        ({"origin_route": "other"}, "origin_route"),
        ({"trust_head": "H"}, "trust_head"),
        ({"policy": {"full_name": AP, "branch": "main"}}, "policy"),
        ({"observation_id": 2**63}, "observation_id"),
        ({"binding": {**binding(None), "df_trust_repository_id": 2**63}}, "binding"),
    ],
    ids=[
        "op",
        "ctx_id",
        "observation_id",
        "origin_route",
        "trust_head",
        "policy",
        "int_bound",
        "binding_int_bound",
    ],
)
def test_strict_v2_request_exit_2(
    tmp_path: Path, capsys: Any, profile: Path, over: dict, named: str
) -> None:
    """Review M1: ints are bounded `0 < v < 2**63` (the echo stays bounded)."""
    assert named in _exit_2(tmp_path, trust_req(profile, **over), capsys)


def _exit_2(tmp_path: Path, payload: dict[str, object], capsys: Any) -> str:
    """`serve` refuses the request without a verdict: its stderr."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    out = io.StringIO()
    code = serve(
        io.BytesIO(json.dumps(payload).encode()),
        out,
        admin_digest=ADMIN,
        home=home,
        complete_v2=True,
        gh=None,
        git=GIT,
        deps=deps(),
    )
    assert code == 2 and out.getvalue() == ""
    return capsys.readouterr().err


@pytest.mark.parametrize(
    "over,why",
    [
        (
            {"trust_accepted": accepted(C0)},
            "cross-field: trust_accepted.sha != expected_snapshot_sha",
        ),
        (
            {"origin_route": "trust-status"},
            "cross-field: op merge needs origin_route merge",
        ),
    ],
    ids=["accepted_not_x", "route"],
)
def test_merge_cross_fields_exit_2(
    tmp_path: Path, capsys: Any, profile: Path, over: dict, why: str
) -> None:
    """`trust_accepted` of a merge is the record at X; the route is `merge`."""
    err = _exit_2(tmp_path, merge_req(profile, **over), capsys)
    assert why in err


def test_v2_unknown_key_exit_2(tmp_path: Path, capsys: Any, profile: Path) -> None:
    err = _exit_2(tmp_path, trust_req(profile, extra=1), capsys)
    assert "keys: unexpected ['extra'], missing []" in err


def test_v2_invalid_value_named_exit_2(
    tmp_path: Path, capsys: Any, profile: Path
) -> None:
    err = _exit_2(tmp_path, trust_req(profile, ctx_id="x"), capsys)
    assert "invalid values: ['ctx_id']" in err


@pytest.mark.parametrize("op", ["trust", "merge"])
def test_policy_mandatory_on_every_op_exit_2(
    tmp_path: Path, capsys: Any, profile: Path, op: str
) -> None:
    """Review D2: without the pinned coordinates С-2 cannot be checked."""
    build = trust_req if op == "trust" else merge_req
    err = _exit_2(tmp_path, build(profile, policy=None), capsys)
    assert "invalid values: ['policy']" in err


@pytest.mark.parametrize("op", ["trust", "merge"])
def test_c2_policy_branch_mismatch_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    candidate: Candidate,
    op: str,
) -> None:
    """С-2: the `source.env` branch on H/X (`main`) is not the default branch
    the publisher resolved (`trunk`): `policy_branch_mismatch@approval-policy`."""
    policy = {"full_name": AP, "branch": "trunk", "p_sha": P}
    build = trust_req if op == "trust" else merge_req
    verdict, _ = run(tmp_path / "home", build(profile, policy=policy), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == (
        "perimeter_findings: policy_branch_mismatch@approval-policy"
    )
    assert verdict["findings"][0]["kind"] == "policy_branch_mismatch"


def test_observation_id_echoed(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    payload = trust_req(profile, observation_id=4242)
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["observation_id"] == 4242
    assert verdict["ctx_id"] == CTX
    assert verdict["request_digest"] == request_digest(payload)
    assert verdict["nonce"] == NONCE


# --- op: trust -----------------------------------------------------------------------


def test_trust_op_clean_accepts_pair(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    verdict, text = run(tmp_path / "home", trust_req(profile), gh)
    assert (verdict["outcome"], verdict["reason"]) == ("allow", "")
    assert verdict["relation"] == "descendant"
    assert (verdict["snapshot_sha"], verdict["accepted_sha"]) == (X, C0)
    assert verdict["last_epoch"] == 1
    assert verdict["source"]["digest"] == DIGEST
    assert verdict["findings"] == [] and verdict["unverified"] == []
    assert verdict["ranges"] is None
    assert verdict["root"] == {
        "host": "github.com",
        "df_trust_repository_id": 11,
        "branch": "main",
    }
    # the wire is exactly the canonical bytes of `fit_verdict`
    raw = text.removesuffix("\n").encode()
    envelope = {
        k: v for k, v in verdict.items() if k not in ("findings", "diagnostics")
    }
    assert raw == act_trust_findings.fit_verdict(envelope, (), "")
    # every trust-source read carried the history token
    assert wire.calls and all(
        HISTORY_TOKEN in h.get("Authorization", "") for _p, h in wire.calls
    )


def test_trust_op_writes_nothing(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stateless: no journal, no `load_trust`, nothing left in the role home."""
    monkeypatch.setattr(
        act_trust, "load_trust", lambda *a, **k: pytest.fail("load_trust called")
    )
    home = tmp_path / "home"
    verdict, _ = run(home, trust_req(profile), gh)
    assert verdict["outcome"] == "allow"
    assert list(home.iterdir()) == []


def test_trust_op_rollback_is_finding_not_synthesised(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """A gone H with a base: relation `not_descendant`, only `history_unverifiable`,
    `source` null (rulings 10, 11)."""
    gone = "9" * 40
    wire.routes[f"repos/{DT}/commits/{gone}"] = (404, {"message": "Not Found"})
    verdict, _ = run(tmp_path / "home", trust_req(profile, trust_head=gone), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["relation"] == "not_descendant"
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("history_unverifiable", "df-trust")
    ]
    assert verdict["source"] is None and verdict["last_epoch"] is None
    assert verdict["reason"] == "perimeter_findings: history_unverifiable@df-trust"


def test_source_null_iff_source_findings(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """`source` is null iff `source_env_invalid`/`history_unverifiable` is found;
    a settings finding keeps the descriptor."""
    wire.routes[f"repos/{AP}"] = (
        200,
        {"full_name": AP, "allow_rebase_merge": True, **_perms()},
    )
    verdict, _ = run(tmp_path / "home", trust_req(profile), gh)
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("rebase_allowed", "approval-policy")
    ]
    assert verdict["source"] is not None
    kinds = {f["kind"] for f in verdict["findings"]}
    assert not kinds & {"source_env_invalid", "history_unverifiable"}


def test_trust_op_unkeyed_is_anonymous_with_warning(
    tmp_path: Path, wire: _Wire
) -> None:
    """O14: a binding without the key reads anonymously; `history_token_required`
    is an unverified premise, not «ok»."""
    verdict, _ = run(tmp_path / "home", trust_req(None), None)
    assert verdict["outcome"] == "allow"
    assert verdict["unverified"] == ["history_token_required"]
    assert all("Authorization" not in h for _p, h in wire.calls)


def test_keyed_binding_without_approved_gh_refused_not_path(
    tmp_path: Path, wire: _Wire, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A keyed binding and no approved `gh`: never `history_token` (gh from PATH)."""
    monkeypatch.setattr(
        act_trust, "history_token", lambda d: pytest.fail("gh from PATH")
    )
    verdict, _ = run(tmp_path / "home", trust_req(profile), None)
    assert (verdict["outcome"], verdict["reason"]) == (
        "unset",
        act_precheck_v2.NO_APPROVED_GH,
    )
    assert wire.calls == []
    merge, _ = run(tmp_path / "home", merge_req(profile), None)
    assert (merge["outcome"], merge["reason"]) == (
        "refuse",
        act_precheck_v2.NO_APPROVED_GH,
    )


def test_reeval_merge_returns_policy_break(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`op: trust` re-evaluating a `merge` observation reads the policy history up
    to the pinned P by SHA and may return `policy_history_break` (and the ranges)."""
    seen: list[tuple] = []

    def history(transport, source, anchor, until, *, head=None):  # noqa: ANN001, ANN202
        seen.append((anchor, until, head))
        return act_facts.HistoryBreak("commit_without_pr: abc")

    monkeypatch.setattr(act_facts, "history", history)
    payload = trust_req(
        profile, origin_route="merge", trust_accepted=accepted(), trust_head=X
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("policy_history_break", "approval-policy")
    ]
    assert verdict["findings"][0]["kind"] == "policy_history_break"
    assert "commit_without_pr: abc" in verdict["findings"][0]["detail"]
    assert seen == [("b" * 40, None, P)]
    assert verdict["ranges"]["ranges"] == [[1, "b" * 40, P]]
    # the route `trust-status` reads no policy history: no ranges, no such finding
    seen.clear()
    clean = run(tmp_path / "home", trust_req(profile), gh)[0]
    assert seen == [] and clean["findings"] == [] and clean["ranges"] is None


def test_unsupported_size_is_typed(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def too_many(epochs, p_sha):  # noqa: ANN001, ANN202
        raise act_trust_findings.UnsupportedSize("epochs")

    monkeypatch.setattr(act_precheck_v2, "range_descriptor", too_many)
    monkeypatch.setattr(act_facts, "history", lambda *a, **k: ())
    payload = trust_req(profile, origin_route="merge")
    verdict, text = run(tmp_path / "home", payload, gh)
    assert (verdict["outcome"], verdict["reason"]) == (
        "unsupported_size",
        "unsupported_size: epochs",
    )
    assert verdict["findings"] == [] and len(text.encode()) < 4096


def test_deadline_is_unset(
    tmp_path: Path, gh: Path, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rung 1 for v2: a source that does not answer gives `unset: deadline`."""

    def slow(req, timeout):  # noqa: ANN001, ANN202
        time.sleep(5)

    monkeypatch.setattr(act_anon, "_default_open", lambda: slow)
    monkeypatch.setattr(act_precheck, "DEADLINE", 0.2)
    monkeypatch.setattr(act_precheck_v2, "ALARM_SLACK", 0.1)
    started = time.monotonic()
    verdict, _ = run(tmp_path / "home", trust_req(profile), gh)
    assert (verdict["outcome"], verdict["reason"]) == ("unset", "deadline")
    assert time.monotonic() - started < 4


# --- op: merge, candidate ------------------------------------------------------------


def test_candidate_allowed_with_act_on_final_head(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert (verdict["outcome"], verdict["reason"]) == ("allow", "")
    assert verdict["findings"] == []
    # the policy history of the open epoch is read up to the pinned P
    assert candidate.heads == [P, P]
    assert verdict["ranges"]["ranges"] == [[1, "b" * 40, P]]
    assert verdict["relation"] == "descendant" and verdict["last_epoch"] == 1


def test_binding_echoes_c_x_pr_kind_root(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    payload = merge_req(profile)
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert {k: verdict[k] for k in ("repository_id", "pr", "head_sha", "kind")} == {
        "repository_id": 42,
        "pr": 7,
        "head_sha": HEAD,
        "kind": "candidate",
    }
    assert verdict["snapshot_sha"] == verdict["expected_snapshot_sha"] == X
    assert verdict["root"] == {
        "host": "github.com",
        "df_trust_repository_id": 11,
        "branch": "main",
    }
    assert (verdict["op"], verdict["origin_route"]) == ("merge", "merge")
    assert verdict["policy"] == {"full_name": AP, "branch": "main", "p_sha": P}
    assert verdict["admin_digest"] == ADMIN
    assert verdict["request_digest"] == request_digest(payload)
    assert PRODUCT_TOKEN not in json.dumps(verdict)


def test_approve_before_evidence_not_counted(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """D1: the approve is on the head before the edge-check evidence commit; the
    PR head (C) is the final one — no act."""
    candidate.approve_on = "d" * 40
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == f"нет approve человека на {HEAD[:12]}"


def test_candidate_foreign_file_refused(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """The surface is of the WHOLE PR: a foreign file of an early commit stays in
    the diff to base even when the last (evidence) commit is clean."""
    changes = [*CHANGES, ["added", "scripts/foreign.sh", None]]
    verdict, _ = run(tmp_path / "home", merge_req(profile, changes=changes), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "surface_violation: scripts/foreign.sh"


def test_candidate_manifest_not_in_pr_refused(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    verdict, _ = run(tmp_path / "home", merge_req(profile, changes=CHANGES[::2]), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == (
        "surface_violation: manifest "
        "workstreams/ws-7/approvals/requests/approve-2-1-1.yaml "
        "is not changed by this PR"
    )


def test_merger_substitution_changes_digest_and_refuses(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """N4: the merger is the request's; a substituted one is another request
    (digest) and fails D3."""
    honest, swapped = (
        merge_req(profile),
        merge_req(profile, merger_login="stranger", merger_type="User"),
    )
    assert request_digest(honest) != request_digest(swapped)
    verdict, _ = run(tmp_path / "home", swapped, gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "мержер stranger не пройдёт D3"
    assert verdict["request_digest"] == request_digest(swapped)


def test_merge_without_history_key_refused(
    tmp_path: Path, wire: _Wire, gh: Path
) -> None:
    """O14: candidate/finalize need the history token key."""
    verdict, _ = run(tmp_path / "home", merge_req(None), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "history_token_required: the binding has no key"
    assert wire.calls == []


def test_merge_settings_finding_refuses_with_findings(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """A finding of the snapshot check (the pair and the settings) is carried in
    the verdict for the publisher to register; no merge decision is reached."""
    wire.routes[f"repos/{DT}/rules/branches/main"] = (200, RULES[:2])
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert verdict["outcome"] == "refuse"
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("ruleset_missing", "df-trust")
    ]
    assert verdict["findings"][0]["kind"] == "ruleset_missing"
    assert candidate.heads == []


def test_merge_policy_break_found_by_load_ranges_is_finding(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`load_ranges` registers a policy history break through the adapter with its
    kind (`act_trust._register` → `record_finding`)."""

    def broken(trust, anchors, t, journal):  # noqa: ANN001, ANN202
        found = ("approval-policy", "commit_without_pr: z")
        return act_trust._register(
            journal, trust.snapshot, found, "policy_history_break"
        )

    monkeypatch.setattr(act_trust, "load_ranges", broken)
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["findings"] == [
        {
            "kind": "policy_history_break",
            "role": "approval-policy",
            "detail": "commit_without_pr: z",
        }
    ]


def test_stale_snapshot_adapter() -> None:
    """`status` is the preliminary state only for the snapshot X."""
    adapter = act_precheck_v2.Adapter("clear", X)
    root = act_trust.TrustRoot("github.com", 11, "main", X)
    snap = act_trust.TrustSnapshot(root, DT, _source(), ())
    assert adapter.status(snap) == "clear"
    moved = replace(snap, root=replace(root, snapshot_sha="2" * 40))
    stale = adapter.status(moved)
    assert isinstance(stale, Unavailable) and stale.reason == "stale_snapshot"
    # through `load_trust`: a reader that yields another SHA is not decided on
    got = act_trust.load_trust(
        act_trust.Readers(lambda c, p: pytest.fail("no credential")),
        _binding(),
        adapter,
        creds=act_trust.COVERED_BY_KEY_HOLDER,
        snapshot=lambda transport, b: moved,
    )
    assert got == Unavailable("журнал периметра: stale_snapshot")
    # a violation without its kind is not kept: fail closed
    kindless = adapter.record(snap, "df-trust", "x")
    assert isinstance(kindless, Unavailable)
    assert kindless.reason == "journal adapter: a violation of df-trust without kind"
    assert adapter.findings == {}


def test_derived_credential_mode_not_empty_creds(wire: _Wire) -> None:
    """N3: the derived product credential is an explicit mode; an empty set of
    credentials is still a refusal."""
    readers = act_trust.Readers(lambda c, p: pytest.fail("self-report of creds"))
    empty = act_trust.load_trust(readers, _binding(), _Clear(), creds=())
    assert empty == Unavailable("самоотчёт: процесс не назвал ни одного credential")
    routes = trust_routes()
    routes[f"repos/{DT}/branches/main"] = (200, {"commit": {"sha": X}})
    wire.routes.update(routes)
    covered = act_trust.load_trust(
        readers, _binding(), _Clear(), creds=act_trust.COVERED_BY_KEY_HOLDER
    )
    assert isinstance(covered, act_trust.Trust)
    assert covered.root.snapshot_sha == X


# --- op: merge, finalize -------------------------------------------------------------


@pytest.fixture()
def product(tmp_path: Path) -> Iterator[tuple[Repo, GitServer]]:
    repo = Repo(tmp_path)
    srv = GitServer(repo.served_root)
    yield repo, srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _finalize_world(repo: Repo, review_id: str = "R1", **files: str) -> dict:
    """The candidate request delivered by PR 5; the finalize PR 9 carries the
    envelope of its node (rendered with `review_id`), plus `files`."""
    req = _request("approve-2-1-1", 1)
    merge = repo.deliver_request("approve-2-1-1", 1, 5)
    current = DeliveredRequest(merge, repo.pulls[5], req)
    path = envelope_path(WS, "requirements")
    text = render_envelope(expected_envelope(current, "requirements", review_id))
    head = repo.branch("fin", {path: text, **files})
    repo.pulls[9] = head
    repo.publish()
    return {"merge": merge, "head": head, "path": path, "text": text}


def _finalize_req(repo: Repo, srv: GitServer, fin: dict, profile: Path, **over):
    changes = [["added", fin["path"], None]]
    return (
        merge_req(
            profile,
            kind="finalize",
            pr=9,
            head_sha=fin["head"],
            head_ref=f"{BRANCH}-final",
            base_tip=repo.tip(),
            changes=changes,
            git_base=srv.base,
            token="ghs_SECRET-token-123",
        )
        | over
    )


def _accepting(monkeypatch: pytest.MonkeyPatch, merge: str, seen: list[dict]) -> None:
    def verdict(readers, trust, journal, **kw):  # noqa: ANN001, ANN202
        seen.append(kw)
        return Accepted(merge, "df-reviewer[bot]", review_id="R1"), None

    monkeypatch.setattr(act_context, "delivery_verdict", verdict)


def test_finalize_review_id_from_delivery(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N7: the envelopes are expected with `review_id` of the final delivery
    decision of the candidate PR (found by the branch), not a current approve."""
    repo, srv = product
    fin = _finalize_world(repo)
    seen: list[dict] = []
    _accepting(monkeypatch, fin["merge"], seen)
    payload = _finalize_req(repo, srv, fin, profile)
    verdict, text = run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))
    assert (verdict["outcome"], verdict["reason"]) == ("allow", "")
    assert verdict["kind"] == "finalize" and verdict["head_sha"] == fin["head"]
    [call] = seen
    assert (call["pr"], call["legacy"], call["head_sha"]) == (5, None, repo.pulls[5])
    assert "ghs_SECRET" not in text


def test_finalize_wrong_envelopes_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo, review_id="R2")
    _accepting(monkeypatch, fin["merge"], [])
    payload = _finalize_req(repo, srv, fin, profile)
    verdict, _ = run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))
    assert verdict["outcome"] == "refuse"
    assert (
        verdict["reason"]
        == "envelope_content: workstreams/ws-7/approvals/requirements.yaml"
    )
    extra = [["added", fin["path"], None], ["added", "README2.md", None]]
    payload = _finalize_req(repo, srv, fin, profile, changes=extra)
    verdict, _ = run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"].startswith("envelope_set: the PR changes")


def test_finalize_branch_of_unknown_request_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)
    _accepting(monkeypatch, fin["merge"], [])
    payload = _finalize_req(
        repo, srv, fin, profile, head_ref="spec/ws-7-approve-3-1-1-final"
    )
    verdict, _ = run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "no_delivered_request: ws-7/approve-3-1-1"
    payload = _finalize_req(repo, srv, fin, profile, head_ref=BRANCH)
    verdict, _ = run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))
    assert verdict["reason"] == f"finalize_branch: {BRANCH!r} has no finalize suffix"


# --- launch contract and closure -----------------------------------------------------


def test_v2_path_launches_only_approved_binaries(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    candidate: Candidate,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh_get`, `history_token` and `act_epochs._git` never run `gh`/`git` from
    `PATH`: every process of the v2 path is the approved `gh` or `git`. Only the
    calls of the program are recorded (the stand's own git is not)."""
    repo, srv = product
    fin = _finalize_world(repo)
    _accepting(monkeypatch, fin["merge"], [])
    launched: list[str] = []
    recording = [False]
    real_run, real_popen = subprocess.run, subprocess.Popen

    def spy_run(args, *a, **kw):  # noqa: ANN001, ANN202
        if recording[0]:
            launched.append(str(args[0]))
        return real_run(args, *a, **kw)

    def spy_popen(args, *a, **kw):  # noqa: ANN001, ANN202
        if recording[0]:
            launched.append(str(args[0]))
        return real_popen(args, *a, **kw)

    def recorded(home: str, payload: dict, forge: Any = None) -> str:
        recording[0] = True
        try:
            return run(tmp_path / home, payload, gh, deps(forge))[0]["outcome"]
        finally:
            recording[0] = False

    monkeypatch.setattr(subprocess, "run", spy_run)
    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    monkeypatch.setattr(act_trust, "history_token", lambda d: pytest.fail("PATH gh"))
    monkeypatch.setattr(act_epochs, "_git", lambda *a: pytest.fail("PATH git"))
    assert recorded("h1", trust_req(profile)) == "allow"
    assert recorded("h2", merge_req(profile)) == "allow"
    payload = _finalize_req(repo, srv, fin, profile)
    assert recorded("h3", payload, Forge(repo.answers)) == "allow"
    assert {str(gh), str(GIT)} <= set(launched)
    assert set(launched) <= {str(gh), str(GIT)}
    # the readers of `load_trust` (`gh api` per credential) use the approved gh
    req = act_precheck_v2.parse(act_precheck.parse_request_v2(merge_req(profile)))
    assert isinstance(req, act_precheck_v2.MergeRequest)
    ctx = act_precheck_v2.Context(ADMIN, tmp_path, gh, GIT, deps(), 0.0)
    built = act_precheck_v2._premerge_deps(
        req, ctx, act_precheck_v2.Adapter("clear", X), []
    )
    launched.clear()
    recording[0] = True
    cred = act_trust.Credential("profile:agent", gh_config_dir=tmp_path)
    built.readers.as_credential(cred, "repos/o/p")
    recording[0] = False
    assert set(launched) == {str(gh)}


def _top_imports(rel: str) -> set[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return {n for n in names if n.split(".")[0] == "governance"}


def test_act_precheck_top_imports_within_legacy_2b() -> None:
    """The top-level imports of `act_precheck` stay in the 2b list; the v2 program
    is reached only by the deferred import after the completeness check."""
    top = _top_imports("governance/act_precheck.py")
    modules = {n for n in top if (ROOT / (n.replace(".", "/") + ".py")).is_file()}
    assert modules and modules <= set(LEGACY_2B.modules)
    assert "governance.act_precheck_v2" not in top
    deferred = ast.parse((ROOT / "governance/act_precheck.py").read_text("utf-8"))
    serving = next(
        n
        for n in deferred.body
        if isinstance(n, ast.FunctionDef) and n.name == "_serve_v2"
    )
    src = ast.unparse(serving)
    assert src.index("if not complete_v2") < src.index("import act_precheck_v2")


JWT_OPS_DF = (
    "governance.act_premerge",
    "governance.ops",
    "governance.runner",
    "governance.act_status",
    "governance.act_node",
    "governance.act_app_token",
    "governance.act_app_installations",
    "governance.edge_check",
    "governance.edge_check.publish",
)


def test_closure_excludes_jwt_ops_df() -> None:
    """AST closure (deferred imports included) of `act_precheck` with v2: no
    `jwt`, no `df`, no `ops`/`runner`/`act_status`/`act_node`/`edge_check`."""
    local, external = _closure("governance.act_precheck")
    assert "governance.act_precheck_v2" in local
    assert "governance.act_premerge_core" in local
    assert [m for m in JWT_OPS_DF if m in local] == []
    assert not any(m == "df" or m.startswith("df.") for m in local)
    assert "jwt" not in external
    assert sorted(external - set(sys.stdlib_module_names)) == ["steward", "yaml"]


def test_premerge_cli_outside_closure_app_chain_static() -> None:
    """Review D1: v2 imports the premerge core, never the CLI `act_premerge`, whose
    App mode imports `act_app_installations` statically — so the merge-harness
    tracer sees the App chain and the precheck closure never holds it."""
    from tests.harness_trust import closure

    local, _external = _closure("governance.act_precheck")
    assert "governance.act_premerge_core" in local
    assert "governance.act_premerge" not in local
    files, problems, _third = closure(ROOT, "governance/act_premerge.py")
    assert problems == []
    assert {
        "governance/act_premerge_core.py",
        "governance/act_app_installations.py",
        "governance/act_app_token.py",
    } <= files


# --- helpers ---------------------------------------------------------------------------


class _Clear:
    def status(self, snap: object) -> str:
        return "clear"

    def record(self, snap: object, source: str, detail: str) -> None:
        pytest.fail(f"event: {source}: {detail}")


def _binding() -> act_trust.Binding:
    return act_trust.Binding(
        "github.com", 11, DT, "main", 22, "public", history_gh_config_dir=None
    )


def _source() -> Any:
    from governance.act_source import parse_source

    return parse_source(SOURCE_ENV)


def test_finding_kinds_of_adapter_bounded() -> None:
    """`record_finding` keeps the first finding of a (kind, role), clipped."""
    adapter = act_precheck_v2.Adapter("clear", X)
    root = act_trust.TrustRoot("github.com", 11, "main", X)
    snap = act_trust.TrustSnapshot(root, DT, _source(), ())
    adapter.record_finding(snap, "token_write", "df-trust", "x" * 4000)
    adapter.record_finding(snap, "token_write", "df-trust", "second")
    [finding] = adapter.findings.values()
    assert isinstance(finding, Finding) and finding.detail.startswith("xxx")
    assert len(finding.detail.encode()) <= 512


# --- the remaining refusals, each with its own reason ---------------------------------


def test_verdict_overflow_is_fixed_size(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An envelope that still leaves no budget is the typed fixed-size answer."""
    real = act_precheck_v2.fit_verdict
    calls: list[int] = []

    def overflowing(envelope, findings, diagnostics):  # noqa: ANN001, ANN202
        calls.append(1)
        if len(calls) == 1:
            raise act_trust_findings.UnsupportedSize("verdict")
        return real(envelope, findings, diagnostics)

    monkeypatch.setattr(act_precheck_v2, "fit_verdict", overflowing)
    verdict, _ = run(tmp_path / "home", trust_req(profile), gh)
    assert verdict["reason"] == "unsupported_size: verdict"
    assert verdict["outcome"] == "unsupported_size" and verdict["findings"] == []


def test_policy_pin_of_other_repo_irreproducible(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """The publisher's pin (by id) names another repository than `source.env` on
    H: H's `source.env` is pinned by its SHA, so no re-evaluation can agree --
    the typed terminal `irreproducible` (3a Task 10 review D-c), not `unset`;
    the reason is the closed code, what it names is in the diagnostics."""
    policy = {"full_name": "owner/renamed", "branch": "main", "p_sha": P}
    verdict, _ = run(tmp_path / "home", trust_req(profile, policy=policy), gh)
    assert verdict["outcome"] == act_precheck_v2.IRREPRODUCIBLE
    assert verdict["reason"] == act_precheck_v2.PIN_MISMATCH
    assert "owner/renamed" in verdict["diagnostics"]
    assert verdict["relation"] is None and verdict["findings"] == []


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        ("http: 404 repos/owner/approval-policy/commits/abc", "irreproducible"),
        ("http: 422 repos/owner/approval-policy/commits/abc", "irreproducible"),
        ("http: 502 repos/owner/approval-policy/commits/abc", "unset"),
        ("transport: timed out", "unset"),
    ],
)
def test_policy_objects_missing_irreproducible(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    outcome: str,
) -> None:
    """D-c: objects of the policy history up to the pinned P that GitHub says
    are gone (404/422) are permanent -- `irreproducible:
    policy_objects_missing`; a gap that is only unreadable now stays `unset`."""
    monkeypatch.setattr(act_facts, "history", lambda *a, **k: Unavailable(reason))
    payload = trust_req(
        profile, origin_route="merge", trust_accepted=accepted(), trust_head=X
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == outcome
    if outcome == "irreproducible":
        assert verdict["reason"] == act_precheck_v2.OBJECTS_MISSING
        assert reason in verdict["diagnostics"]
        assert verdict["ranges"] is None and verdict["findings"] == []
        assert verdict["relation"] == "descendant"


def test_reeval_merge_keeps_findings_over_transient_policy_gap(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid review #704 P1: re-evaluating a `merge` observation, a settings
    finding already established wins over a transient gap of the policy
    history (`refuse` with the finding, I6 shape kept, ranges carried), and
    over a permanent one (re-review N2); without a finding the transient gap
    stays `unset`."""
    wire.routes[f"repos/{AP}"] = (
        200,
        {"full_name": AP, "allow_rebase_merge": True, **_perms()},
    )
    payload = trust_req(
        profile, origin_route="merge", trust_accepted=accepted(), trust_head=X
    )
    monkeypatch.setattr(
        act_facts, "history", lambda *a, **k: Unavailable("transport: timed out")
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("rebase_allowed", "approval-policy")
    ]
    assert verdict["relation"] == "descendant" and verdict["source"] is not None
    assert verdict["ranges"] is not None
    monkeypatch.setattr(
        act_facts, "history", lambda *a, **k: Unavailable("http: 404 repos/x/c/abc")
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    # re-review N2: the finding wins over a permanent gap too
    assert verdict["outcome"] == "refuse"
    assert [(f["kind"], f["role"]) for f in verdict["findings"]] == [
        ("rebase_allowed", "approval-policy")
    ]
    wire.routes[f"repos/{AP}"] = (
        200,
        {"full_name": AP, "allow_rebase_merge": False, **_perms()},
    )
    monkeypatch.setattr(
        act_facts, "history", lambda *a, **k: Unavailable("transport: timed out")
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "unset" and verdict["findings"] == []


def test_pin_mismatch_keeps_pair_findings(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """Re-review N2: H is not a descendant of A and its `source.env` names
    another policy repository than the pin -- the pair's `not_descendant`
    is reported (`refuse`), not swallowed by `irreproducible`."""
    policy = {"full_name": "owner/renamed", "branch": "main", "p_sha": P}
    payload = trust_req(
        profile, policy=policy, trust_accepted=accepted(X), trust_head=C0
    )
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"
    assert ("not_descendant", "df-trust") in [
        (f["kind"], f["role"]) for f in verdict["findings"]
    ]
    assert verdict["relation"] == "not_descendant"


def test_pin_compared_case_insensitively(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """Re-review N4: GitHub owner/name are case-insensitive -- a pin that
    differs from `source.env` only in case is the same repository, no K."""
    policy = {"full_name": AP.upper(), "branch": "main", "p_sha": P}
    verdict, _ = run(tmp_path / "home", trust_req(profile, policy=policy), gh)
    assert verdict["outcome"] == "allow"


def test_policy_break_wins_over_missing_objects(
    tmp_path: Path, gh: Path, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken form is a finding and wins over another epoch's gone objects."""
    routes = _Wire(trust_routes(journal_x=J2))
    monkeypatch.setattr(act_anon, "_default_open", lambda: routes)

    def history(transport, source, anchor, until, *, head=None):  # noqa: ANN001, ANN202
        if anchor == "b" * 40:
            return Unavailable("http: 404 repos/x/commits/b")
        return act_facts.HistoryBreak("commit_without_pr: late")

    monkeypatch.setattr(act_facts, "history", history)
    payload = trust_req(profile, origin_route="merge", trust_accepted=None)
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"


def test_irreproducible_reasons_closed() -> None:
    assert act_precheck_v2.IRREPRODUCIBLE_REASONS == {
        act_precheck_v2.PIN_MISMATCH,
        act_precheck_v2.OBJECTS_MISSING,
    }
    assert all(
        r.startswith(f"{act_precheck_v2.IRREPRODUCIBLE}: ")
        for r in act_precheck_v2.IRREPRODUCIBLE_REASONS
    )


def test_candidate_legacy_dag_variant_refused(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    payload = merge_req(profile, dag_variant="legacy5")
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == (
        "surface_violation: dag variant 'legacy5' is not served in v1"
    )


def _finalize_run(
    tmp_path: Path,
    repo: Repo,
    srv: GitServer,
    fin: dict,
    profile: Path,
    gh: Path,
    **over,
) -> dict[str, Any]:
    payload = _finalize_req(repo, srv, fin, profile, **over)
    return run(tmp_path / "home", payload, gh, deps(Forge(repo.answers)))[0]


def test_finalize_not_a_finalize_branch_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)
    _accepting(monkeypatch, fin["merge"], [])
    verdict = _finalize_run(
        tmp_path, repo, srv, fin, profile, gh, head_ref="spec/ws-7-approve-x-final"
    )
    assert verdict["reason"] == (
        "finalize_branch: 'spec/ws-7-approve-x-final' is not a finalize branch"
    )


def test_finalize_read_failure_unset(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)

    def failing(*a: object) -> object:
        raise OSError("disk")

    monkeypatch.setattr(act_precheck_v2, "load_history", failing)
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh)
    assert verdict["outcome"] == "unset"
    assert verdict["reason"] == "read_failed: disk"


def test_finalize_history_unavailable_unset(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)
    monkeypatch.setattr(
        act_precheck_v2, "load_history", lambda *a: Unavailable("first-parent")
    )
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh)
    assert verdict["reason"] == "history_unavailable: first-parent"


def test_finalize_superseded_request_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later generation of the node was delivered: the finalized request is no
    longer its current event."""
    repo, srv = product
    req = _request("approve-2-1-1", 1)
    merge = repo.deliver_request("approve-2-1-1", 1, 5)
    repo.deliver_request("approve-3-1-1", 2, 6)
    current = DeliveredRequest(merge, repo.pulls[5], req)
    path = envelope_path(WS, "requirements")
    text = render_envelope(expected_envelope(current, "requirements", "R1"))
    head = repo.branch("fin", {path: text})
    repo.pulls[9] = head
    repo.publish()
    _accepting(monkeypatch, merge, [])
    fin = {"merge": merge, "head": head, "path": path, "text": text}
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh)
    assert verdict["reason"] == "request_not_current: nodes ['requirements']"


def test_finalize_candidate_pr_unreadable_unset(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)
    _accepting(monkeypatch, fin["merge"], [])
    monkeypatch.setattr(
        act_precheck_v2, "pr_for_merge", lambda *a: Unavailable("no PR")
    )
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh)
    assert verdict["reason"] == "candidate_pr: no PR"


@pytest.mark.parametrize(
    "outcome,reason",
    [
        (Rejected("I_BYTES", "x"), "delivery_rejected: I_BYTES: x"),
        (WaitingDelivery(), "delivery_waiting: candidate PR #5"),
        (Unavailable("facts down"), "delivery_unset: facts down"),
    ],
    ids=["rejected", "waiting", "unset"],
)
def test_finalize_delivery_not_accepted(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
    outcome: object,
    reason: str,
) -> None:
    repo, srv = product
    fin = _finalize_world(repo)
    monkeypatch.setattr(
        act_context, "delivery_verdict", lambda *a, **k: (outcome, None)
    )
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh)
    assert verdict["reason"] == reason


class _Files:
    """`SnapshotHistory` reads of a stand: (ref, path) → text or bytes."""

    def __init__(self, files: dict[tuple[str, str], str]) -> None:
        self.files = files

    def show_file(self, _target: str, ref: str, path: str) -> str | None:
        return self.files.get((ref, path))

    def show_file_bytes(self, _target: str, ref: str, path: str) -> bytes | None:
        text = self.files.get((ref, path))
        return None if text is None else text.encode()


def test_head_hashes_refusals() -> None:
    req = _request("approve-2-1-1", 1)
    path = req.nodes["requirements"].path
    absent = act_precheck_v2.head_hashes(_Files({}), req, HEAD)  # type: ignore[arg-type]
    assert isinstance(absent, Unavailable)
    assert absent.reason == f"{path} at {HEAD[:12]} does not read"
    marked = "---\napproval_act: other/v9\n---\nтело\n"
    bad = act_precheck_v2.head_hashes(
        _Files({(HEAD, path): marked}),  # type: ignore[arg-type]
        req,
        HEAD,
    )
    assert isinstance(bad, Unavailable)
    assert "маркер узла" in bad.reason


def test_envelope_unreadable_unset(profile: Path) -> None:
    req = _request("approve-2-1-1", 1)
    current = DeliveredRequest("m" * 40, "h" * 40, req)
    path = envelope_path(WS, "requirements")
    parsed = act_precheck_v2.parse(
        act_precheck.parse_request_v2(
            merge_req(profile, kind="finalize", changes=[["added", path, None]])
        )
    )
    assert isinstance(parsed, act_precheck_v2.MergeRequest)
    got = act_precheck_v2._envelopes(
        parsed,
        _Files({}),  # type: ignore[arg-type]
        WS,
        current,
        Accepted("m" * 40, "df-reviewer[bot]", review_id="R1"),
    )
    assert got == (
        2,
        "envelope_unreadable: workstreams/ws-7/approvals/requirements.yaml",
    )


def test_lone_surrogates_request_exit_2_api_text_replaced(
    tmp_path: Path,
    capsys: Any,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request with a lone surrogate has no digest: exit 2, no verdict. Text
    from the API (a policy history reason) with one is replaced by U+FFFD
    before the canonical UTF-8 bytes."""
    err = _exit_2(tmp_path, merge_req(profile, merger_login="x\ud800"), capsys)
    assert "request carries lone surrogates" in err
    bad = {**binding(profile), "host": "github.com\ud800"}
    assert "request carries lone surrogates" in _exit_2(
        tmp_path, trust_req(profile, binding=bad), capsys
    )
    monkeypatch.setattr(
        act_facts, "history", lambda *a, **k: act_facts.HistoryBreak("login x\ud800")
    )
    payload = trust_req(profile, origin_route="merge")
    verdict, text = run(tmp_path / "home", payload, gh)
    assert verdict["findings"][0]["detail"] == "epoch 1: login x\ufffd"
    assert "\\ud800" not in text


def test_long_reason_clipped_whole_in_diagnostics(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """The reason is bounded (512 bytes serialised); the whole text, the product
    token masked, goes to the diagnostics."""
    login = "m" * 900 + PRODUCT_TOKEN
    payload = merge_req(profile, merger_login=login, merger_type="User")
    verdict, text = run(tmp_path / "home", payload, gh)
    assert len(verdict["reason"].encode()) <= 512
    assert verdict["diagnostics"] == f"reason: мержер {'m' * 900}[token] не пройдёт D3"
    assert PRODUCT_TOKEN not in text


# --- review round 1 -------------------------------------------------------------------


def test_pre_read_refusal_shape(tmp_path: Path, wire: _Wire, gh: Path) -> None:
    """Review I6: no pair read (`relation` null) — `source`, `last_epoch`,
    `ranges` null and no findings; the reason is not a findings reason. The
    unkeyed binding still names `history_token_required` (M3)."""
    verdict, _ = run(tmp_path / "home", merge_req(None), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["relation"] is None
    assert (verdict["source"], verdict["last_epoch"], verdict["ranges"]) == (
        None,
        None,
        None,
    )
    assert verdict["findings"] == []
    assert verdict["unverified"] == ["history_token_required"]
    assert verdict["snapshot_sha"] == X  # an echo: nothing was read


def test_unkeyed_trust_unset_still_names_history_token_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M3: the code comes from the binding whatever the outcome."""
    monkeypatch.setattr(act_anon, "_default_open", lambda: _Wire({}))
    verdict, _ = run(tmp_path / "home", trust_req(None), None)
    assert verdict["outcome"] == "unset" and verdict["relation"] is None
    assert verdict["unverified"] == ["history_token_required"]


def test_shape_contract_enforced(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review I6: a read pair without a source descriptor and without a source
    finding breaks the contract — a named `unset`, not a mis-shaped verdict."""
    broken = act_precheck_v2.Part("descendant", None, None, (), ())
    monkeypatch.setattr(act_precheck_v2, "trust_part", lambda *a: broken)
    verdict, _ = run(tmp_path / "home", trust_req(profile), gh)
    assert verdict["outcome"] == "unset"
    assert verdict["reason"] == "invariant_violated: source null with findings []"
    assert (
        act_precheck_v2._shape_problem(
            act_precheck_v2.Answer("refuse", "x", None, None, (_finding(),))
        )
        == "findings without a read pair"
    )


def _finding() -> Finding:
    return Finding("history_unverifiable", "df-trust", "gone")


def test_trust_verdict_carries_validation(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """Review D6: the publisher records the validation version verbatim."""
    verdict, _ = run(tmp_path / "home", trust_req(profile), gh)
    assert verdict["validation"] == "df-trust-rules/1:" + ADMIN
    merge, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert "validation" not in merge


def test_merge_unverified_includes_load_trust_premise(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M3: the settings read sees `allow_rebase_merge = false`, the
    `load_trust` read sees it hidden — the verdict names the premise the
    decision used."""
    real = act_trust._rebase

    def rebase(anon, snap, role=None):  # noqa: ANN001, ANN202
        if role is None:  # `load_trust`'s own call
            return act_trust.REBASE_PREMISE
        return real(anon, snap, role)

    monkeypatch.setattr(act_trust, "_rebase", rebase)
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert verdict["outcome"] == "allow"
    assert verdict["unverified"] == ["allow_rebase_merge@approval-policy"]


def test_policy_history_reads_all_epochs_finding_wins(
    tmp_path: Path, gh: Path, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M4: epoch 1 unreadable, epoch 2 broken — the finding, as on the
    merge path (`load_ranges` goes on past a gap)."""
    routes = _Wire(trust_routes(journal_x=J2))
    monkeypatch.setattr(act_anon, "_default_open", lambda: routes)

    def history(transport, source, anchor, until, *, head=None):  # noqa: ANN001, ANN202
        if anchor == "b" * 40:
            return Unavailable("epoch 1 down")
        return act_facts.HistoryBreak("commit_without_pr: late")

    monkeypatch.setattr(act_facts, "history", history)
    payload = trust_req(profile, origin_route="merge", trust_accepted=None)
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["findings"][0]["detail"] == "epoch 2: commit_without_pr: late"
    assert [r[0] for r in verdict["ranges"]["ranges"]] == [1, 2]


def test_alarm_never_postpones_a_shorter_timer() -> None:
    """Review M5: an outer timer shorter than ours fires on time (as our alarm),
    and the outer one is re-armed after."""
    import signal

    saved = signal.getitimer(signal.ITIMER_REAL)
    outer = signal.signal(signal.SIGALRM, lambda *a: None)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.2)
        started = time.monotonic()
        with pytest.raises(act_precheck_v2.DeadlineAlarm, match="deadline passed"):
            with act_precheck_v2._alarm(time.monotonic() + 60, time.monotonic):
                time.sleep(3)
        assert time.monotonic() - started < 2
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0  # outer re-armed
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, outer)
        if saved[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *saved)


def test_emit_binary_buffer_exact_bytes(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path
) -> None:
    """Review M6: production stdout has a binary buffer — exactly the
    `fit_verdict` bytes and a newline go through it."""
    home = tmp_path / "home"
    home.mkdir()
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii")  # not UTF-8: bytes bypass it
    code = serve(
        io.BytesIO(json.dumps(trust_req(profile)).encode()),
        out,
        admin_digest=ADMIN,
        home=home,
        complete_v2=True,
        gh=gh,
        git=GIT,
        deps=deps(),
    )
    out.flush()
    wire_bytes = raw.getvalue()
    assert code == 0 and wire_bytes.endswith(b"\n")
    verdict = json.loads(wire_bytes)
    envelope = {
        k: v for k, v in verdict.items() if k not in ("findings", "diagnostics")
    }
    assert wire_bytes == act_trust_findings.fit_verdict(envelope, (), "") + b"\n"


def test_reader_invariant_is_named_unset(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M7: a broken invariant (`ValueError`) is a named `unset` verdict,
    not a traceback without one."""

    def malformed(epochs, p_sha):  # noqa: ANN001, ANN202
        raise ValueError("epoch numbers not contiguous from 1")

    monkeypatch.setattr(act_precheck_v2, "range_descriptor", malformed)
    monkeypatch.setattr(act_facts, "history", lambda *a, **k: ())
    payload = trust_req(profile, origin_route="merge")
    verdict, _ = run(tmp_path / "home", payload, gh)
    assert verdict["outcome"] == "unset"
    assert verdict["reason"] == (
        "invariant_violated: epoch numbers not contiguous from 1"
    )


def test_envelope_merge_commits_differ_refused(profile: Path) -> None:
    """Review M15: the delivery decision names another merge commit than the
    base history — refused by name, not as a confusing content mismatch."""
    req = _request("approve-2-1-1", 1)
    current = DeliveredRequest("m" * 40, "h" * 40, req)
    path = envelope_path(WS, "requirements")
    parsed = act_precheck_v2.parse(
        act_precheck.parse_request_v2(
            merge_req(profile, kind="finalize", changes=[["added", path, None]])
        )
    )
    assert isinstance(parsed, act_precheck_v2.MergeRequest)
    got = act_precheck_v2._envelopes(
        parsed,
        _Files({}),  # type: ignore[arg-type]
        WS,
        current,
        Accepted("n" * 40, "df-reviewer[bot]", review_id="R1"),
    )
    assert got == (
        3,
        (
            "merge_commit_mismatch: delivery decision nnnnnnnnnnnn != base history "
            "mmmmmmmmmmmm"
        ),
    )


def test_candidate_real_load_ranges_reads_to_pinned_p(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review I3 (К): the candidate's policy history is read by the real
    `load_ranges` up to the pinned P, not the current branch head."""
    monkeypatch.setattr(act_trust, "load_ranges", REAL_LOAD_RANGES)
    monkeypatch.setattr(
        act_trust,
        "load_anchor_times",
        lambda *a: {"b" * 40: parse_ts("2026-10-12T08:00:00Z")},
    )
    heads: list[str | None] = []

    def history(transport, source, anchor, until, *, head=None):  # noqa: ANN001, ANN202
        heads.append(head)
        return (act_facts.ActivationFact(1, ACT, parse_ts("2026-10-12T08:00:00Z"), {}),)

    monkeypatch.setattr(act_facts, "history", history)
    monkeypatch.setattr(act_trust, "policy_set", lambda *a: POLICY)
    verdict, _ = run(tmp_path / "home", merge_req(profile), gh)
    assert verdict["outcome"] == "allow"
    assert heads and set(heads) == {P}


def test_candidate_rename_from_foreign_path_refused(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """Review I4: a rename out of a foreign path deletes it — the `previous`
    side is surface-checked too."""
    changes = [*CHANGES[1:], ["renamed", NODE_FILE, "scripts/foreign.sh"]]
    verdict, _ = run(tmp_path / "home", merge_req(profile, changes=changes), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "surface_violation: scripts/foreign.sh"


def test_candidate_removed_manifest_is_not_changed(
    tmp_path: Path, wire: _Wire, gh: Path, profile: Path, candidate: Candidate
) -> None:
    """Review I4: the manifest counts only when added or modified."""
    manifest_row = ["removed", manifest_path(WS, "approve-2-1-1"), None]
    changes = [CHANGES[0], manifest_row, CHANGES[2]]
    verdict, _ = run(tmp_path / "home", merge_req(profile, changes=changes), gh)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"].endswith("is not changed by this PR")


def test_finalize_rename_onto_envelope_refused(
    tmp_path: Path,
    wire: _Wire,
    gh: Path,
    profile: Path,
    product: tuple[Repo, GitServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review I4: a foreign file renamed onto the expected envelope path has an
    exact `touched` set — only the plain-change rule refuses it."""
    repo, srv = product
    fin = _finalize_world(repo)
    _accepting(monkeypatch, fin["merge"], [])
    renamed = [["renamed", fin["path"], "README.md"]]
    verdict = _finalize_run(tmp_path, repo, srv, fin, profile, gh, changes=renamed)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"].startswith("envelope_set: the PR changes")


def test_premerge_deps_without_approved_gh_is_invariant(profile: Path) -> None:
    req = act_precheck_v2.parse(act_precheck.parse_request_v2(merge_req(profile)))
    assert isinstance(req, act_precheck_v2.MergeRequest)
    ctx = act_precheck_v2.Context(ADMIN, profile, None, GIT, deps(), 0.0)
    adapter = act_precheck_v2.Adapter("clear", X)
    with pytest.raises(ValueError, match="premerge deps without an approved gh"):
        act_precheck_v2._premerge_deps(req, ctx, adapter, [])
