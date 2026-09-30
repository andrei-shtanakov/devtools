import json

from conductor.sources_gh import ci_state, collect_gh, discover


def fake(responses: dict[str, tuple[int, str, str]]):
    def run(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        for prefix, answer in responses.items():
            if key.startswith(prefix):
                return answer
        return 1, "", f"unexpected: {key}"

    return run


def _page(
    items: list[tuple[str, int, bool]],
    total: int | None = None,
    incomplete: bool = False,
) -> dict:
    return {
        "total_count": len(items) if total is None else total,
        "incomplete_results": incomplete,
        "items": [
            {
                "repository_url": f"https://api.github.com/repos/own/{n}",
                "number": k,
                **({"pull_request": {}} if p else {}),
            }
            for n, k, p in items
        ],
    }


OPEN = "api -X GET search/issues -f q=user:own is:open"
ISSUE = json.dumps(
    {
        "title": "t",
        "body": "b",
        "state": "OPEN",
        "stateReason": None,
        "author": {"login": "u"},
        "labels": [{"name": "inbox"}],
        "updatedAt": "2026-09-28T00:00:00Z",
        "url": "https://x/1",
    }
)
PR = json.dumps(
    {
        "title": "p",
        "body": "",
        "state": "OPEN",
        "mergedAt": None,
        "author": {"login": "u"},
        "labels": [],
        "updatedAt": "",
        "url": "",
        "closingIssuesReferences": [{"number": 1, "repository": {"name": "a"}}],
        "headRefOid": "abc",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"__typename": "CheckRun", "conclusion": "SUCCESS"}],
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
                        "nodes": [{"state": "APPROVED", "commit": {"oid": "old"}}],
                    },
                    "files": {"totalCount": 1, "nodes": [{"path": ".github/x.yml"}]},
                }
            }
        }
    }
)
COMMENTS = json.dumps(
    [[{"user": {"login": "u"}, "body": "c", "created_at": "2026-09-28T00:00:00Z"}]]
)


def test_offline_is_error() -> None:
    result = collect_gh(
        "own", {}, lambda _: set(), fake({OPEN: (1, "", "not logged in")})
    )
    assert result.state == "error" and "not logged in" in result.detail


def test_incomplete_results_is_error() -> None:
    run = fake({OPEN: (0, json.dumps([_page([("a", 1, False)], incomplete=True)]), "")})
    assert discover("own", {"a"}, run)[1] == "error"


def test_total_count_above_received_is_error() -> None:
    run = fake({OPEN: (0, json.dumps([_page([("a", 1, False)], total=5)]), "")})
    assert discover("own", {"a"}, run)[1] == "error"


def test_non_fleet_repos_are_ignored() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 1, False), ("zzz", 2, False)])]), ""),
        }
    )
    hits, state, _ = discover("own", {"a"}, run)
    assert state == "read" and hits == [("a", 1, False)]


def test_collects_pr_ci_and_follows_refs() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
            "pr view 2 -R own/a": (0, PR, ""),
            "api graphql": (0, PR_EXTRA, ""),
            "issue view 1 -R own/a": (0, ISSUE, ""),
            "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    result = collect_gh("own", {"a": "a"}, lambda _: {("a", 1)}, run)
    assert result.state == "read"
    by_number = {r["number"]: r for r in result.records}
    assert by_number[2]["ci"] == "green" and by_number[2]["head_sha"] == "abc"
    assert by_number[2]["approved_at_head"] is False  # одобрен старый SHA
    assert by_number[2]["files"] == [".github/x.yml"] and by_number[2]["complete"]
    assert by_number[2]["closing_refs"] == ["a#1"]
    assert by_number[1]["labels"] == ["inbox"] and by_number[1]["repo"] == "a"


def test_head_moved_between_reads_is_error() -> None:
    moved = PR_EXTRA.replace('"headRefOid": "abc"', '"headRefOid": "new"')
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
            "pr view 2 -R own/a": (0, PR, ""),
            "api graphql": (0, moved, ""),
            "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
        }
    )
    assert collect_gh("own", {"a": "a"}, lambda _: set(), run).state == "error"


def test_ci_state_table() -> None:
    assert ci_state(None) == "unknown"
    assert ci_state([{"__typename": "CheckRun", "conclusion": "FAILURE"}]) == "red"
    assert ci_state([{"__typename": "StatusContext", "state": "PENDING"}]) == "pending"
    assert (
        ci_state(
            [
                {"__typename": "CheckRun", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "state": "SUCCESS"},
            ]
        )
        == "green"
    )


def test_weak_refs_are_best_effort_and_never_chained() -> None:
    responses = {
        OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
        "pr view 2 -R own/a": (0, PR, ""),
        "api graphql": (0, PR_EXTRA, ""),
        "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
    }
    lost = collect_gh(
        "own",
        {"a": "a"},
        lambda _: set(),
        fake(responses),
        weak_refs=lambda _: {("a", 9)},
    )
    # голый #9, которого нет, — не сбой источника (питает только подсказки)
    assert lost.state == "read" and "слабых ссылок не дочитано: 1" in lost.detail
    responses.update(
        {
            "issue view 1 -R own/a": (0, ISSUE, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    found = collect_gh(
        "own",
        {"a": "a"},
        lambda _: set(),
        fake(responses),
        weak_refs=lambda _: {("a", 1)},
    )
    assert found.state == "read" and {r["number"] for r in found.records} == {1, 2}


def test_strong_requirement_wins_when_ref_is_also_weak() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
            "pr view 2 -R own/a": (0, PR, ""),
            "api graphql": (0, PR_EXTRA, ""),
            "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
        }
    )
    both = collect_gh(
        "own", {"a": "a"}, lambda _: {("a", 1)}, run, weak_refs=lambda _: {("a", 1)}
    )
    # недочитанная строгая ссылка — сбой источника, слабая её не прикрывает
    assert both.state == "error" and "не дочитан a#1" in both.detail
