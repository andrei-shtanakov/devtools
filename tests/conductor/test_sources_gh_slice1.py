"""Дочитывания GitHub для среза 1 (спека среза 1, §6.1, §7.3–7.5)."""

import json

from conductor.sources_gh import (
    closed_event,
    fetch_record,
    issue_extras,
    queue_records,
    red_checks,
)


def fake(responses: dict[str, list[tuple[int, str, str]]]):
    """Ответы по префиксу команды; список расходуется по порядку."""
    calls: list[str] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        calls.append(key)
        for prefix, answers in responses.items():
            if key.startswith(prefix):
                return answers.pop(0) if len(answers) > 1 else answers[0]
        return 1, "", f"unexpected: {key}"

    run.calls = calls  # type: ignore[attr-defined]
    return run


ROLLUP = [
    {
        "__typename": "CheckRun",
        "name": "test",
        "conclusion": "FAILURE",
        "detailsUrl": "https://ci/1",
    },
    {
        "__typename": "StatusContext",
        "context": "lint",
        "state": "ERROR",
        "targetUrl": "https://ci/2",
    },
    {
        "__typename": "CheckRun",
        "name": "ok",
        "conclusion": "SUCCESS",
        "detailsUrl": "https://ci/3",
    },
]
PR_VIEW = json.dumps(
    {
        "title": "p",
        "body": "",
        "state": "OPEN",
        "mergedAt": None,
        "author": {"login": "u"},
        "labels": [],
        "updatedAt": "2026-09-30T00:00:00Z",
        "url": "https://x/pull/7",
        "closingIssuesReferences": [],
        "headRefOid": "abc",
        "reviewDecision": None,
        "statusCheckRollup": ROLLUP,
        "mergeCommit": None,
        "isDraft": False,
        "createdAt": "2026-09-20T00:00:00Z",
    }
)
PR_EXTRA = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": "abc",
                    "latestReviews": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "state": "COMMENTED",
                                "submittedAt": "2026-09-25T00:00:00Z",
                                "author": {"login": "rev"},
                                "commit": {"oid": "abc"},
                            }
                        ],
                    },
                    "files": {"totalCount": 0, "nodes": []},
                    "commits": {
                        "nodes": [{"commit": {"committedDate": "2026-09-24T00:00:00Z"}}]
                    },
                    "timelineItems": {"nodes": [{"createdAt": "2026-09-22T00:00:00Z"}]},
                }
            }
        }
    }
)
COMMENTS = json.dumps(
    [
        [
            {
                "id": 5,
                "user": {"login": "u"},
                "body": "c",
                "created_at": "t1",
                "updated_at": "t2",
            }
        ]
    ]
)


def test_red_checks_names_and_links() -> None:
    assert red_checks(ROLLUP) == [
        {"name": "test", "url": "https://ci/1"},
        {"name": "lint", "url": "https://ci/2"},
    ]
    assert red_checks(None) == []


def test_pr_record_carries_activity_and_ids() -> None:
    run = fake(
        {
            "pr view 7": [(0, PR_VIEW, "")],
            "api --paginate --slurp repos/own/a/issues/7/comments": [(0, COMMENTS, "")],
            "api graphql": [(0, PR_EXTRA, "")],
        }
    )
    rec = fetch_record("own", "a", 7, True, run)
    assert rec is not None
    assert rec["comments"] == [
        {"id": 5, "author": "u", "body": "c", "created_at": "t1", "updated_at": "t2"}
    ]
    assert rec["is_draft"] is False and rec["created_at"] == "2026-09-20T00:00:00Z"
    assert rec["ready_at"] == "2026-09-22T00:00:00Z"
    assert rec["last_commit_at"] == "2026-09-24T00:00:00Z"
    assert rec["reviews"] == [{"author": "rev", "submitted_at": "2026-09-25T00:00:00Z"}]
    assert [c["name"] for c in rec["red_checks"]] == ["test", "lint"]
    assert rec["merge_sha"] is None


