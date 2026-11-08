"""Факты форджа brief-маршрута (§11.4.6): адаптер RealOps на записанных ответах gh.

Ответы — ровно то, что печатает `gh` (для `--jq` — уже отжатый вывод).
Неполнота любого рода — UNAVAILABLE, а не «что успели прочитать».
"""

from __future__ import annotations

import json
import subprocess

import pytest

from governance.facts import Outcome
from governance.ops import RealOps

REPO = "o/target"
HEAD = "1" * 40
MERGE = "2" * 40
SHA = "3" * 40


def _gh(monkeypatch, responses: list[tuple[int, object]]) -> list[list[str]]:
    calls: list[list[str]] = []
    queue = list(responses)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        assert queue, f"лишний вызов {argv}"
        rc, payload = queue.pop(0)
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _page(nodes, has_next=False, cursor=None, key="files", **pr):
    node = {
        "state": "MERGED",
        "baseRefName": "main",
        "headRefName": "brief/WS-1",
        "headRefOid": HEAD,
        "mergedBy": {"login": "human"},
        "mergedAt": "2026-10-08T10:00:00Z",
        "mergeCommit": {"oid": MERGE},
        key: {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        },
    }
    node.update(pr)
    return {"data": {"repository": {"pullRequest": node}}}


def _file(path, change="ADDED"):
    return {"path": path, "changeType": change}


def test_merged_pr_found(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_file("d/brief.md")]))])
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND
    assert fact.value.files == (("d/brief.md", "added"),)
    assert fact.value.merge_commit == MERGE and fact.value.head_sha == HEAD


def test_files_pagination_followed(monkeypatch) -> None:
    calls = _gh(
        monkeypatch,
        [
            (0, _page([_file("d/brief.md")], has_next=True, cursor="c1")),
            (0, _page([_file("d/approval-request.yaml")])),
        ],
    )
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert [f for f, _ in fact.value.files] == ["d/brief.md", "d/approval-request.yaml"]
    assert "c=c1" in calls[1]


@pytest.mark.parametrize(
    "pages",
    [
        [_page([_file("a")], has_next=True, cursor=None)],
        [
            _page([_file("a")], has_next=True, cursor="c1"),
            _page([], has_next=True, cursor="c1"),
        ],
        [
            _page([_file("a")], has_next=True, cursor="c1"),
            _page([], headRefOid="9" * 40),
        ],
    ],
    ids=["no-cursor", "cursor-stuck", "head-moved"],
)
def test_incomplete_pages_unavailable(monkeypatch, pages) -> None:
    _gh(monkeypatch, [(0, p) for p in pages])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_page_without_pageinfo_unavailable(monkeypatch) -> None:
    page = _page([_file("a")])
    del page["data"]["repository"]["pullRequest"]["files"]["pageInfo"]
    _gh(monkeypatch, [(0, page)])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "over",
    [
        {"state": "OPEN"},
        {"mergedBy": None},
        {"state": "WEIRD"},
        {"headRefOid": None},
    ],
    ids=["open-with-merge", "merged-no-login", "unknown-state", "no-head"],
)
def test_impossible_shapes_unavailable(monkeypatch, over) -> None:  # T48a
    _gh(monkeypatch, [(0, _page([_file("a")], **over))])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_open_pr_without_merge_fields_found(monkeypatch) -> None:
    page = _page(
        [_file("a")], state="OPEN", mergedBy=None, mergedAt=None, mergeCommit=None
    )
    _gh(monkeypatch, [(0, page)])
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND and fact.value.merge_commit is None


def test_rc_failure_unavailable(monkeypatch) -> None:
    _gh(monkeypatch, [(1, "")])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def _comment(**over):
    node = {
        "id": "C1",
        "author": {"login": "human"},
        "body": "policy-reconfirm: o/p@" + SHA,
        "createdAt": "2026-10-09T00:00:00Z",
        "lastEditedAt": None,
    }
    node.update(over)
    return node


def test_comments_found_with_null_edit(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_comment()], key="comments"))])
    fact = RealOps().pr_comments_fact(REPO, 7)
    assert fact.value[0].last_edited_at is None and fact.value[0].author == "human"


