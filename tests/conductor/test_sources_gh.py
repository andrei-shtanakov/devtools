import json
import subprocess

import pytest

import conductor.sources_gh as sources_gh
from conductor.sources_gh import (
    ISSUE_FIELDS,
    ci_state,
    collect_gh,
    discover,
    fetch_record,
)


def fake(responses: dict[str, tuple[int, str, str]]):
    # Поиск отклонённых заявок (#511) — пустой, если тест не задал свой.
    responses = {DECLINED_Q: (0, json.dumps([_page([])]), ""), **responses}

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
DECLINED_Q = f"api -X GET search/issues -f q=user:own {sources_gh.DECLINED}"
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


def test_pages_are_deduped_and_dedup_counts_against_total() -> None:
    """#511: элемент, съехавший между страницами, не считается дважды —
    ни в находках, ни в сверке с total_count."""
    shifted = [_page([("a", 1, False), ("a", 2, False)]), _page([("a", 2, False)], 2)]
    hits, state, _ = discover("own", {"a"}, fake({OPEN: (0, json.dumps(shifted), "")}))
    assert state == "read" and hits == [("a", 1, False), ("a", 2, False)]
    dup = [_page([("a", 1, False)], total=2), _page([("a", 1, False)], total=2)]
    assert discover("own", {"a"}, fake({OPEN: (0, json.dumps(dup), "")}))[1] == (
        "error"
    )


def test_total_count_changed_between_pages_is_error() -> None:
    """#511: total_count сверяется по каждой странице, не только по первой."""
    pages = [_page([("a", 1, False)], total=1), _page([("a", 2, False)], total=2)]
    hits, state, detail = discover(
        "own", {"a"}, fake({OPEN: (0, json.dumps(pages), "")})
    )
    assert (hits, state) == ([], "error") and "total_count" in detail


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


# ревью рубежа 1 (2026-09-30)

BASE = {
    OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
    "pr view 2 -R own/a": (0, PR, ""),
    "api graphql": (0, PR_EXTRA, ""),
    "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
}


def test_one_failed_ref_does_not_drop_the_others() -> None:
    run = fake(
        {
            **BASE,
            "issue view 3 -R own/a": (0, ISSUE, ""),
            "api --paginate --slurp repos/own/a/issues/3/comments": (0, COMMENTS, ""),
        }
    )
    result = collect_gh("own", {"a": "a"}, lambda _: {("a", 1), ("a", 3)}, run)
    assert result.state == "error" and "a#1" in result.detail
    assert {r["number"] for r in result.records} == {2, 3}


def test_issue_view_of_a_pr_number_is_read_as_the_pr() -> None:
    as_issue = json.loads(ISSUE) | {"state": "MERGED", "url": "https://x/pull/5"}
    run = fake(
        {
            **BASE,
            "issue view 5 -R own/a": (0, json.dumps(as_issue), ""),
            "pr view 5 -R own/a": (0, PR, ""),
            "api --paginate --slurp repos/own/a/issues/5/comments": (0, COMMENTS, ""),
        }
    )
    result = collect_gh("own", {"a": "a"}, lambda _: {("a", 5)}, run)
    five = next(r for r in result.records if r["number"] == 5)
    assert result.state == "read" and five["is_pr"] is True


def test_malformed_json_is_a_read_failure_not_a_crash() -> None:
    run = fake({**BASE, "issue view 1 -R own/a": (0, "{not json", "")})
    result = collect_gh("own", {"a": "a"}, lambda _: {("a", 1)}, run)
    assert result.state == "error" and "a#1" in result.detail
    broken = fake({**BASE, "api graphql": (0, '{"data": {}}', "")})
    assert collect_gh("own", {"a": "a"}, lambda _: set(), broken).state == "error"


