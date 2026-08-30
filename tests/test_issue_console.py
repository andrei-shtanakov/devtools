from pathlib import Path
from types import SimpleNamespace

import issue_console


def _raw(
    repo: str = "alpha",
    body: str = "slug: slug-one",
    labels: tuple[str, ...] = ("inbox",),
    author: str = "owner",
) -> dict:
    return {
        "repository": {"name": repo, "nameWithOwner": f"owner/{repo}"},
        "number": 7,
        "title": "Research queue",
        "body": body,
        "author": {"login": author},
        "createdAt": "2026-08-30T10:00:00Z",
        "updatedAt": "2026-08-30T11:00:00Z",
        "url": "https://example/7",
        "labels": [{"name": x} for x in labels],
    }


def _fleet(tmp_path: Path, todo: str | None = "- [ ] do thing slug-one\n") -> Path:
    repo = tmp_path / "alpha"
    (repo / ".git").mkdir(parents=True)
    if todo is not None:
        (repo / "TODO.md").write_text(todo)
    return tmp_path


def _issue(
    repo: str = "alpha",
    number: int = 1,
    author: str = "owner",
    created: str = "2026-08-30T10:00:00Z",
) -> issue_console.Issue:
    return issue_console.Issue(
        repo=repo,
        number=number,
        title="t",
        body="",
        author=author,
        created_at=created,
        url="",
        labels=(),
        inbox=False,
        accepted="n/a",
        kind="unknown",
        internal=True,
        owner="owner",
        updated_at="2026-08-30T11:00:00Z",
    )


def test_classify_is_conservative() -> None:
    assert issue_console.classify("Fix parser regression", "", ()) == "fix"
    assert issue_console.classify("Explore model choices", "research", ()) == "research"
    assert issue_console.classify("Fix docs typo", "", ()) == "unknown"
    assert issue_console.classify("Unclear request", "", ()) == "unknown"


def test_classify_covers_each_kind() -> None:
    assert issue_console.classify("Update README documentation", "", ()) == "document"
    assert issue_console.classify("Implement feature support", "", ()) == "code"


def test_acceptance_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "accepted"
    assert issue.internal and issue.inbox


def test_acceptance_not_accepted(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo="- [ ] unrelated item\n")
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"


