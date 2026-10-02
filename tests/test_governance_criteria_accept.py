"""criteria_accept — чистые решения среза 2a (спека §7.2a, §3.2, §3.5)."""

from dataclasses import replace

import pytest

from governance import criteria_accept as ca
from governance.frontmatter import split_frontmatter

CLOSURE = (
    "---\nworkstream: ws\ncode: ENC\nproduct_sha: abc\nclosure: traced\n"
    "human_pending: 0\n---\n# Закрытие воркстрима\n\nтело\n"
)
SOURCE = "github:o/approval-policy@" + "a" * 40 + ":policy/approvers.env"


def _p(human=False, accounts=("owner",)):
    return ca.Proposal(
        head="h1",
        base="master",
        text_sha256="s1",
        human=human,
        accounts=frozenset(accounts),
    )


def _f(**over):
    base = ca.AcceptFacts(
        state="MERGED",
        head="h1",
        base="master",
        merged_by="ai-prosto",
        merge_oid="m1",
        merged_blob_sha256="s1",
        nodes_fresh=True,
        approval_pr_open=False,
        product_on_tip=True,
        agent_login="ai-prosto",
    )
    return replace(base, **over)


def test_proposal_text_adds_status_and_source_keeps_body():
    text = ca.proposal_text(CLOSURE, SOURCE)
    meta, body = split_frontmatter(text)
    assert meta["status"] == "proposed" and meta["policy_source"] == SOURCE
    assert body == split_frontmatter(CLOSURE)[1]
    assert list(meta)[:2] == ["workstream", "code"]


def test_stamp_differs_from_proposal_only_by_three_fields():
    prop = ca.proposal_text(CLOSURE, SOURCE)
    stamp = ca.stamp_text(prop, merge_oid="m1", pr=7)
    pm, pb = split_frontmatter(prop)
    sm, sb = split_frontmatter(stamp)
    assert pb == sb
    assert sm["status"] == "accepted"
    assert (sm["accepted_merge"], sm["accepted_pr"]) == ("m1", 7)
    assert {
        k: v
        for k, v in sm.items()
        if k not in ("status", "accepted_merge", "accepted_pr")
    } == {k: v for k, v in pm.items() if k != "status"}


def test_stamp_refuses_non_proposal():
    with pytest.raises(ValueError):
        ca.stamp_text(CLOSURE, merge_oid="m1", pr=7)


def test_test_only_merged_by_agent_is_stamping():
    assert ca.predicate(_p(), _f()).kind == "stamping"


def test_test_only_merged_by_snapshot_account_is_stamping():
    assert ca.predicate(_p(), _f(merged_by="owner")).kind == "stamping"


def test_human_merged_by_snapshot_account_is_stamping():
    assert ca.predicate(_p(human=True), _f(merged_by="owner")).kind == "stamping"


@pytest.mark.parametrize("login", ["ai-prosto", "stranger"])
def test_human_merged_by_agent_or_outsider_is_rejected(login):
    """§8.4 п.5: человеческий критерий, мерж не человеком из снимка."""
    v = ca.predicate(_p(human=True), _f(merged_by=login))
    assert v.kind == "rejected" and "act" in v.reason


def test_human_with_agent_listed_in_policy_still_rejected():
    v = ca.predicate(_p(human=True, accounts=("owner", "ai-prosto")), _f())
    assert v.kind == "rejected"


def test_test_only_outsider_rejected():
    assert ca.predicate(_p(), _f(merged_by="stranger")).kind == "rejected"


@pytest.mark.parametrize(
    ("over", "kind", "word"),
    [
        ({"state": "OPEN"}, "waiting", "open"),
        ({"state": "CLOSED"}, "rejected", "closed"),
        ({"head": "h2"}, "rejected", "head-moved"),
        ({"base": "main"}, "rejected", "base"),
        ({"merged_blob_sha256": "s2"}, "rejected", "blob"),
        ({"nodes_fresh": False}, "superseded", "bundle"),
        ({"approval_pr_open": True}, "superseded", "approval"),
        ({"product_on_tip": False}, "superseded", "product"),
    ],
)
def test_predicate_table(over, kind, word):
    v = ca.predicate(_p(), _f(**over))
    assert v.kind == kind and word in v.reason


@pytest.mark.parametrize(
    "over",
    [
        {"state": None},
        {"state": "WEIRD"},
        {"head": None},
        {"merged_blob_sha256": None},
        {"nodes_fresh": None},
        {"approval_pr_open": None},
        {"product_on_tip": None},
        {"merged_by": None},
        {"merge_oid": None},
    ],
)
def test_unestablished_fact_is_unavailable_never_terminal(over):
    assert ca.predicate(_p(), _f(**over)).kind == "unavailable"


def test_human_needs_agent_login_to_exclude_it():
    v = ca.predicate(_p(human=True), _f(merged_by="owner", agent_login=None))
    assert v.kind == "unavailable"


def test_test_only_outsider_with_unknown_agent_is_unavailable():
    """Не узнали учётку агента — «чужой» не установлен положительно."""
    v = ca.predicate(_p(), _f(merged_by="stranger", agent_login=None))
    assert v.kind == "unavailable"


def test_check_stamp():
    assert ca.check_stamp("x", "x") == "valid"
    assert ca.check_stamp("x", "y") == "invalid"
    assert ca.check_stamp("x", "") == "invalid"  # файла в default нет
    assert ca.check_stamp("x", None) == "unavailable"


def test_foreign_head_wins_over_unread_blob():
    """Ревью круга 2 B-M3: голова установленно чужая — rejected, не unavailable."""
    v = ca.predicate(_p(), _f(head="h2", merged_blob_sha256=None))
    assert v.kind == "rejected" and "head-moved" in v.reason


def test_absent_closure_in_merge_commit_is_rejected():
    v = ca.predicate(_p(), _f(merged_blob_sha256=""))
    assert v.kind == "rejected" and "blob" in v.reason


def test_unrecorded_head_is_unavailable_not_head_moved():
    """Ревью пары B2: незаписанная голова — не «head-moved»."""
    p = replace(_p(), head=None)
    assert ca.predicate(p, _f()).kind == "unavailable"