def _extras_page(numbers: list[int], has_next: bool, total: int = 1) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "defaultBranchRef": {"name": "master"},
                    "issues": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": "CUR"},
                        "nodes": [
                            {
                                "number": n,
                                "createdAt": "2026-09-01T00:00:00Z",
                                "closedByPullRequestsReferences": {
                                    "totalCount": total,
                                    "nodes": [
                                        {
                                            "number": 50 + n,
                                            "merged": True,
                                            "mergedAt": "2026-09-10T00:00:00Z",
                                            "mergeCommit": {"oid": f"m{n}"},
                                            "baseRefName": "master",
                                            "baseRepository": {
                                                "defaultBranchRef": {"name": "master"}
                                            },
                                            "repository": {"name": "a"},
                                        }
                                    ],
                                },
                                "timelineItems": {
                                    "nodes": [
                                        {
                                            "id": "RE_1",
                                            "createdAt": "2026-09-15T00:00:00Z",
                                        }
                                    ]
                                    if n == 2
                                    else []
                                },
                            }
                            for n in numbers
                        ],
                    },
                }
            }
        }
    )


def test_issue_extras_pages_and_periods() -> None:
    run = fake(
        {
            "api graphql -f query=query($o:String!,$n:String!,$c:String)": [
                (0, _extras_page([1], True), ""),
                (0, _extras_page([2], False), ""),
            ]
        }
    )
    extras = issue_extras("own", "a", run)
    assert extras is not None and set(extras) == {1, 2}
    assert extras[1]["period"] == "0"
    assert extras[1]["period_start"] == "2026-09-01T00:00:00Z"
    assert extras[2]["period"] == "RE_1"
    assert extras[2]["period_start"] == "2026-09-15T00:00:00Z"
    assert extras[1]["closed_by"][0] == {
        "repo": "a",
        "number": 51,
        "merged": True,
        "merged_at": "2026-09-10T00:00:00Z",
        "merge_sha": "m1",
        "base_is_default": True,
    }
    assert any("c=CUR" in c for c in run.calls)  # type: ignore[attr-defined]


def test_issue_extras_truncated_is_unread() -> None:
    run = fake(
        {
            "api graphql -f query=query($o:String!,$n:String!,$c:String)": [
                (0, _extras_page([1], False, total=21), "")
            ]
        }
    )
    assert issue_extras("own", "a", run) is None


ISSUE_VIEW = json.dumps(
    {
        "title": "Очередь",
        "body": "b",
        "state": "CLOSED",
        "stateReason": "COMPLETED",
        "author": {"login": "conductor[bot]"},
        "labels": [{"name": "owner-queue"}],
        "updatedAt": "u",
        "url": "https://x/issues/3",
        "closedAt": "2026-09-30T00:00:00Z",
    }
)


def test_queue_records_any_state_with_pinned() -> None:
    search = json.dumps(
        [{"total_count": 1, "incomplete_results": False, "items": [{"number": 3}]}]
    )
    pinned = json.dumps({"data": {"repository": {"issue": {"isPinned": True}}}})
    run = fake(
        {
            "api -X GET search/issues -f q=user:own repo:own/ws label:owner-queue": [
                (0, search, "")
            ],
            "issue view 3 -R own/ws": [(0, ISSUE_VIEW, "")],
            "api --paginate --slurp repos/own/ws/issues/3/comments": [(0, "[[]]", "")],
            "api graphql": [(0, pinned, "")],
        }
    )
    [rec] = queue_records("own", "ws", run) or [None]
    assert rec is not None and rec["pinned"] is True and rec["repo_full"] == "own/ws"
    assert rec["state"] == "closed" and rec["closed_at"] == "2026-09-30T00:00:00Z"


def test_closed_event_is_last_closed_node_id() -> None:
    """fact_id закрытия — id события closed (решение владельца 2), не closedAt."""
    timeline = json.dumps(
        [
            [
                {"event": "closed", "node_id": "CE_1"},
                {"event": "reopened", "node_id": "RE_1"},
                {"event": "closed", "node_id": "CE_2"},
            ]
        ]
    )
    path = "api --paginate --slurp repos/own/a/issues/7/timeline?per_page=100"
    assert closed_event("own", "a", 7, fake({path: [(0, timeline, "")]})) == "CE_2"
    assert closed_event("own", "a", 7, fake({path: [(0, "[[]]", "")]})) == ""
    assert closed_event("own", "a", 7, fake({path: [(1, "", "boom")]})) is None