def test_comment_without_edit_field_unavailable(monkeypatch) -> None:
    node = _comment()
    del node["lastEditedAt"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    assert RealOps().pr_comments_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_find_brief_pr_all_states_and_limit(monkeypatch) -> None:
    calls = _gh(monkeypatch, [(0, [{"number": 9}, {"number": 3}])])
    fact = RealOps().find_brief_pr_fact(REPO, "brief/WS-1")
    assert fact.value == [3, 9]
    assert calls[0][calls[0].index("--state") + 1] == "all"
    _gh(monkeypatch, [(0, [{"number": n} for n in range(100)])])
    assert RealOps().find_brief_pr_fact(REPO, "b").outcome is Outcome.UNAVAILABLE


def test_default_branch_name_and_sha(monkeypatch) -> None:
    payload = {
        "data": {
            "repository": {
                "defaultBranchRef": {"name": "main", "target": {"oid": HEAD}}
            }
        }
    }
    _gh(monkeypatch, [(0, payload)])
    fact = RealOps().default_branch_fact(REPO)
    assert (fact.value.name, fact.value.sha) == ("main", HEAD)


def _touched(oid):
    return {
        "data": {
            "repository": {
                "object": {"history": {"nodes": [{"oid": oid}] if oid else []}}
            }
        }
    }


@pytest.mark.parametrize(
    ("status", "touched_oid", "expected"),
    [
        ("ahead", SHA, True),
        ("identical", SHA, True),
        ("ahead", "4" * 40, False),
        ("ahead", None, False),
    ],
    ids=["ancestor", "equal", "did-not-touch", "never-touched"],
)
def test_policy_version_at_found(
    monkeypatch, status, touched_oid, expected
) -> None:  # T21c
    _gh(monkeypatch, [(0, f"{status}\n"), (0, _touched(touched_oid))])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.FOUND and fact.value is expected


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_policy_version_at_not_in_history(monkeypatch, status) -> None:  # T21c
    calls = _gh(monkeypatch, [(0, f"{status}\n")])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.FOUND and fact.value is False
    assert len(calls) == 1
    assert calls[0][2] == f"repos/o/policy/compare/{SHA}...main"


_EXISTS = {"data": {"repository": {"object": {"history": {"nodes": []}}}}}


@pytest.mark.parametrize(
    "responses",
    [
        [(1, ""), (1, "")],  # compare и проверка существования — оба сбой
        [(1, ""), (0, {"data": None})],  # repository не прочитан
        [(1, ""), (0, _EXISTS)],  # коммит есть, compare не дал статуса
        [(0, "weird\n"), (0, {"data": {"repository": {}}})],  # нет поля object
        [(0, "ahead\n"), (0, {"data": None})],
    ],
    ids=["both-fail", "no-repository", "exists-compare-failed", "no-object", "ahead"],
)
def test_policy_version_at_unavailable(monkeypatch, responses) -> None:
    _gh(monkeypatch, responses)
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.UNAVAILABLE


def test_policy_version_at_unknown_sha_is_not_a_version(monkeypatch) -> None:
    """REST compare на несуществующий sha — 404 (замер 2026-10-08); GraphQL
    `object: null` — установленный факт «коммита нет»: не версия, не retry."""
    calls = _gh(monkeypatch, [(1, ""), (0, {"data": {"repository": {"object": None}}})])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.FOUND and fact.value is False
    assert calls[1][:3] == ["gh", "api", "graphql"]


def test_compare_files_and_cap(monkeypatch) -> None:
    _gh(monkeypatch, [(0, json.dumps([["d/brief.md", "added"]]))])
    assert RealOps().compare_files_fact(REPO, "a", "b").value == (
        ("d/brief.md", "added"),
    )
    _gh(monkeypatch, [(0, json.dumps([[f"f{i}", "added"] for i in range(300)]))])
    assert RealOps().compare_files_fact(REPO, "a", "b").outcome is Outcome.UNAVAILABLE


# --- неполный ответ ≠ отрицательный факт (ревью части A, A4) ---


def test_history_node_without_oid_is_unavailable(monkeypatch) -> None:
    payload = {"data": {"repository": {"object": {"history": {"nodes": [{}]}}}}}
    _gh(monkeypatch, [(0, "ahead\n"), (0, payload)])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "payload", [{}, [{"number": "7"}], [{}]], ids=["obj", "str", "empty"]
)
def test_find_brief_pr_malformed_is_unavailable(monkeypatch, payload) -> None:
    _gh(monkeypatch, [(0, payload)])
    assert RealOps().find_brief_pr_fact(REPO, "b").outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "payload", [{}, [["only-one"]], [["", "added"]]], ids=["obj", "short", "empty"]
)
def test_compare_malformed_is_unavailable(monkeypatch, payload) -> None:
    _gh(monkeypatch, [(0, json.dumps(payload))])
    assert RealOps().compare_files_fact(REPO, "a", "b").outcome is Outcome.UNAVAILABLE