def test_acceptance_unverifiable_without_slug(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues(
        [_raw(body="просто текст")], root, {"owner"}
    )[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_unverifiable_without_todo(tmp_path: Path) -> None:
    root = _fleet(tmp_path, todo=None)
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "unverifiable"


def test_acceptance_na_for_non_inbox(tmp_path: Path) -> None:
    root = _fleet(tmp_path)
    issue = issue_console.parse_issues(
        [_raw(labels=("research",))], root, {"owner"}
    )[0]
    assert issue.accepted == "n/a"


def test_acceptance_ignores_slug_in_prose(tmp_path: Path) -> None:
    root = _fleet(
        tmp_path, todo="в прозе упомянут slug-one, но пункта нет\n"
    )
    issue = issue_console.parse_issues([_raw()], root, {"owner"})[0]
    assert issue.accepted == "not-accepted"


def test_fetch_uses_gh_search_issues_compatible_flags(monkeypatch) -> None:
    captured = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    assert issue_console.fetch_issues("owner") == []
    assert captured[:3] == ["gh", "search", "issues"]
    assert "--include-prs" not in captured
    assert "--type" not in captured


def test_fetch_warns_when_search_limit_reached(monkeypatch, capsys) -> None:
    import json as _json

    full_page = _json.dumps([{"number": i} for i in range(1000)])

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=full_page, stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    data = issue_console.fetch_issues("owner")
    assert len(data) == 1000
    err = capsys.readouterr().err
    assert "потолок" in err and "неполным" in err


def test_fetch_no_warning_below_limit(monkeypatch, capsys) -> None:
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="[{\"number\": 1}]", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    assert len(issue_console.fetch_issues("owner")) == 1
    assert capsys.readouterr().err == ""


def test_fleet_filter_drops_repos_without_clone(tmp_path: Path) -> None:
    root = _fleet(tmp_path)  # clone exists only for alpha
    raw = [_raw(), {**_raw(repo="ghost"), "repository": {
        "name": "ghost", "nameWithOwner": "owner/ghost"}}]
    issues = issue_console.parse_issues(raw, root, {"owner"})
    assert [x.repo for x in issues] == ["alpha"]


def test_sort_issues_newest_first() -> None:
    older = _issue(number=1, created="2026-08-01T00:00:00Z")
    newer = _issue(number=2, created="2026-08-29T00:00:00Z")
    assert issue_console.sort_issues([older, newer], mode="date") == [
        newer,
        older,
    ]


def test_sort_issues_grouped_by_author_then_newest() -> None:
    a_old = _issue(number=1, author="bob", created="2026-08-01T00:00:00Z")
    a_new = _issue(number=2, author="bob", created="2026-08-29T00:00:00Z")
    b_new = _issue(number=3, author="alice", created="2026-08-28T00:00:00Z")
    assert issue_console.sort_issues([a_old, b_new, a_new], mode="author") == [
        b_new,
        a_new,
        a_old,
    ]


def test_sort_issues_grouped_by_repo_then_newest() -> None:
    a_old = _issue(number=1, repo="alpha", created="2026-08-01T00:00:00Z")
    a_new = _issue(number=2, repo="alpha", created="2026-08-29T00:00:00Z")
    b_new = _issue(number=3, repo="beta", created="2026-08-28T00:00:00Z")
    assert issue_console.sort_issues([a_old, b_new, a_new], mode="repo") == [
        a_new,
        a_old,
        b_new,
    ]


def test_group_key() -> None:
    issue = _issue(author="bob", repo="alpha")
    assert issue_console.group_key(issue, mode="author") == "bob"
    assert issue_console.group_key(issue, mode="repo") == "alpha"
    assert issue_console.group_key(issue, mode="date") == ""


def test_acceptance_char_covers_all_acceptance_values() -> None:
    assert set(issue_console.ACCEPTANCE_CHAR) == set(issue_console.ACCEPTANCE)


def test_internal_default_set() -> None:
    assert issue_console.resolve_internal([]) == {"andrei-shtanakov", "ai-prosto"}


def test_internal_flag_replaces_default() -> None:
    assert issue_console.resolve_internal(["Alice", "bob"]) == {"alice", "bob"}


def test_apply_kinds_replaces_only_listed() -> None:
    a, b = _issue(number=1), _issue(number=2)
    updated = issue_console.apply_kinds([a, b], {"alpha#1": "fix"})
    assert [x.kind for x in updated] == ["fix", "unknown"]
    assert updated[1] is b


def test_launch_skips_existing_tmux_session(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"неожиданная команда: {cmd}")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    status = issue_console.launch(_issue(repo="alpha", number=7), root, "plan")
    assert status == "exists: tmux attach -t =issue-alpha-7"
    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=issue-alpha-7"
    assert not any(c[:2] == ["tmux", "new-session"] for c in calls)


def test_launch_uses_exact_tmux_target_not_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    """has-session без точного совпадения не должен молчаливо блокировать

    запуск, даже когда существует сессия с именем-надмножеством (например,
    issue-alpha-67 при попытке запустить issue-alpha-6).
    """
    root = _fleet(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    status = issue_console.launch(_issue(repo="alpha", number=6), root, "plan")
    assert status == "started issue-alpha-6"
    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=issue-alpha-6"
    assert any(c[:2] == ["tmux", "new-session"] for c in calls)


def test_launch_passes_output_root(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured["shell"] = cmd[-1]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(issue_console.subprocess, "run", fake_run)
    status = issue_console.launch(_issue(repo="alpha", number=7), root, "plan")
    assert status == "started issue-alpha-7"
    assert "--output-root" in captured["shell"]
    assert str(issue_console.OUT_ROOT) in captured["shell"]
    assert "--owner" in captured["shell"]


def test_resolve_clone_prefers_full_slug_and_blocks_forks(tmp_path: Path) -> None:
    fork = tmp_path / "foo"
    repos = {"fork-owner/foo": fork}
    assert issue_console.resolve_clone(repos, "upstream-owner", "foo") is None
    assert issue_console.resolve_clone(repos, "fork-owner", "foo") == fork
    bare = {"alpha": tmp_path / "alpha"}
    assert issue_console.resolve_clone(bare, "owner", "alpha") == bare["alpha"]


def test_discover_repos_keys_by_owner_slug(tmp_path: Path) -> None:
    import subprocess as sp

    clone = tmp_path / "foo"
    clone.mkdir()
    sp.run(["git", "init", "-q", str(clone)], check=True)
    sp.run(
        ["git", "-C", str(clone), "remote", "add", "origin",
         "git@github.com:Fork-Owner/Foo.git"],
        check=True,
    )
    repos = issue_console.discover_repos(tmp_path)
    assert repos == {"fork-owner/foo": clone}


def test_fleet_filter_blocks_same_name_fork(tmp_path: Path, monkeypatch) -> None:
    _fleet(tmp_path)
    real_discover = issue_console.discover_repos

    def fork_only(root):
        return {"fork-owner/alpha": tmp_path / "alpha"}

    monkeypatch.setattr(issue_console, "discover_repos", fork_only)
    try:
        issues = issue_console.parse_issues([_raw()], tmp_path, {"owner"})
    finally:
        monkeypatch.setattr(issue_console, "discover_repos", real_discover)
    assert issues == []


def test_classify_ai_flag_wires_refine(tmp_path: Path, monkeypatch) -> None:
    root = _fleet(tmp_path)
    raw = [_raw(body="просто текст", labels=("misc",))]
    called = {}

    def fake_refine(issues, cache_path, run=None):
        called["keys"] = [x.key for x in issues]
        called["cache"] = cache_path
        return {"alpha#7": "research"}

    monkeypatch.setattr(issue_console.issue_classify, "refine", fake_refine)
    import json as _json
    fixture = tmp_path / "issues.json"
    fixture.write_text(_json.dumps(raw))
    monkeypatch.setattr(
        "sys.argv",
        ["issue_console.py", "--root", str(root), "--input", str(fixture),
         "--json", "--classify-ai"])
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert issue_console.main() == 0
    data = _json.loads(out.getvalue())
    assert data[0]["kind"] == "research"
    assert called["cache"] == issue_console.OUT_ROOT / "issue-kind-cache.json"


def test_json_offline_without_classify_ai_skips_refine(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fleet(tmp_path)
    raw = [_raw(labels=("research",))]  # non-inbox: no plan-fields needed

    def fail_refine(*args, **kwargs):
        raise AssertionError("refine must not run without --classify-ai")

    monkeypatch.setattr(issue_console.issue_classify, "refine", fail_refine)
    import json as _json
    fixture = tmp_path / "issues.json"
    fixture.write_text(_json.dumps(raw))
    monkeypatch.setattr(
        "sys.argv",
        ["issue_console.py", "--root", str(root), "--input", str(fixture),
         "--json"])
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert issue_console.main() == 0
    data = _json.loads(out.getvalue())
    assert data[0]["repo"] == "alpha"
    assert data[0]["accepted"] == "n/a"


def test_main_returns_2_when_plan_fields_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fleet(tmp_path)  # inbox issue by default (see _raw())
    raw = [_raw()]
    monkeypatch.setattr(issue_console, "scrape_items", None)
    import json as _json
    fixture = tmp_path / "issues.json"
    fixture.write_text(_json.dumps(raw))
    monkeypatch.setattr(
        "sys.argv",
        ["issue_console.py", "--root", str(root), "--input", str(fixture),
         "--json"])
    assert issue_console.main() == 2
