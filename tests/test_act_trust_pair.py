"""The `df-trust` pair by SHA (installer spec §6.1, `op: trust`; plan 3a Task 5)."""

from __future__ import annotations

import hashlib
from collections import Counter

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
    assert len(got.findings) == 1
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


def _reads(anon: Anon, kind: str) -> Counter[str]:
    return Counter(
        p.rsplit("/", 1)[1] if kind == "commits" else p.rsplit("=", 1)[1]
        for p in anon.sent
        if p.startswith(f"repos/{DT}/{kind}")
    )


def test_cold_descendant_and_warm_read_each_object_once() -> None:
    """M-1: the cold descendant walk goes on below A; the warm path reuses the
    journal on A. Commit reads = one walk read + one per contents read there."""
    _got, cold = pair(world(LINEAR), base(C1), C2, v="admin-digest-2/rules-1")
    assert _reads(cold, "commits") == Counter({C0: 2, C1: 2, C2: 3})
    assert _reads(cold, "contents/epochs.yaml") == Counter({C0: 1, C1: 1, C2: 1})
    _got, warm = pair(world(LINEAR), base(C1), C2)
    assert _reads(warm, "commits") == Counter({C1: 2, C2: 3})
    assert _reads(warm, "contents/epochs.yaml") == Counter({C1: 1, C2: 1})


def test_gone_ancestor_on_cold_descendant_path() -> None:
    """M-2: the relation is proven by the A..H walk; the journal is not verified."""
    routes = world(LINEAR)
    routes[f"repos/{DT}/commits/{C0}"] = (404, {"message": "Not Found"})
    got, _anon = pair(routes, base(C1), C2, v="admin-digest-2/rules-1")
    assert isinstance(got, act_trust_pair.PairResult)
    assert got.relation == "descendant"
    assert got.snapshot is None
    assert got.source == act_trust_findings.source_descriptor(
        parse_source(SOURCE_ENV), SOURCE_ENV.encode()
    )
    assert [(f.kind, f.role) for f in got.findings] == [
        ("history_unverifiable", "df-trust")
    ]


@pytest.mark.parametrize(
    "repo_id,role", [("11", "df-trust"), ("22", "approval-policy")], ids=["dt", "ap"]
)
def test_pair_private_repo_is_not_public_finding(repo_id: str, role: str) -> None:
    routes = world(LINEAR)
    name = DT if role == "df-trust" else AP
    routes[f"repositories/{repo_id}"] = (200, {"full_name": name, "private": True})
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, act_trust_pair.PairResult)
    assert [(f.kind, f.role) for f in got.findings] == [("not_public", role)]
    assert got.findings[0].detail == f"{name}: репо не публичный (private = true)"
    # the read went on: the history is verified as usual
    assert got.relation == "descendant" and got.snapshot is not None


def test_pair_repository_404_stays_unavailable() -> None:
    routes = world(LINEAR)
    routes["repositories/22"] = (404, {"message": "Not Found"})
    got, _anon = pair(routes, base(C1), C2)
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("repositories/22: HTTP 404")


def test_load_trust_still_refuses_private_repo() -> None:
    """I-4: `not_public` is pair-side only; `_named` keeps refusing."""
    routes = world(LINEAR)
    routes["repositories/22"] = (200, {"full_name": AP, "private": True})
    got = act_trust._named(Anon(routes), 22, AP)
    assert isinstance(got, Unavailable)
    assert "только публичные" in got.reason


# --- settings --------------------------------------------------------------------


def settings_routes(
    *,
    rebase: dict[str, object] | None = None,
    push: tuple[str, ...] = (),
    rules: dict[str, list[str]] | None = None,
) -> dict:
    routes = world(LINEAR)
    rebase = rebase or {}
    for repo in (DT, AP):
        kinds = (rules or {}).get(
            repo, ["deletion", "non_fast_forward", "pull_request"]
        )
        routes[f"repos/{repo}/rules/branches/main"] = (
            200,
            [{"type": k} for k in kinds],
        )
        body: dict = {"full_name": repo, **_perms(repo in push)}
        if repo in rebase:
            body["allow_rebase_merge"] = rebase[repo]
        routes[f"repos/{repo}"] = (200, body)
    return routes


def snapshot(routes: dict) -> tuple[act_trust.TrustSnapshot, Anon]:
    anon = Anon(routes)
    got = act_trust_pair.read_pair(anon, binding(), base(C1), C2, validation=V1)
    assert isinstance(got, act_trust_pair.PairResult) and got.snapshot is not None
    return got.snapshot, anon


def settings(routes: dict, *, branch: str = "main", keyed: bool = True):
    snap, anon = snapshot(routes)
    return act_trust_pair.settings_findings(
        anon, snap, policy_branch=branch, keyed=keyed
    )


