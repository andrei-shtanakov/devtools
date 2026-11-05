"""The `df-trust` pair by SHA (installer spec §6.1, `op: trust`; plan 3a Task 5)."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("steward")

from governance import act_trust, act_trust_findings, act_trust_pair  # noqa: E402
from governance.act_source import parse_source  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402
from governance.act_trust_types import AcceptedBase, Finding  # noqa: E402
from tests.test_act_trust import (  # noqa: E402
    AP,
    BOOT,
    DT,
    J1,
    J2,
    SOURCE_ENV,
    Anon,
    _commit_world,
    _perms,
    binding,
)

C0, C1, C2, D1 = "0" * 40, "1" * 40, "2" * 40, "d" * 40
V1 = "admin-digest-1/rules-1"
DIGEST = hashlib.sha256(SOURCE_ENV.encode()).hexdigest()
OTHER_BOOT = "e" * 40
J_OTHER = f"schema: approval-policy-epochs/v1\nepochs:\n  - epoch: 1\n    anchor: {OTHER_BOOT}\n"


def world(
    commits: dict[str, tuple[str | None, str | None]],
    *,
    source: dict[str, str] | None = None,
) -> dict:
    """`df-trust` commits: sha → (first parent, journal text); `source.env` per sha."""
    routes: dict[str, tuple[int, object]] = {
        "repositories/11": (200, {"full_name": DT, "private": False}),
        "repositories/22": (200, {"full_name": AP, "private": False}),
    }
    for sha, (parent, journal) in commits.items():
        files = {"source.env": (source or {}).get(sha, SOURCE_ENV)}
        if journal is not None:
            files["epochs.yaml"] = journal
        parents = [{"sha": parent}] if parent else []
        routes.update(_commit_world(DT, sha, files, sha=sha, parents=parents))
    return routes


LINEAR = {C0: (None, J1), C1: (C0, J1), C2: (C1, J2)}


def base(sha: str, *, epoch: int = 1, digest: str = DIGEST, v: str = V1):
    return AcceptedBase(sha, epoch, digest, v)


def pair(routes: dict, accepted: AcceptedBase | None, head: str, v: str = V1):
    anon = Anon(routes)
    got = act_trust_pair.read_pair(anon, binding(), accepted, head, validation=v)
    return got, anon


def _commits(anon: Anon) -> list[str]:
    return [p.rsplit("/", 1)[1] for p in anon.sent if f"repos/{DT}/commits/" in p]


def test_pair_descendant_incremental_from_A() -> None:
    got, anon = pair(world(LINEAR), base(C1), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "descendant"
    assert got.findings == ()
    assert got.snapshot is not None
    assert got.snapshot.root.snapshot_sha == C2
    assert [e.epoch for e in got.snapshot.epochs] == [1, 2]
    # incremental: nothing below A is read
    assert C0 not in _commits(anon)
    assert f"repos/{DT}/contents/epochs.yaml?ref={C1}" in anon.sent


def test_pair_same_head_is_descendant() -> None:
    got, _anon = pair(world(LINEAR), base(C2, epoch=2), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "descendant"
    assert got.snapshot is not None and got.findings == ()


def test_pair_first_head_cold_without_relation() -> None:
    got, anon = pair(world(LINEAR), None, C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "first"
    assert got.findings == ()
    assert C0 in _commits(anon)


def test_pair_rollback_is_not_descendant() -> None:
    got, _anon = pair(world(LINEAR), base(C2, epoch=2), C1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "not_descendant"
    assert [(f.kind, f.role) for f in got.findings] == [("not_descendant", "df-trust")]
    assert "не потомок принятой" in got.findings[0].detail
    # the journal of H is still verified (cold): H can be accepted with the finding
    assert got.snapshot is not None
    assert [e.epoch for e in got.snapshot.epochs] == [1]


def test_pair_other_validated_branch_not_descendant() -> None:
    routes = world({C0: (None, J1), C1: (C0, J1), D1: (C0, J1)})
    got, anon = pair(routes, base(C1), D1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "not_descendant"
    assert [f.kind for f in got.findings] == ["not_descendant"]
    assert C1 not in _commits(anon)


def test_pair_reads_by_sha_not_branch() -> None:
    routes = world(LINEAR)
    # the current branch head points elsewhere; the pair never asks for it
    routes[f"repos/{DT}/branches/main"] = (200, {"commit": {"sha": C0}})
    got, anon = pair(routes, base(C1), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.snapshot is not None and got.snapshot.root.snapshot_sha == C2
    assert [p for p in anon.sent if "/branches/" in p] == []


def test_changed_bootstrap_warm_equals_cold() -> None:
    changed = SOURCE_ENV.replace(BOOT, OTHER_BOOT)
    routes = world({C0: (None, J1), C1: (C0, J1)}, source={C1: changed})
    warm, _a = pair(routes, base(C0), C1)
    cold, _b = pair(routes, None, C1)
    assert isinstance(warm, act_trust_pair.PairResult)
    assert isinstance(cold, act_trust_pair.PairResult)
    assert [f.kind for f in warm.findings] == ["bootstrap_mismatch"]
    assert warm.findings == cold.findings
    assert warm.snapshot is None and cold.snapshot is None
    assert warm.source == cold.source and warm.source is not None
    assert warm.source.bootstrap_activation == OTHER_BOOT


def test_validation_change_goes_cold() -> None:
    # the journal never started from BOOTSTRAP_ACTIVATION; only a cold pass sees it
    routes = world({C0: (None, J_OTHER), C1: (C0, J_OTHER)})
    warm, _a = pair(routes, base(C0), C1)
    cold, _b = pair(routes, base(C0), C1, v="admin-digest-2/rules-1")
    assert isinstance(warm, act_trust_pair.PairResult)
    assert isinstance(cold, act_trust_pair.PairResult)
    assert warm.findings == () and warm.relation == "descendant"
    assert [f.kind for f in cold.findings] == ["bootstrap_mismatch"]
    assert "не bootstrap" in cold.findings[0].detail
    assert cold.relation == "descendant"


def test_source_digest_change_goes_cold() -> None:
    routes = world({C0: (None, J_OTHER), C1: (C0, J_OTHER)})
    got, _anon = pair(routes, base(C0, digest="f" * 64), C1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [f.kind for f in got.findings] == ["bootstrap_mismatch"]


def test_base_epoch_not_on_A_goes_cold() -> None:
    routes = world({C0: (None, J_OTHER), C1: (C0, J_OTHER)})
    got, _anon = pair(routes, base(C0, epoch=7), C1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [f.kind for f in got.findings] == ["bootstrap_mismatch"]


@pytest.mark.parametrize("status", [404, 422])
def test_unreadable_head_objects_history_unverifiable(status: int) -> None:
    routes = world(LINEAR)
    routes[f"repos/{DT}/commits/{C2}"] = (status, {"message": "No commit found"})
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [(f.kind, f.role) for f in got.findings] == [
        ("history_unverifiable", "df-trust")
    ]
    assert got.findings[0].detail.endswith(f"коммит не читается (HTTP {status})")
    assert got.snapshot is None and got.source is None
    assert got.relation == "not_descendant"


def test_head_read_temporarily_failing_is_unavailable() -> None:
    routes = world(LINEAR)
    routes[f"repos/{DT}/commits/{C2}"] = (500, {})
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, Unavailable)
    assert got.reason == f"repos/{DT}/commits/{C2}: HTTP 500"


def test_journal_unreadable_is_unavailable_not_finding() -> None:
    routes = world(LINEAR)
    del routes[f"repos/{DT}/contents/epochs.yaml?ref={C2}"]
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, Unavailable)


@pytest.mark.parametrize(
    "text,expected",
    [(None, "нет source.env"), ("APPROVAL_POLICY_REPO=x\n", "ожидалась ровно одна")],
    ids=["absent", "invalid"],
)
def test_source_env_invalid_finding(text: str | None, expected: str) -> None:
    routes = world({C0: (None, J1)})
    if text is None:
        routes = {k: v for k, v in routes.items() if "source.env" not in k}
        routes.update(_commit_world(DT, C0, {"epochs.yaml": J1}, sha=C0, parents=[]))
    else:
        routes = world({C0: (None, J1)}, source={C0: text})
    got, _anon = pair(routes, None, C0)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [f.kind for f in got.findings] == ["source_env_invalid"]
    assert expected in got.findings[0].detail
    assert got.snapshot is None and got.source is None


def test_journal_parse_and_transition_findings() -> None:
    broken = {C0: (None, J1), C1: (C0, "schema: nope\n")}
    got, _anon = pair(world(broken), None, C1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [f.kind for f in got.findings] == ["journal_parse"]
    assert got.findings[0].kind == "journal_parse"
    deleted = {C0: (None, J1), C1: (C0, None)}
    got, _anon = pair(world(deleted), base(C0), C1)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [f.kind for f in got.findings] == ["journal_transition"]
    assert "epochs.yaml удалён" in got.findings[0].detail


def test_pair_source_descriptor_from_findings_module() -> None:
    got, _anon = pair(world(LINEAR), base(C1), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    raw = SOURCE_ENV.encode()
    assert got.source == act_trust_findings.source_descriptor(
        parse_source(SOURCE_ENV), raw
    )


def test_pair_root_identity_checked() -> None:
    routes = world(LINEAR)
    routes["repositories/11"] = (200, {"full_name": "evil/df-trust", "private": False})
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, Unavailable)
    assert "привязка —" in got.reason


def test_pair_refuses_non_sha() -> None:
    got, anon = pair(world(LINEAR), base(C1), "main")
    assert isinstance(got, Unavailable)
    assert got.reason == "пара df-trust: A и H — полные SHA"
    assert anon.sent == []


# --- settings --------------------------------------------------------------------


def settings_routes(
    *, rebase: dict[str, object] | None = None, push: bool = False
) -> dict:
    routes = world(LINEAR)
    rebase = rebase or {}
    for repo in (DT, AP):
        routes[f"repos/{repo}/rules/branches/main"] = (
            200,
            [{"type": k} for k in ("deletion", "non_fast_forward", "pull_request")],
        )
        body: dict = {"full_name": repo, **_perms(push and repo == AP)}
        if repo in rebase:
            body["allow_rebase_merge"] = rebase[repo]
        routes[f"repos/{repo}"] = (200, body)
    return routes


def snapshot(routes: dict) -> tuple[act_trust.TrustSnapshot, Anon]:
    anon = Anon(routes)
    got = act_trust_pair.read_pair(anon, binding(), base(C1), C2, validation=V1)
    assert isinstance(got, act_trust_pair.PairResult) and got.snapshot is not None
    return got.snapshot, anon


def test_settings_clean_hidden_rebase_no_finding() -> None:
    snap, anon = snapshot(settings_routes())
    assert act_trust_pair.settings_findings(anon, snap, policy_branch="main") == ()


def test_df_trust_rebase_allowed_finding() -> None:
    snap, anon = snapshot(settings_routes(rebase={DT: True, AP: False}))
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="main")
    assert got == (
        Finding("rebase_allowed", "df-trust", f"{DT}: allow_rebase_merge = true"),
    )


def test_settings_checks_called_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    snap, anon = snapshot(settings_routes())
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        act_trust,
        "_history_self_report",
        lambda t, s: calls.append(("self_report",)),
    )
    monkeypatch.setattr(
        act_trust, "_rulesets", lambda t, s: calls.append(("rulesets",))
    )
    monkeypatch.setattr(
        act_trust,
        "_rebase",
        lambda t, s, role="approval-policy": calls.append(("rebase", role)),
    )
    assert act_trust_pair.settings_findings(anon, snap, policy_branch="main") == ()
    assert calls == [
        ("self_report",),
        ("rulesets",),
        ("rebase", "approval-policy"),
        ("rebase", "df-trust"),
    ]


def test_runtime_source_branch_not_default_finding() -> None:
    snap, anon = snapshot(settings_routes())
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="trunk")
    assert [(f.kind, f.role) for f in got] == [
        ("policy_branch_mismatch", "approval-policy")
    ]
    assert isinstance(got, tuple)
    assert "default branch 'trunk'" in got[0].detail


def test_settings_token_write_and_ruleset_findings() -> None:
    routes = settings_routes(push=True)
    routes[f"repos/{DT}/rules/branches/main"] = (200, [{"type": "deletion"}])
    snap, anon = snapshot(routes)
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="main")
    assert isinstance(got, tuple)
    assert [(f.kind, f.role) for f in got] == [
        ("token_write", "approval-policy"),
        ("ruleset_missing", "df-trust"),
    ]


def test_settings_unreadable_is_unavailable_unless_finding() -> None:
    routes = settings_routes()
    del routes[f"repos/{AP}/rules/branches/main"]
    snap, anon = snapshot(routes)
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="main")
    assert isinstance(got, Unavailable)
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="trunk")
    assert isinstance(got, tuple)
    assert [f.kind for f in got] == ["policy_branch_mismatch"]


def test_settings_without_history_permissions_unavailable() -> None:
    routes = settings_routes()
    for repo in (DT, AP):
        routes[f"repos/{repo}"] = (200, {"full_name": repo})
    snap, anon = snapshot(routes)
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="main")
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("самоотчёт profile:history")
