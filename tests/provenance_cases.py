"""Общие наборы дефектов акта и политики (спека need-stage §11.7, T14–T27, T31).

Каждый случай портит РОВНО один факт согласованного мира `consistent_world()`.
Один набор гоняют предикаты (`tests/test_brief_provenance.py`, Task 4) и полный
вход spec-loop (`tests/test_governance_spec_loop.py`, Task 8 части B): новая
форма дефекта, добавленная сюда, проверяется на обоих уровнях сразу.
"""

from __future__ import annotations

from collections.abc import Callable

from governance import approval_request as ar
from governance import discovery_approval as da
from tests.forge_fake import (
    C1,
    DIR,
    DRAFT,
    HUMAN,
    MERGE,
    MERGED_AT,
    POLICY_PATH,
    POLICY_REPO,
    PR,
    SIGNED,
    FakeForge,
    P,
    request,
)

Spoil = Callable[[FakeForge], None]

BRIEF = f"{DIR}/brief.md"
REQUEST = f"{DIR}/{ar.FILE_NAME}"
#: Другой бриф того же формата, честно подписанный тем же событием мержа.
OTHER_DRAFT = DRAFT.replace("## Goals", "## Goals\n\nиной бриф\n", 1)
OTHER_SIGNED = da._approval.stamp(
    OTHER_DRAFT, da._approval.MergeEvent(HUMAN, MERGED_AT, MERGE)
)


def _request_file(forge: FakeForge, **changes: str) -> None:
    forge.files[(MERGE, REQUEST)] = ar.render(request(**changes))


def _commit_other_brief(forge: FakeForge) -> None:
    """Бриф и заявка в merge-коммите согласованы между собой, но бриф иной."""
    forge.files[(MERGE, BRIEF)] = OTHER_DRAFT
    _request_file(forge, brief_self_hash=da.self_hash(OTHER_DRAFT))


def _files(*files: tuple[str, str]) -> Spoil:
    return lambda f: f.set_pr(files=files)


#: (id, порча, причина `read_act`) — T19–T22, T25.
ACT_DEFECTS: list[tuple[str, Spoil, str]] = [
    (
        "not-merged",
        lambda f: f.set_pr(
            state="OPEN", merged_by=None, merged_at=None, merge_commit=None
        ),
        "pr_not_merged",
    ),
    ("wrong-base", lambda f: f.set_pr(base_ref="side"), "pr_wrong_base"),
    ("only-brief", _files((BRIEF, "added")), "pr_files"),
    (
        "extra-file",
        lambda f: f.set_pr(files=f.prs[PR].files + (("x.txt", "added"),)),
        "pr_files",
    ),
    (
        "bundle-pr",
        lambda f: f.set_pr(
            files=f.prs[PR].files + (("x/30-decomposition.md", "added"),)
        ),
        "pr_files",
    ),
    ("modified", _files((BRIEF, "modified"), (REQUEST, "added")), "pr_files"),
    (
        "two-dirs",
        _files(
            ("a/00-discovery/brief.md", "added"),
            (f"b/00-discovery/{ar.FILE_NAME}", "added"),
        ),
        "pr_files",
    ),
    (
        "not-00-discovery",
        _files(("a/brief.md", "added"), (f"a/{ar.FILE_NAME}", "added")),
        "pr_files",
    ),
    (
        "coords-repo",
        lambda f: _request_file(f, policy_repo="o/other"),
        "request_coordinates",
    ),
    ("coords-ref", lambda f: _request_file(f, policy_ref="dev"), "request_coordinates"),
    (
        "coords-path",
        lambda f: _request_file(f, policy_path="x.env"),
        "request_coordinates",
    ),
    (
        "brief-hash",
        lambda f: f.files.__setitem__((MERGE, BRIEF), DRAFT + "\nправка\n"),
        "brief_hash",
    ),
    (
        "p-not-version",
        lambda f: f.policy_versions.__setitem__(P, False),
        "not_policy_version",
    ),
    (
        "merger-out-of-p",
        lambda f: f.files.__setitem__(
            (P, POLICY_PATH), "AUTHORIZED_APPROVER_ACCOUNTS=other\n"
        ),
        "merger_not_in_act_policy",
    ),
]


def _none(forge: FakeForge) -> None:
    return None


#: (id, порча мира, буфер оператора, причина `check_operator_brief`) — T14–T18, T22.
OPERATOR_DEFECTS: list[tuple[str, Spoil, str, str]] = [
    ("draft", _none, DRAFT, "operator_brief"),
    (
        "no-hash",
        _none,
        SIGNED.replace("approved_content_hash", "x_hash"),
        "operator_brief",
    ),
    ("edited", _none, SIGNED + "\nправка\n", "operator_brief"),
    ("crlf", _none, SIGNED.replace("\n", "\r\n"), "operator_brief"),
    (
        "approver",
        _none,
        SIGNED.replace(f"approver: {HUMAN}", "approver: someone"),
        "envelope_mismatch",
    ),
    (
        "approved-at",
        _none,
        SIGNED.replace(
            "approved_at: '2026-10-08T10:00:00Z'",
            "approved_at: '2026-10-08T11:00:00Z'",
        ),
        "envelope_mismatch",
    ),
    # T22, вторая пара: честно подписанный, но ДРУГОЙ бриф в буфере.
    ("buffer-other-brief", _none, OTHER_SIGNED, "operator_brief"),
    # T22: бриф и заявка в коммите согласованы, но это не бриф буфера.
    ("commit-other-brief", _commit_other_brief, SIGNED, "operator_brief"),
]


def _reconfirm(sha: str = C1, **kwargs: str) -> Spoil:
    def spoil(forge: FakeForge) -> None:
        forge.add_policy(C1, f"{HUMAN},helper")
        forge.reconfirm(sha, **kwargs)

    return spoil


def _drift(forge: FakeForge) -> None:
    forge.add_policy(C1)


def _merger_out_of_c(forge: FakeForge) -> None:
    forge.add_policy(C1, "helper")
    forge.reconfirm(C1, author="helper")


#: (id, порча, причина `check_policy`) — T26, T27 (каждый двойник по одному дефекту).
POLICY_DEFECTS: list[tuple[str, Spoil, str]] = [
    ("drift-no-reconfirm", _drift, "upstream_policy_drift"),
    ("author-not-in-policy", _reconfirm(author="stranger"), "upstream_policy_drift"),
    ("ai-prosto", _reconfirm(author="ai-prosto"), "upstream_policy_drift"),
    ("edited", _reconfirm(edited="2026-10-09T01:00:00Z"), "upstream_policy_drift"),
    (
        "before-merge",
        _reconfirm(created="2026-10-07T00:00:00Z"),
        "upstream_policy_drift",
    ),
    ("not-a-version", _reconfirm("9" * 40), "upstream_policy_drift"),
    ("points-to-p", _reconfirm(P), "upstream_policy_drift"),
    (
        "other-coords",
        _reconfirm(body=f"policy-reconfirm: o/other@{C1}"),
        "upstream_policy_drift",
    ),
    (
        "extra-text",
        _reconfirm(body=f"policy-reconfirm: {POLICY_REPO}@{C1} спасибо"),
        "upstream_policy_drift",
    ),
    ("merger-out-of-c", _merger_out_of_c, "merger_not_in_current_policy"),
]

#: Факты, недоступность которых — «повторите» (T31): акт и политика.
ACT_UNAVAILABLE = ["pr", "default", "file", "history"]
POLICY_UNAVAILABLE = ["comments", "policy"]


def ids(cases: list) -> list[str]:
    """Идентификаторы параметризации — первый элемент случая."""
    return [case[0] for case in cases]