def test_run_gh_has_no_tty_and_no_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """#511: gh не наследует TTY и не может зависнуть на интерактивном вопросе."""
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setattr(sources_gh.subprocess, "run", fake_run)
    sources_gh.run_gh(["api", "user"])
    env = seen["env"]
    assert seen["stdin"] is subprocess.DEVNULL
    assert isinstance(env, dict) and env["GH_PROMPT_DISABLED"] == "1"
    assert "GIT_DIR" not in env


def test_issue_record_carries_created_at() -> None:
    # #511: возраст ожидания по from: — от создания запроса
    raw = json.dumps({**json.loads(ISSUE), "createdAt": "2026-09-20T00:00:00Z"})
    run = fake(
        {
            "issue view 1 -R own/a --json": (0, raw, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    assert "createdAt" in ISSUE_FIELDS.split(",")
    rec = fetch_record("own", "a", 1, False, run)
    assert rec is not None and rec["created_at"] == "2026-09-20T00:00:00Z"


def test_declined_inbox_request_is_discovered_and_read() -> None:
    """Терм. ревью #550 (major): отклонённая заявка не находится ни поиском
    открытых, ни адресным дочитыванием (на неё ссылается `todo://`, а не
    номер) — без своего поиска `declined_requests` в бою пуст, и ожидание
    остаётся «предпосылки нет»."""
    declined = json.dumps(
        {
            **json.loads(ISSUE),
            "body": "slug: y\nfrom: a",
            "state": "CLOSED",
            "stateReason": "NOT_PLANNED",
        }
    )
    run = fake(
        {
            OPEN: (0, json.dumps([_page([])]), ""),
            DECLINED_Q: (0, json.dumps([_page([("b", 5, False)])]), ""),
            "issue view 5 -R own/b --json": (0, declined, ""),
            "api --paginate --slurp repos/own/b/issues/5/comments": (0, COMMENTS, ""),
        }
    )
    result = collect_gh("own", {"b": "b"}, lambda _: set(), run)
    assert result.state == "read"
    [rec] = result.records
    assert (rec["number"], rec["state"], rec["state_reason"]) == (
        5,
        "closed",
        "not_planned",
    )


def test_declined_search_failure_is_error() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([])]), ""),
            DECLINED_Q: (1, "", "rate limited"),
        }
    )
    assert discover("own", {"a"}, run)[1] == "error"


def test_app_author_is_normalised_to_bot_login() -> None:
    """Issue/PR, созданный GitHub App, приходит из `gh ... --json author`
    в форме `app/<slug>` — отлично от REST/`GET /app` формы `<slug>[bot]`,
    с которой сравнивается bot login везде в conductor (owner_queue#131:
    очередь владельца от App не распознавалась «своей»)."""
    raw = json.dumps({**json.loads(ISSUE), "author": {"login": "app/conductorsandbox"}})
    run = fake(
        {
            "issue view 1 -R own/a --json": (0, raw, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    rec = fetch_record("own", "a", 1, False, run)
    assert rec is not None and rec["author"] == "conductorsandbox[bot]"


def test_human_author_login_is_unchanged() -> None:
    raw = json.dumps({**json.loads(ISSUE), "author": {"login": "andrei-shtanakov"}})
    run = fake(
        {
            "issue view 1 -R own/a --json": (0, raw, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    rec = fetch_record("own", "a", 1, False, run)
    assert rec is not None and rec["author"] == "andrei-shtanakov"


def test_already_rest_form_bot_login_is_unchanged() -> None:
    """Логин, уже отданный в форме `<slug>[bot]` (REST/GraphQL), нормализация
    не трогает — идемпотентность на случай, если gh когда-нибудь это исправит."""
    raw = json.dumps(
        {**json.loads(ISSUE), "author": {"login": "conductorsandbox[bot]"}}
    )
    run = fake(
        {
            "issue view 1 -R own/a --json": (0, raw, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    rec = fetch_record("own", "a", 1, False, run)
    assert rec is not None and rec["author"] == "conductorsandbox[bot]"