def test_settings_hidden_rebase_is_unverified_per_role() -> None:
    got = settings(settings_routes(rebase={AP: False}))
    assert got == act_trust_pair.Settings((), ("allow_rebase_merge@df-trust",))
    got = settings(settings_routes(rebase={DT: False}))
    assert got == act_trust_pair.Settings((), ("allow_rebase_merge@approval-policy",))
    got = settings(settings_routes(rebase={DT: False, AP: False}))
    assert got == act_trust_pair.Settings((), ())


def test_unverified_codes_closed_set() -> None:
    assert act_trust_pair.UNVERIFIED == {
        "allow_rebase_merge@df-trust",
        "allow_rebase_merge@approval-policy",
        "history_token_required",
    }


def test_df_trust_rebase_allowed_finding() -> None:
    got = settings(settings_routes(rebase={DT: True, AP: False}))
    assert isinstance(got, act_trust_pair.Settings)
    assert got.findings == (
        Finding("rebase_allowed", "df-trust", f"{DT}: allow_rebase_merge = true"),
    )


def test_settings_checks_called_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    snap, anon = snapshot(settings_routes(rebase={DT: False, AP: False}))
    calls: list[tuple[str, str]] = []

    def record(name: str):
        return lambda t, s, role="approval-policy": calls.append((name, role))

    monkeypatch.setattr(act_trust, "_history_report_of", record("self_report"))
    monkeypatch.setattr(act_trust, "_ruleset_of", record("rulesets"))
    monkeypatch.setattr(act_trust, "_rebase", record("rebase"))
    got = act_trust_pair.settings_findings(anon, snap, policy_branch="main", keyed=True)
    assert got == act_trust_pair.Settings((), ())
    assert calls == [
        (name, role)
        for name in ("self_report", "rulesets", "rebase")
        for role in ("approval-policy", "df-trust")
    ]


def test_runtime_source_branch_not_default_finding() -> None:
    got = settings(settings_routes(rebase={DT: False, AP: False}), branch="trunk")
    assert isinstance(got, act_trust_pair.Settings)
    assert [(f.kind, f.role) for f in got.findings] == [
        ("policy_branch_mismatch", "approval-policy")
    ]
    assert "default branch 'trunk'" in got.findings[0].detail


def test_settings_both_roles_reported() -> None:
    """I-3: one `token_write` and one `ruleset_missing` per role."""
    routes = settings_routes(
        rebase={DT: False, AP: False},
        push=(DT, AP),
        rules={DT: ["deletion"], AP: ["deletion"]},
    )
    got = settings(routes)
    assert isinstance(got, act_trust_pair.Settings)
    assert [(f.kind, f.role) for f in got.findings] == [
        ("token_write", "approval-policy"),
        ("token_write", "df-trust"),
        ("ruleset_missing", "approval-policy"),
        ("ruleset_missing", "df-trust"),
    ]


def test_settings_unreadable_first_repo_does_not_mask_second() -> None:
    routes = settings_routes(rebase={DT: False, AP: False}, rules={DT: ["deletion"]})
    del routes[f"repos/{AP}/rules/branches/main"]
    got = settings(routes)
    assert isinstance(got, act_trust_pair.Settings)
    assert [(f.kind, f.role) for f in got.findings] == [("ruleset_missing", "df-trust")]


def test_settings_unreadable_is_unavailable_unless_finding() -> None:
    routes = settings_routes(rebase={DT: False, AP: False})
    del routes[f"repos/{AP}/rules/branches/main"]
    got = settings(routes)
    assert isinstance(got, Unavailable)
    got = settings(routes, branch="trunk")
    assert isinstance(got, act_trust_pair.Settings)
    assert [f.kind for f in got.findings] == ["policy_branch_mismatch"]


def test_settings_keyed_unreadable_self_report_is_unavailable() -> None:
    """O14: with a history key an unreadable self-report never falls back."""
    routes = settings_routes()
    for repo in (DT, AP):
        routes[f"repos/{repo}"] = (
            200,
            {"full_name": repo, "allow_rebase_merge": False},
        )
    got = settings(routes)
    assert isinstance(got, Unavailable)
    assert got.reason.startswith("самоотчёт profile:history")


def test_settings_unkeyed_anonymous_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O14: without a history key the check runs anonymously, with the warning."""
    routes = settings_routes()
    for repo in (DT, AP):
        routes[f"repos/{repo}"] = (
            200,
            {"full_name": repo, "allow_rebase_merge": False},
        )
    monkeypatch.setattr(
        act_trust, "_history_report_of", lambda *a: pytest.fail("self-report called")
    )
    got = settings(routes, keyed=False)
    assert got == act_trust_pair.Settings((), ("history_token_required",))