def test_comment_without_author_field_is_unavailable(monkeypatch) -> None:
    node = _comment()
    del node["author"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    assert RealOps().pr_comments_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_comment_with_deleted_author_is_found_unknown(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_comment(author=None)], key="comments"))])
    fact = RealOps().pr_comments_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND and fact.value[0].author == ""


@pytest.mark.parametrize("over", [{"mergedBy": "bad"}, {"mergeCommit": ["x"]}])
def test_merge_fields_of_wrong_type_are_unavailable(monkeypatch, over) -> None:
    _gh(monkeypatch, [(0, _page([_file("a")], **over))])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


# --- синтетическая политика test (план 3a, Task 3) ---


def _test_config(monkeypatch, tmp_path, *, mode: str = "test") -> None:
    from pathlib import Path

    from tests.df_config_fixture import write_config

    strict = {
        "human": "bob",
        "agent_gh_config_dir": Path("~/.config/review"),
        "human_gh_config_dir": Path("~/.config/gh"),
    }
    extra = strict if mode == "strict" else {"human": "alice"}
    env = write_config(tmp_path, mode=mode, agent="alice", **extra)
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])


def _no_gh(monkeypatch) -> None:
    def boom(argv, **kwargs):
        raise AssertionError(f"вызов gh для синтетической политики: {argv}")

    monkeypatch.setattr(subprocess, "run", boom)


@pytest.mark.parametrize("facts", ["real_ops", "merge_check"])
def test_brief_facts_test_coords_never_call_gh(monkeypatch, tmp_path, facts) -> None:
    """Версия, `…_at` и состав для координат test — из конфига, без `gh`; и у
    RealOps, и у проверяльщика human-merge (один миксин)."""
    from governance import brief_merge_check, policy_rule

    _test_config(monkeypatch, tmp_path)
    _no_gh(monkeypatch)
    ops = RealOps() if facts == "real_ops" else brief_merge_check.GhFacts()
    policy = policy_rule.test_policy()
    assert policy is not None
    repo, ref, path = policy_rule.TEST_POLICY_SOURCE
    version = ops.policy_version_fact(repo, ref, path)
    assert (version.outcome, version.value) == (Outcome.FOUND, policy.sha)
    at = ops.policy_version_fact_at(repo, ref, path, policy.sha)
    assert (at.outcome, at.value) == (Outcome.FOUND, True)
    content = ops.repo_file_fact(repo, policy.sha, path)
    assert (content.outcome, content.value) == (Outcome.FOUND, policy.content)


def test_brief_facts_test_coords_other_sha_absent(monkeypatch, tmp_path) -> None:
    """Истории нет: другой sha — не версия и без состава."""
    from governance import policy_rule

    _test_config(monkeypatch, tmp_path)
    _no_gh(monkeypatch)
    repo, ref, path = policy_rule.TEST_POLICY_SOURCE
    at = RealOps().policy_version_fact_at(repo, ref, path, SHA)
    assert (at.outcome, at.value) == (Outcome.FOUND, False)
    assert RealOps().repo_file_fact(repo, SHA, path).outcome is Outcome.ABSENT


def test_brief_facts_test_coords_outside_test_mode_absent(monkeypatch, tmp_path):
    """Координаты test при strict-конфиге — синтетической политики нет, без `gh`."""
    from governance import policy_rule

    _test_config(monkeypatch, tmp_path, mode="strict")
    _no_gh(monkeypatch)
    repo, ref, path = policy_rule.TEST_POLICY_SOURCE
    assert RealOps().policy_version_fact(repo, ref, path).outcome is Outcome.ABSENT
    at = RealOps().policy_version_fact_at(repo, ref, path, SHA)
    assert (at.outcome, at.value) == (Outcome.FOUND, False)


def test_brief_facts_other_coords_still_call_gh(monkeypatch, tmp_path) -> None:
    """Прочие координаты в test — как сегодня, через `gh`."""
    _test_config(monkeypatch, tmp_path)
    calls = _gh(monkeypatch, [(0, {"data": {"repository": {"ref": None}}})])
    fact = RealOps().policy_version_fact("o/policy", "main", "p.env")
    assert fact.outcome is Outcome.ABSENT and calls
